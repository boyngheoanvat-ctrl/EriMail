"""Regression checks for checksummed parallel uploads and resume."""
import hashlib
import os
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory(prefix="eri-fast-") as workspace:
    os.environ["ERI_DB_PATH"] = str(Path(workspace) / "fast.sqlite3")
    os.environ["ERI_UPLOAD_DIR"] = str(Path(workspace) / "uploads")
    os.environ["ERI_ADMIN_KEY"] = "ci-fast-admin-key-0123456789-abcdefgh"
    from fastapi.testclient import TestClient
    from app import app, UPLOAD_DIR

    client = TestClient(app)
    key = {"X-ERI-Admin-Key": os.environ["ERI_ADMIN_KEY"]}
    chunk_bytes = 4 * 1024 * 1024
    content = b"A" * chunk_bytes + b"test tail content"
    start = client.post("/api/v1/admin/upload-sessions", headers=key, json={
        "kind": "ai", "filename": "performance.txt", "size_bytes": len(content),
        "name": "Performance Test", "price_vnd": 5000, "fast": True,
    })
    assert start.status_code == 201, start.text
    session = start.json()
    assert session["fast"] is True
    base = "/api/v1/admin/upload-sessions/" + session["upload_id"]
    assert client.get(base + "/fast-parts").status_code == 403
    assert client.post(base + "/complete", headers=key).status_code == 409

    for index in [1, 0]:
        part = content[index * chunk_bytes:(index + 1) * chunk_bytes]
        endpoint = base + "/fast-parts/" + str(index)
        headers = {**key, "X-ERI-Chunk-SHA256": hashlib.sha256(part).hexdigest()}
        response = client.post(endpoint, headers=headers, content=part)
        assert response.status_code == 200, response.text
        assert response.json()["received_bytes"] == len(part)
        assert client.post(endpoint, headers=headers, content=part).json()["duplicate"]
        status = client.get(base + "/fast-parts", headers=key)
        assert status.status_code == 200
        assert index in status.json()["received"]

    finished = client.post(base + "/complete", headers=key)
    assert finished.status_code == 201, finished.text
    uploaded = list(UPLOAD_DIR.glob("*.txt"))
    assert len(uploaded) == 1 and uploaded[0].read_bytes() == content
    print("Fast parallel upload, integrity, resume and idempotency OK")
