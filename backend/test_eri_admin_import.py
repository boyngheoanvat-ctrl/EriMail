"""Verify ERI ADMIN iPhone chunk uploads publish only verified, unencrypted IPA."""
import hashlib
import io
import os
import plistlib
import struct
import tempfile
import zipfile
from pathlib import Path

with tempfile.TemporaryDirectory(prefix="eri-admin-ipa-") as root:
    os.environ["ERI_DB_PATH"] = str(Path(root) / "store.sqlite3")
    os.environ["ERI_UPLOAD_DIR"] = str(Path(root) / "storage")
    os.environ["ERI_ADMIN_KEY"] = "eri-admin-ipa-test-key-123456789012345678"
    os.environ["ERI_CORS_ORIGINS"] = "https://eri-store-web.onrender.com"

    from app import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    auth = {"X-ERI-Admin-Key": os.environ["ERI_ADMIN_KEY"]}

    def archive(cryptid):
        macho = struct.pack("<IIIIIIII", 0xfeedfacf, 0x0100000c, 0, 2, 1, 24, 0, 0)
        macho += struct.pack("<IIIIII", 0x2c, 24, 0, 0, cryptid, 0)
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as ipa:
            ipa.writestr("Payload/Demo.app/Info.plist", plistlib.dumps({
                "CFBundleIdentifier": "com.eri.demo",
                "CFBundleShortVersionString": "1.0.0",
                "CFBundleDisplayName": "Demo",
                "CFBundleExecutable": "Demo"
            }))
            ipa.writestr("Payload/Demo.app/Demo", macho)
        return data.getvalue()

    def upload(payload):
        init = client.post("/api/v1/admin/upload-sessions", headers=auth, json={
            "kind": "ipa", "filename": "Demo.ipa", "size_bytes": len(payload),
            "name": "Demo", "price_vnd": 0, "authorized": True,
            "fast": True, "verify_free_ipa": True
        })
        assert init.status_code == 201, init.text
        session = init.json()["upload_id"]
        base = "/api/v1/admin/upload-sessions/" + session
        sha = hashlib.sha256(payload).hexdigest()
        result = client.post(base + "/fast-parts/0",
                             data=payload, headers={**auth,"X-ERI-Chunk-SHA256":sha})
        assert result.status_code == 200, result.text
        progress = client.get(base + "/fast-parts",headers=auth).json()
        assert progress["received"] == [0]
        return client.post(base + "/complete",headers=auth)

    cors = client.options("/api/v1/admin/upload-sessions", headers={
        "Origin": "https://eri-store-web.onrender.com",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-eri-admin-key,content-type,x-eri-chunk-sha256"
    })
    assert cors.status_code == 200, cors.text
    assert "x-eri-admin-key" in cors.headers["access-control-allow-headers"].lower()

    encrypted = upload(archive(cryptid=1))
    assert encrypted.status_code == 422, encrypted.text
    assert client.get("/api/v1/tools/ipa/decrypt/vault").json()["total"] == 0

    original = archive(cryptid=0)
    approved = upload(original)
    assert approved.status_code == 201, approved.text
    product = approved.json()["id"]
    vault = client.get("/api/v1/tools/ipa/decrypt/vault").json()
    assert vault["total"] == 1, vault
    item = vault["items"][0]
    assert item["bundle_id"] == "com.eri.demo"
    assert item["version"] == "1.0.0"
    download = client.get("/api/v1/mods/" + str(product) + "/download")
    assert download.status_code == 200
    assert download.content == original
    print("ERI ADMIN: CORS, resumable upload, reject encrypted IPA, verify output, download OK")
