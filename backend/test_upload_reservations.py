"""Prove multiple sparse uploads cannot reserve the same free disk.

All files and requests stay inside a temporary local test environment.
"""
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

with tempfile.TemporaryDirectory(prefix="eri-capacity-reservations-") as root:
    os.environ["ERI_DB_PATH"] = str(Path(root) / "store.sqlite3")
    os.environ["ERI_UPLOAD_DIR"] = str(Path(root) / "uploads")
    os.environ["ERI_ADMIN_KEY"] = "ci-capacity-test-key-0123456789-abcdefgh"
    os.environ["ERI_VAULT_MODE"] = "off"

    from fastapi.testclient import TestClient
    from app import app, _eri_reserved_upload_bytes, ERI_UPLOAD_DISK_RESERVE_BYTES

    client = TestClient(app)
    auth = {"X-ERI-Admin-Key": os.environ["ERI_ADMIN_KEY"]}
    MiB = 1024**2

    def start(size):
        return client.post("/api/v1/admin/upload-sessions", headers=auth, json={
            "kind": "ipa", "filename": "demo.ipa", "size_bytes": size,
            "name": "Durable upload reservation", "price_vnd": 1000,
            "authorized": True, "fast": True,
        })

    with patch("app.shutil.disk_usage", return_value=SimpleNamespace(free=512 * MiB)):
        first = start(150 * MiB)
        assert first.status_code == 201, first.text
        assert _eri_reserved_upload_bytes() >= 150 * MiB
        second = start(300 * MiB)
        assert second.status_code == 507, second.text

    report = client.get("/api/v1/admin/upload-capacity", headers=auth)
    assert report.status_code == 200, report.text
    assert report.json()["pending_upload_bytes"] >= 150 * MiB
    assert report.json()["reserve_bytes"] == ERI_UPLOAD_DISK_RESERVE_BYTES
    print("Concurrent sparse-upload reservations prevent overcommit: OK")
