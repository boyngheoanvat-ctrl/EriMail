"""Regression: ERI IPA jobs only finish with downloaded, verified, non-FairPlay files."""
import hashlib
import io
import os
import plistlib
import struct
import tempfile
import time
import zipfile
from pathlib import Path
from unittest.mock import patch

with tempfile.TemporaryDirectory(prefix="eri-real-ipa-") as root:
    os.environ["ERI_DB_PATH"] = str(Path(root) / "eri.sqlite3")
    os.environ["ERI_UPLOAD_DIR"] = str(Path(root) / "uploads")
    os.environ["ERI_ADMIN_KEY"] = "eri-test-admin-012345678901234567890123456789"
    os.environ["ERI_IPA_SOURCE_HOSTS"] = "files.eri.example"

    from fastapi.testclient import TestClient
    from app import app
    import decrypt_catalog
    import eri_ipa_pipeline

    client = TestClient(app)
    registration = client.post("/api/v1/auth/register", json={
        "username": "pipelinertest",
        "display_name": "Pipeline Test",
        "password": "this-is-a-test-password-123",
        "contact": "test@example.invalid"
    })
    assert registration.status_code == 201, registration.text
    customer = {"Authorization": "Bearer " + registration.json()["token"]}
    admin = {"X-ERI-Admin-Key": os.environ["ERI_ADMIN_KEY"]}

    apple = {"results": [{
        "kind": "software", "trackId": 123456789,
        "trackName": "ERI Sample", "artistName": "ERI",
        "bundleId": "com.eri.sample", "version": "1.2.3",
        "trackViewUrl": "https://apps.apple.com/vn/app/eri-sample/id123456789",
        "price": 0,
    }]}
    with patch.object(decrypt_catalog, "_apple_json", return_value=apple):
        queued = client.post(
            "/api/v1/tools/ipa/decrypt/requests", headers=customer,
            json={"target": "com.eri.sample", "country": "vn"}
        )
    assert queued.status_code == 201, queued.text
    job = queued.json()
    assert job["state"] == "waiting_for_source"
    assert job["progress"] is None
    assert job["result"] is None

    # Spoofed/random URLs or absent source allowlist must not run remotely.
    bad = client.post("/api/v1/admin/decrypt/sources", headers=admin, json={
        "name": "ERI Sample", "bundle_id": "com.eri.sample",
        "version": "1.2.3", "url": "http://127.0.0.1/private",
        "sha256": "0" * 64, "distribution_authorized": True
    })
    assert bad.status_code == 422, bad.text
    assert client.post("/api/v1/admin/decrypt/sources",json={}).status_code == 403

    macho = struct.pack("<IIIIIIII", 0xfeedfacf, 0x0100000c, 0, 2, 1, 24, 0, 0)
    macho += struct.pack("<IIIIII", 0x2c, 24, 0, 0, 0, 0)  # cryptid = 0
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("Payload/ERI.app/Info.plist", plistlib.dumps({
            "CFBundleDisplayName": "ERI Sample",
            "CFBundleIdentifier": "com.eri.sample",
            "CFBundleExecutable": "ERIBin",
            "CFBundleShortVersionString": "1.2.3",
        }))
        z.writestr("Payload/ERI.app/ERIBin", macho)
    payload = archive.getvalue()

    class FakeResponse(io.BytesIO):
        def __init__(self, data):
            super().__init__(data)
            self.headers = {"Content-Length": str(len(data))}

    class FakeOpener:
        def open(self, request, timeout=None):
            assert request.full_url == "https://files.eri.example/ERI.ipa"
            return FakeResponse(payload)

    with patch.object(eri_ipa_pipeline.urllib.request, "build_opener",
                      return_value=FakeOpener()):
        registered = client.post("/api/v1/admin/decrypt/sources", headers=admin, json={
            "name": "ERI Sample", "bundle_id": "com.eri.sample",
            "version": "1.2.3", "url": "https://files.eri.example/ERI.ipa",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "distribution_authorized": True
        })
        assert registered.status_code == 201, registered.text
        for _ in range(100):
            status = client.get(
                "/api/v1/tools/ipa/decrypt/requests/" + job["job_id"],
                headers=customer
            ).json()
            if status["state"] in ("completed", "failed"):
                break
            time.sleep(0.05)
    assert status["state"] == "completed", status
    assert status["progress"] == 100
    assert status["result"]["bundle_id"] == "com.eri.sample"
    url = status["result"]["download_path"]
    final = client.get(url)
    assert final.status_code == 200, final.text
    assert final.content == payload
    assert client.get("/api/v1/tools/ipa/decrypt/vault").json()["total"] == 1

    print("ERI authorized IPA worker: trusted source, measured transfer, validation, real downloadable IPA OK")
