"""Regression: a lost HTTP 201 must never duplicate or hang an IPA upload.

Uses a tiny valid IPA, temporary local SQLite and mock-free FastAPI TestClient.
No Supabase token, private customer data or production filesystem is touched.
"""
import hashlib
import io
import os
import plistlib
import tempfile
import zipfile
from pathlib import Path

with tempfile.TemporaryDirectory(prefix="eri-finalize-idempotent-") as folder:
    os.environ["ERI_DB_PATH"] = str(Path(folder) / "eri-test.sqlite3")
    os.environ["ERI_UPLOAD_DIR"] = str(Path(folder) / "uploads")
    os.environ["ERI_ADMIN_KEY"] = "ci-idempotent-test-only-key-01234567890123"
    os.environ["ERI_VAULT_MODE"] = "off"

    from fastapi.testclient import TestClient
    from app import app, connection

    client = TestClient(app)
    auth = {"X-ERI-Admin-Key": os.environ["ERI_ADMIN_KEY"]}

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Payload/Eri.app/Info.plist", plistlib.dumps({
            "CFBundleIdentifier": "com.example.eri.test",
            "CFBundleShortVersionString": "2.1.0",
            "CFBundleExecutable": "Eri",
        }))
        zf.writestr("Payload/Eri.app/Eri", b"TEST_ONLY_EXECUTABLE")
    data = archive.getvalue()
    start = client.post("/api/v1/admin/upload-sessions", headers=auth, json={
        "kind": "ipa", "filename": "Au_2_vip.ipa",
        "size_bytes": len(data), "name": "Au 2 Vip",
        "price_vnd": 200000, "authorized": True, "fast": True,
    })
    assert start.status_code == 201, start.text
    sid = start.json()["upload_id"]
    base = "/api/v1/admin/upload-sessions/" + sid
    uploaded = client.post(base + "/fast-parts/0",
        content=data, headers={**auth, "X-ERI-Chunk-SHA256": hashlib.sha256(data).hexdigest()})
    assert uploaded.status_code == 200, uploaded.text
    first = client.post(base + "/complete", headers=auth)
    assert first.status_code == 201, first.text
    pid = first.json()["id"]

    # Emulate iOS losing the response and calling complete repeatedly.
    for i in range(3):
        retry = client.post(base + "/complete", headers=auth)
        assert retry.status_code == 201, (i, retry.text)
        assert retry.json()["id"] == pid, "Never create duplicate products"
        assert retry.json()["uploaded"] is True
    # Even after local metadata is deleted, read-only session endpoints must
    # report published status without reconstructing the archived IPA.
    status = client.get(base, headers=auth)
    assert status.status_code == 200 and status.json()["published"] is True
    assert status.json()["offset"] == len(data)
    parts = client.get(base + "/fast-parts", headers=auth)
    assert parts.status_code == 200, parts.text
    assert parts.json()["published"] is True and parts.json()["received"] == [0]

    with connection() as db:
        count = db.execute("SELECT COUNT(*) FROM ipa WHERE id=?", (pid,)).fetchone()[0]
        assert count == 1
        link = db.execute(
            "SELECT id,product_id,size_bytes FROM private_product_uploads WHERE id=?", (sid,)
        ).fetchone()
        assert link and link["product_id"] == pid and link["size_bytes"] == len(data)
        no_dupes = db.execute(
            "SELECT COUNT(*) FROM private_product_uploads WHERE id=?", (sid,)
        ).fetchone()[0]
        assert no_dupes == 1
    # A bogus or forbidden upload must not reveal a published record.
    assert client.post(base + "/complete").status_code == 403
    missing = client.post("/api/v1/admin/upload-sessions/" + "a" * 32 + "/complete",
                          headers=auth)
    assert missing.status_code == 404
    print("IPA finalize retries return same product; /fast-parts reads without cloud hydration: PASS")
