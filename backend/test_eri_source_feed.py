"""ERI controlled-source auto-feed: request -> worker -> actual IPA download.

All Apple and HTTPS responses are mocked. No Apple accounts or third-party IPAs.
"""
import hashlib
import io
import json
import os
import plistlib
import struct
import tempfile
import time
import zipfile
from pathlib import Path
from unittest.mock import patch

with tempfile.TemporaryDirectory(prefix="eri-feed-test-") as root:
    os.environ["ERI_DB_PATH"] = str(Path(root) / "app.sqlite3")
    os.environ["ERI_UPLOAD_DIR"] = str(Path(root) / "uploads")
    os.environ["ERI_ADMIN_KEY"] = "test-only-admin-key-012345678901234567890"
    os.environ["ERI_IPA_SOURCE_HOSTS"] = "files.eri.example"
    os.environ["ERI_IPA_SOURCE_MANIFEST_URL"] = "https://files.eri.example/ipa-sources.json"

    from fastapi.testclient import TestClient
    from app import app
    import decrypt_catalog
    import eri_ipa_pipeline

    store_info = {"results": [{
        "kind": "software", "trackId": 135792468,
        "trackName": "ERI Feed Demo", "artistName": "ERI",
        "bundleId": "com.eri.feed", "version": "1.2.3",
        "trackViewUrl": "https://apps.apple.com/vn/app/id135792468",
        "price": 0
    }]}
    binary = struct.pack("<IIIIIIII", 0xfeedfacf, 0x0100000c, 0, 2, 1, 24, 0, 0)
    binary += struct.pack("<IIIIII", 0x2c, 24, 0, 0, 0, 0)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        z.writestr("Payload/Feed.app/Info.plist", plistlib.dumps({
            "CFBundleDisplayName": "ERI Feed Demo",
            "CFBundleIdentifier": "com.eri.feed",
            "CFBundleExecutable": "Feed",
            "CFBundleShortVersionString": "1.2.3",
        }))
        z.writestr("Payload/Feed.app/Feed", binary)
    ipa = stream.getvalue()
    digest = hashlib.sha256(ipa).hexdigest()
    feed = json.dumps({"items": [
        {"name": "ERI Feed Demo", "bundle_id": "com.eri.feed",
         "version": "1.2.3", "url": "https://files.eri.example/feed.ipa",
         "sha256": digest, "distribution_authorized": True},
        {"name": "Unapproved", "bundle_id": "com.eri.unauthorized",
         "version": "1.0", "url": "https://files.eri.example/other.ipa",
         "sha256": "0" * 64, "distribution_authorized": False},
    ]}).encode("utf-8")

    class FakeResponse(io.BytesIO):
        def __init__(self, contents):
            super().__init__(contents)
            self.headers = {"Content-Length": str(len(contents))}

    class FakeOpener:
        def open(self, request, timeout=None):
            if request.full_url == os.environ["ERI_IPA_SOURCE_MANIFEST_URL"]:
                return FakeResponse(feed)
            if request.full_url == "https://files.eri.example/feed.ipa":
                return FakeResponse(ipa)
            raise AssertionError("Unexpected outbound request " + request.full_url)

    client = TestClient(app)
    created = client.post("/api/v1/auth/register", json={
        "username": "feeduser", "display_name": "Feed user",
        "password": "strong-test-password-456",
        "contact": "feed@example.invalid"
    })
    assert created.status_code == 201, created.text
    auth = {"Authorization": "Bearer " + created.json()["token"]}
    engine = client.get("/api/v1/tools/ipa/decrypt/engine")
    assert engine.status_code == 200
    assert engine.json()["source_feed_configured"] is True
    assert engine.json()["fairplay_decrypt_available"] is False

    with patch.object(decrypt_catalog, "_apple_json", return_value=store_info), \
            patch.object(eri_ipa_pipeline.urllib.request, "build_opener",
                         return_value=FakeOpener()):
        res = client.post("/api/v1/tools/ipa/decrypt/requests",
            json={"target": "com.eri.feed", "country": "vn"}, headers=auth)
        assert res.status_code == 201, res.text
        job = res.json()
        assert job["source_available"] is True
        assert job["job_id"]
        for _ in range(150):
            status = client.get(
                "/api/v1/tools/ipa/decrypt/requests/" + job["job_id"],
                headers=auth
            ).json()
            if status["state"] in ("completed", "failed"):
                break
            time.sleep(0.05)

    assert status["state"] == "completed", status
    assert status["progress"] == 100
    assert status["result"]["download_path"]
    assert client.get(status["result"]["download_path"]).content == ipa
    engine = client.get("/api/v1/tools/ipa/decrypt/engine").json()
    assert engine["registered_sources"] == 1
    assert client.get("/api/v1/tools/ipa/decrypt/vault").json()["total"] == 1
    print("ERI self-hosted authorized IPA source feed: real request, worker and download OK")
