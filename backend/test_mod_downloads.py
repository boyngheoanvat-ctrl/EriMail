"""Smoke tests: IPA/APK FREE downloads and unavailable-file handling."""
import hashlib
import io
import os
import plistlib
import tempfile
import zipfile
from pathlib import Path

with tempfile.TemporaryDirectory(prefix="eri-mod-test-") as root:
    os.environ["ERI_DB_PATH"] = str(Path(root) / "shop.sqlite3")
    os.environ["ERI_UPLOAD_DIR"] = str(Path(root) / "files")
    os.environ["ERI_ADMIN_KEY"] = "ci-test-admin-password-0123456789abcdef"
    from fastapi.testclient import TestClient
    from app import app, UPLOAD_DIR

    admin = {"X-ERI-Admin-Key": os.environ["ERI_ADMIN_KEY"]}
    client = TestClient(app)

    def sample_file(platform):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
            if platform == "ipa":
                archive.writestr("Payload/Example.app/Info.plist", plistlib.dumps({
                    "CFBundleIdentifier": "vn.eri.example",
                    "CFBundleShortVersionString": "1.2.3"
                }))
            else:
                archive.writestr("AndroidManifest.xml", b"\x03\x00\x08\x00manifest")
            archive.writestr("data.bin", b"Mod Game test" * 100)
        return buffer.getvalue()

    def upload(platform, price):
        data = sample_file(platform)
        start = client.post("/api/v1/admin/upload-sessions", headers=admin, json={
            "kind": platform, "filename": "ModGame." + platform,
            "size_bytes": len(data), "name": "Game " + platform.upper(),
            "price_vnd": price, "authorized": True, "fast": True
        })
        assert start.status_code == 201, start.text
        session = start.json()
        base = "/api/v1/admin/upload-sessions/" + session["upload_id"]
        part = client.post(base + "/fast-parts/0", headers={
            **admin, "X-ERI-Chunk-SHA256": hashlib.sha256(data).hexdigest()
        }, content=data)
        assert part.status_code == 200, part.text
        finish = client.post(base + "/complete", headers=admin)
        assert finish.status_code == 201, finish.text
        return finish.json()["id"], data, session["upload_id"]

    for platform in ("ipa", "apk"):
        product_id, data, upload_id = upload(platform, 0)
        items = client.get("/api/v1/mods").json()["items"]
        row = next(i for i in items if i["id"] == product_id)
        assert row["platform"] == platform, row
        assert row["download_path"] == "/api/v1/mods/" + str(product_id) + "/download"
        downloaded = client.get(row["download_path"])
        assert downloaded.status_code == 200 and downloaded.content == data
        # Simulate a Render ephemeral disk reset while SQLite still has rows.
        (UPLOAD_DIR / (upload_id + "." + platform)).unlink()
        updated = next(i for i in client.get("/api/v1/mods").json()["items"]
                       if i["id"] == product_id)
        assert "download_path" not in updated and updated["file_available"] is False
        assert client.get("/api/v1/mods/" + str(product_id) + "/download").status_code == 404

    product_id, _, _ = upload("apk", 25000)
    paid = next(i for i in client.get("/api/v1/mods").json()["items"]
                if i["id"] == product_id)
    assert paid["platform"] == "apk" and paid["price_vnd"] == 25000
    assert "download_path" not in paid
    assert client.get("/api/v1/mods/" + str(product_id) + "/download").status_code == 404
    assert client.get("/api/v1/admin/catalog/apk", headers=admin).status_code == 200
    print("FREE IPA/APK downloads, paid APK, missing files and admin catalog: OK")
