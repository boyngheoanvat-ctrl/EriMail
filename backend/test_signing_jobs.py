"""Integration test for 2 MiB verified upload, job processing and OTA persistence."""
import hashlib
import io
import os
import plistlib
import shutil
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

with tempfile.TemporaryDirectory(prefix="eri-job-ci-") as root:
    os.environ["ERI_DB_PATH"] = str(Path(root) / "test.db")
    os.environ["ERI_UPLOAD_DIR"] = str(Path(root) / "products")
    os.environ["ERI_SIGN_JOBS_DIR"] = str(Path(root) / "jobs")
    os.environ["ERI_SIGNED_IPA_DIR"] = str(Path(root) / "signed")
    os.environ["ERI_ADMIN_KEY"] = "ci-test-signing-jobs-admin-key-0123456789"

    from fastapi.testclient import TestClient
    from app import app
    import ipa_tools
    import ipasign

    client = TestClient(app)
    register = client.post("/api/v1/auth/register", json={
        "username": "jobtester", "password": "long-password-2026",
        "display_name": "Signing Tester", "contact": "test@example.invalid"
    })
    assert register.status_code == 201, register.text
    headers = {"Authorization": "Bearer " + register.json()["token"]}
    basename = "/api/v1/tools/ipa/sign-jobs"

    plist = plistlib.dumps({
        "CFBundleIdentifier": "com.eri.signingtest",
        "CFBundleVersion": "1.2.3",
        "CFBundleShortVersionString": "1.2.3",
        "CFBundleDisplayName": "Test IPA"
    })
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as z:
        z.writestr("Payload/Example.app/Info.plist", plist)
        z.writestr("Payload/Example.app/data.bin", b"a" * (2 * 1024 * 1024 + 4096))
    ipa = stream.getvalue()

    invalid = client.post(basename, json={"filename": "Test.ipa", "size_bytes":len(ipa)})
    assert invalid.status_code == 401, invalid.text
    created = client.post(basename, headers=headers, json={
        "filename": "Test.ipa", "size_bytes": len(ipa)
    })
    assert created.status_code == 201, created.text
    state = created.json()
    job_id = state["job_id"]
    path = basename + "/" + job_id
    assert state["total_chunks"] == 2

    # Browser must send all confirmed chunks before signing can start.
    incomplete = client.post(path + "/start", headers=headers, files={
        "p12": ("cert.p12", b"fake", "application/octet-stream"),
        "mobileprovision": ("test.mobileprovision", b"fake", "application/octet-stream"),
    }, data={"password":"test-pass"})
    assert incomplete.status_code == 409, incomplete.text

    for i in [1, 0]:
        part = ipa[i * state["chunk_bytes"]:(i+1) * state["chunk_bytes"]]
        uri = path + "/chunks/" + str(i)
        checksum = hashlib.sha256(part).hexdigest()
        wrong = client.put(uri, headers={
            **headers, "X-ERI-Chunk-SHA256": "a"*64
        }, content=part)
        assert wrong.status_code == 422, wrong.text
        good = client.put(uri, headers={
            **headers, "X-ERI-Chunk-SHA256": checksum
        }, content=part)
        assert good.status_code == 200, good.text
        again = client.put(uri, headers={
            **headers, "X-ERI-Chunk-SHA256": checksum
        }, content=part)
        assert again.status_code == 200 and again.json()["duplicate"] is True
    assert client.get(path, headers=headers).json()["uploaded_bytes"] == len(ipa)

    class FakeSigner:
        def __init__(self, certificate, provision, password):
            assert Path(certificate).read_bytes() == b"test-p12"
            assert Path(provision).read_bytes() == b"test-provision"
            assert password == "test-password"
        def sign(self, source, output):
            shutil.copyfile(source, output)
            return type("SignedResult", (), {"bundle_id": "com.eri.signingtest"})()

    with patch.object(ipasign, "Key", FakeSigner):
        started = client.post(path + "/start", headers=headers, files={
            "p12": ("cert.p12", b"test-p12", "application/octet-stream"),
            "mobileprovision": ("cert.mobileprovision", b"test-provision", "application/octet-stream")
        }, data={"password":"test-password"})
    assert started.status_code == 202, started.text
    done = client.get(path, headers=headers)
    assert done.status_code == 200, done.text
    result = done.json()
    assert result["state"] == "completed", result
    assert result["result"]["download_path"].endswith("/app.ipa")
    signed = client.get(result["result"]["download_path"])
    assert signed.status_code == 200 and signed.content == ipa
    # Restarting the API process should not invalidate the signed file while
    # the container filesystem is intact.
    ipa_tools._signed_tickets.clear()
    assert client.get(result["result"]["download_path"]).content == ipa
    manifest = client.get(result["result"]["manifest_path"])
    assert manifest.status_code == 200
    assert plistlib.loads(manifest.content)["items"][0]["metadata"]["bundle-identifier"] == "com.eri.signingtest"
    assert not (Path(root) / "jobs" / job_id / "cert.p12").exists()
    assert not (Path(root) / "jobs" / job_id / "profile.mobileprovision").exists()

    wrong_user = client.post("/api/v1/auth/register", json={
        "username":"other", "password":"long-password-2026", "display_name":"Other",
        "contact":"other@example.invalid"
    })
    assert wrong_user.status_code == 201
    assert client.get(path, headers={
        "Authorization": "Bearer " + wrong_user.json()["token"]
    }).status_code == 404
    print("Chunk upload / resume / safe signing / private jobs / OTA manifest: OK")
