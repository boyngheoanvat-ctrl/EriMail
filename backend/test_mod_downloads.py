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
    os.environ["ERI_PAYMENT_MODE"] = "static_momo"
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
    # A paid APK must be deliverable to its specific buyer, never publicly.
    buyer = client.post("/api/v1/auth/register", json={
        "username": "apkbuyer", "password": "apktestpassword2026",
        "display_name": "APK Customer", "contact": "contact@example.test"
    })
    assert buyer.status_code == 201, buyer.text
    token = buyer.json()["token"]
    authorization = {"Authorization": "Bearer " + token}
    order = client.post("/api/v1/orders", headers=authorization, json={
        "kind": "mod", "product_id": product_id
    })
    assert order.status_code == 201, order.text
    order_id = order.json()["id"]
    from app import connection
    with connection() as db:
        db.execute("UPDATE orders SET status='paid' WHERE id=?", (order_id,))
    delivery = sample_file("apk")
    base = "/api/v1/admin/orders/" + order_id + "/delivery-sessions"
    begin = client.post(base, headers=admin, json={
        "filename": "paid-game.apk", "size_bytes": len(delivery)
    })
    assert begin.status_code == 201, begin.text
    session_id = begin.json()["upload_id"]
    session_path = base + "/" + session_id
    transferred = client.put(session_path, headers={
        **admin, "X-ERI-Upload-Offset": "0"
    }, content=delivery)
    assert transferred.status_code == 200, transferred.text
    completed = client.post(session_path + "/complete", headers=admin)
    assert completed.status_code == 201, completed.text
    received = client.get("/api/v1/me/orders/" + order_id + "/delivery",
                          headers=authorization)
    assert received.status_code == 200 and received.content == delivery

    print("FREE IPA/APK, missing files, paid APK order and delivery: OK")
