"""Functional checks for ERI STORE IPA analyzer, ZIP decrypt, and signer safeguards."""
import io
import os
import plistlib
import shutil
import struct
import subprocess
import tempfile
import zipfile
from pathlib import Path

with tempfile.TemporaryDirectory(prefix="eri-tools-test-") as root:
    os.environ["ERI_DB_PATH"] = str(Path(root) / "test.db")
    os.environ["ERI_UPLOAD_DIR"] = str(Path(root) / "private")
    os.environ["ERI_ADMIN_KEY"] = "ci-very-long-admin-secret-0123456789"
    from fastapi.testclient import TestClient
    from app import app
    client = TestClient(app)

    registration = client.post("/api/v1/auth/register", json={
        "username": "toolstester", "password": "tools-password-2026",
        "display_name": "IPA Tester", "contact": "test@example.invalid"
    })
    assert registration.status_code == 201, registration.text
    token = registration.json()["token"]
    auth = {"Authorization": "Bearer " + token}

    executable = struct.pack("<IiiIIIII", 0xfeedfacf, 0x100000c, 0, 2, 1, 24, 0, 0)
    encrypted = executable + struct.pack("<IIIIII", 0x2c, 24, 0, 0, 1, 0)
    plist = plistlib.dumps({
        "CFBundleIdentifier": "vn.eri.tool.test",
        "CFBundleExecutable": "Sample",
        "CFBundleShortVersionString": "1.0",
        "CFBundleDisplayName": "Sample"
    })
    def make_ipa(binary):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as zip_file:
            zip_file.writestr("Payload/Sample.app/Info.plist", plist)
            zip_file.writestr("Payload/Sample.app/Sample", binary)
        return data.getvalue()

    filedata = make_ipa(encrypted)
    files = {"ipa": ("Sample.ipa", filedata, "application/octet-stream")}
    assert client.post("/api/v1/tools/ipa/analyze", files=files).status_code == 401
    status = client.get("/api/v1/tools/ipa/status")
    assert status.status_code == 200
    assert status.json()["fairplay_decrypt_available"] is False
    analyze = client.post("/api/v1/tools/ipa/analyze", files=files, headers=auth)
    assert analyze.status_code == 200, analyze.text
    assert analyze.json()["fairplay_encrypted"] is True
    assert analyze.json()["zip_password_protected"] is False
    assert analyze.json()["app"]["bundle_id"] == "vn.eri.tool.test"

    not_decrypted = client.post("/api/v1/tools/ipa/decrypt",
                                files=files, data={"password": "incorrect"}, headers=auth)
    assert not_decrypted.status_code == 422

    signed = client.post("/api/v1/tools/ipa/sign", files={
        "ipa": ("Sample.ipa", filedata, "application/octet-stream"),
        "p12": ("cert.p12", b"not-a-certificate", "application/octet-stream"),
        "mobileprovision": ("example.mobileprovision", b"not-a-profile", "application/octet-stream"),
    }, data={"password": "private"}, headers=auth)
    assert signed.status_code in (422, 503), signed.text
    assert "private" not in signed.text

    if shutil.which("zip"):
        folder = Path(root) / "zip-source"
        (folder / "Payload" / "Sample.app").mkdir(parents=True)
        (folder / "Payload" / "Sample.app" / "Info.plist").write_bytes(plist)
        (folder / "Payload" / "Sample.app" / "Sample").write_bytes(
            executable + struct.pack("<IIIIII", 0x2c, 24, 0, 0, 0, 0))
        archive = Path(root) / "zip-locked.ipa"
        subprocess.run(["zip", "-qr", "-P", "testzip123", str(archive), "Payload"],
                       cwd=folder, check=True, capture_output=True)
        locked = archive.read_bytes()
        check = client.post("/api/v1/tools/ipa/analyze", headers=auth, files={
            "ipa": ("locked.ipa", locked, "application/octet-stream")})
        assert check.status_code == 200, check.text
        assert check.json()["zip_password_protected"] is True
        wrong = client.post("/api/v1/tools/ipa/decrypt", headers=auth,
                            files={"ipa": ("locked.ipa", locked, "application/octet-stream")},
                            data={"password": "wrong"})
        assert wrong.status_code == 422, wrong.text
        decrypted = client.post("/api/v1/tools/ipa/decrypt", headers=auth,
                                files={"ipa": ("locked.ipa", locked, "application/octet-stream")},
                                data={"password": "testzip123"})
        assert decrypted.status_code == 200, decrypted.text
        download = decrypted.json()["download_path"]
        returned = client.get(download)
        assert returned.status_code == 200, returned.text
        with zipfile.ZipFile(io.BytesIO(returned.content)) as zip_file:
            assert not any(x.flag_bits & 1 for x in zip_file.infolist())
            assert zip_file.read("Payload/Sample.app/Info.plist") == plist
        assert client.get(download).status_code == 404


    # Realistic framework symlinks are valid IPA entries, but links escaping
    # the Payload are rejected before the user provides P12 material.
    safe_archive = io.BytesIO()
    with zipfile.ZipFile(safe_archive, "w") as writer:
        writer.writestr("Payload/Sample.app/Info.plist", plist)
        symlink = zipfile.ZipInfo("Payload/Sample.app/Frameworks/Tool.framework/Versions/Current")
        symlink.create_system = 3
        symlink.external_attr = (0o120777 << 16)
        writer.writestr(symlink, "A")
    valid_link = client.post("/api/v1/tools/ipa/analyze", headers=auth,
                             files={"ipa": ("frameworks.ipa", safe_archive.getvalue(),
                                             "application/octet-stream")})
    assert valid_link.status_code == 200, valid_link.text

    unsafe_archive = io.BytesIO()
    with zipfile.ZipFile(unsafe_archive, "w") as writer:
        writer.writestr("Payload/Sample.app/Info.plist", plist)
        symlink = zipfile.ZipInfo("Payload/Sample.app/link")
        symlink.create_system = 3
        symlink.external_attr = (0o120777 << 16)
        writer.writestr(symlink, "../../../../outside")
    invalid_link = client.post("/api/v1/tools/ipa/analyze", headers=auth,
                               files={"ipa": ("unsafe.ipa", unsafe_archive.getvalue(),
                                               "application/octet-stream")})
    assert invalid_link.status_code == 422, invalid_link.text

    from ipa_tools import _manifest_details, _register_signed_output
    signed_test = Path(root) / "signed-test.ipa"
    signed_test.write_bytes(make_ipa(executable))
    metadata = _manifest_details(signed_test)
    assert metadata["bundle_id"] == "vn.eri.tool.test"
    signed_result = _register_signed_output(signed_test, metadata)
    assert signed_result["install_url"].startswith("itms-services://?action=download-manifest")
    manifest = client.get(signed_result["manifest_path"])
    assert manifest.status_code == 200, manifest.text
    manifest_data = plistlib.loads(manifest.content)
    assert manifest_data["items"][0]["metadata"]["bundle-identifier"] == "vn.eri.tool.test"
    assert manifest_data["items"][0]["metadata"]["kind"] == "software"
    actual_ipa = client.get(signed_result["download_path"])
    assert actual_ipa.status_code == 200 and actual_ipa.content == make_ipa(executable)
    assert client.get(signed_result["download_path"]).status_code == 200

    print("IPA analysis, symlink validation, decrypt and iOS install manifest: OK")
