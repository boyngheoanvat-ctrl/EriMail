"""Tests for real, authorized ERI IPA retrieval + Mach-O verification.

No copyrighted IPA, Apple credentials, third-party decrypt API or internet used.
"""
import hashlib
import io
import os
import plistlib
import struct
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

with tempfile.TemporaryDirectory(prefix="eri-real-worker-") as root:
    os.environ["ERI_DB_PATH"] = str(Path(root) / "database.sqlite3")
    os.environ["ERI_UPLOAD_DIR"] = str(Path(root) / "uploads")
    os.environ["ERI_ADMIN_KEY"] = "test-admin-worker-0123456789-0123456789"
    os.environ["ERI_SIGN_JOBS_DIR"] = str(Path(root) / "sign")
    os.environ["ERI_SIGNED_IPA_DIR"] = str(Path(root) / "signed")
    os.environ["ERI_DECRYPT_WORKER_ENABLED"] = "0"
    os.environ["ERI_DECRYPT_SOURCE_HOSTS"] = "ipa.example.org"

    from fastapi.testclient import TestClient
    from app import app, connection, UPLOAD_DIR
    import decrypt_catalog
    import ipa_verified_worker as worker

    client = TestClient(app)
    admin = {"X-ERI-Admin-Key": os.environ["ERI_ADMIN_KEY"]}
    api = "/api/v1/tools/ipa/decrypt"
    apple = {"results": [{
        "kind": "software", "trackId": 6447961802,
        "trackName": "ERI Self-Test App", "artistName": "ERI Test",
        "bundleId": "com.example.eri.test", "version": "2.0.0",
        "trackViewUrl": "https://apps.apple.com/vn/app/eri-test/id6447961802",
        "price": 0, "artworkUrl100": "", "primaryGenreName": "Utilities"
    }]}

    def ipa_bytes(version="2.0.0", cryptid=0, metadata=False):
        content = io.BytesIO()
        # A tiny synthetic Mach-O arm64 executable with encryption-info command.
        macho = struct.pack("<IiiIIIII", 0xFEEDFACF, 0, 0, 2, 1, 24, 0, 0)
        macho += struct.pack("<IIIIII", 0x2C, 24, 0, 0, cryptid, 0)
        with zipfile.ZipFile(content, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("Payload/Test.app/Info.plist", plistlib.dumps({
                "CFBundleIdentifier": "com.example.eri.test",
                "CFBundleShortVersionString": version,
                "CFBundleVersion": version,
                "CFBundleExecutable": "Test",
            }))
            archive.writestr("Payload/Test.app/Test", macho)
            if metadata:
                archive.writestr("iTunesMetadata.plist", b"account-private-data")
        return content.getvalue()

    healthy = ipa_bytes()
    encrypted = ipa_bytes("2.0.1", cryptid=1)
    assert worker._mach_o_unencrypted(
        struct.pack("<IiiIIIII", 0xFEEDFACF, 0, 0, 2, 1, 24, 0, 0)
        + struct.pack("<IIIIII", 0x2C, 24, 0, 0, 0, 0)) is None

    sample = Path(root) / "sample.ipa"
    sample.write_bytes(healthy)
    assert worker.verify_ipa(sample, "com.example.eri.test", "2.0.0")
    for invalid in (
        ("2.0.1", 1, False),
        ("2.0.0", 0, True),
    ):
        sample.write_bytes(ipa_bytes(*invalid))
        try:
            worker.verify_ipa(sample, "com.example.eri.test", invalid[0])
            raise AssertionError("Invalid IPA passed verification")
        except ValueError:
            pass
    sample.write_bytes(healthy)
    try:
        worker.verify_ipa(sample, "com.other.app", "2.0.0")
        raise AssertionError("Wrong Bundle ID was allowed")
    except ValueError:
        pass

    customer = client.post("/api/v1/auth/register", json={
        "username": "workersample", "display_name": "Worker test",
        "password": "long-password-123", "contact": "test@example.invalid",
    })
    assert customer.status_code == 201, customer.text
    auth = {"Authorization": "Bearer " + customer.json()["token"]}
    with patch.object(decrypt_catalog, "_apple_json", return_value=apple):
        response = client.post(api + "/requests", headers=auth, json={
            "target": "com.example.eri.test", "country": "vn", "version": "2.0.0"
        })
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]
    assert response.json()["state"] == "waiting_for_authorized_file"

    details = {
        "name": "ERI Self-Test App", "bundle_id": "com.example.eri.test",
        "version": "2.0.0", "download_url": "https://ipa.example.org/test.ipa",
        "sha256": hashlib.sha256(healthy).hexdigest(), "authorized": True,
    }
    assert client.post("/api/v1/admin/decrypt/sources", json=details).status_code == 403
    assert client.post("/api/v1/admin/decrypt/sources",
                       headers=admin, json={**details, "authorized": False}).status_code == 422
    assert client.post("/api/v1/admin/decrypt/sources", headers=admin,
                       json={**details, "download_url": "http://127.0.0.1/app.ipa"}).status_code == 422

    # Fake a licensed HTTPS origin and exercise the actual stream-copy, checksum,
    # archive verification, database publication and user-scoped download path.
    class FakeResponse(io.BytesIO):
        def __init__(self, data):
            super().__init__(data)
            self.headers = {"Content-Length": str(len(data))}

    class FakeOpener:
        def __init__(self, data):
            self.data = data
        def open(self, request, timeout=30):
            return FakeResponse(self.data)

    with patch.object(worker, "_safe_url", return_value=details["download_url"]):
        created = client.post("/api/v1/admin/decrypt/sources", headers=admin, json=details)
        assert created.status_code == 201, created.text
        assert created.json()["state"] == "queued"
        queued = client.get(api + "/requests/" + job_id, headers=auth).json()
        assert queued["state"] == "queued" and queued["progress"] == 0

        with patch.object(worker.urllib.request, "build_opener",
                          return_value=FakeOpener(healthy)):
            assert worker.process_next_source(connection, UPLOAD_DIR) is True

        finished = client.get(api + "/requests/" + job_id, headers=auth).json()
        assert finished["state"] == "completed", finished
        assert finished["progress"] == 100
        assert finished["result"]["fairplay_verified"] is True
        actual = client.get(finished["result"]["download_path"])
        assert actual.status_code == 200 and actual.content == healthy
        assert worker.process_next_source(connection, UPLOAD_DIR) is False
        assert client.get("/api/v1/admin/decrypt/worker", headers=admin).status_code == 200
        assert client.get("/api/v1/admin/decrypt/sources", headers=admin).json()["items"][0]["state"] == "completed"

        # Protected files are never published or labeled cryptid=0.
        encrypted_source = {**details, "version": "2.0.1",
                            "sha256": hashlib.sha256(encrypted).hexdigest()}
        assert client.post("/api/v1/admin/decrypt/sources",headers=admin,
                           json=encrypted_source).status_code == 201
        with patch.object(worker.urllib.request, "build_opener",
                          return_value=FakeOpener(encrypted)):
            assert worker.process_next_source(connection, UPLOAD_DIR) is True
        entries = client.get("/api/v1/admin/decrypt/sources", headers=admin).json()["items"]
        bad = next(item for item in entries if item["version"] == "2.0.1")
        assert bad["state"] == "failed" and "cryptid" in bad["message"]
        inventory = client.get(api + "/vault").json()
        assert inventory["total"] == 1

        # Invalid SHA means no publication, even for a syntactically valid IPA.
        checksum_source = {**details, "version": "2.0.2", "sha256": "f" * 64}
        assert client.post("/api/v1/admin/decrypt/sources",headers=admin,
                           json=checksum_source).status_code == 201
        with patch.object(worker.urllib.request, "build_opener",
                          return_value=FakeOpener(ipa_bytes("2.0.2"))):
            assert worker.process_next_source(connection, UPLOAD_DIR) is True
        entries = client.get("/api/v1/admin/decrypt/sources", headers=admin).json()["items"]
        mismatch = next(item for item in entries if item["version"] == "2.0.2")
        assert mismatch["state"] == "failed"
        assert client.get(api + "/vault").json()["total"] == 1

    print("ERI authorized IPA worker: download, verified cryptid, rejection, vault, customer jobs OK")
