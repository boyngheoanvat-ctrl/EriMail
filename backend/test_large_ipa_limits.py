"""Capacity-aware IPA/API regression tests, without allocating large payloads.

These tests use temporary SQLite files, sparse placeholders, mock disk capacity,
and an in-memory vault. Never call production endpoints or read live secrets.
"""
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

with tempfile.TemporaryDirectory(prefix="eri-no-fixed-ipa-cap-") as folder:
    os.environ["ERI_DB_PATH"] = str(Path(folder) / "store.sqlite3")
    os.environ["ERI_UPLOAD_DIR"] = str(Path(folder) / "uploads")
    os.environ["ERI_ADMIN_KEY"] = "ci-large-ipa-key-0123456789-abcdefgh"
    os.environ["ERI_VAULT_MODE"] = "off"
    os.environ["ERI_MOD_UPLOAD_MAX_BYTES"] = "0"
    os.environ["ERI_PRIVATE_MAX_PRODUCT_BYTES"] = "0"

    from fastapi.testclient import TestClient
    from pydantic import ValidationError
    from app import (
        app, UPLOAD_LIMITS, MOD_UPLOAD_MAX_BYTES, DELIVERY_MAX_BYTES,
        DeliveryBegin, ERI_UPLOAD_DISK_RESERVE_BYTES,
    )
    from eri_vault import MAX_PRODUCT_BYTES, CHUNK_BYTES, MAX_FILE_CHUNKS, VaultError
    from test_eri_vault import MemoryVault

    assert MOD_UPLOAD_MAX_BYTES == MAX_PRODUCT_BYTES == DELIVERY_MAX_BYTES == 0
    assert UPLOAD_LIMITS["ipa"] == UPLOAD_LIMITS["apk"] == 0
    assert UPLOAD_LIMITS["ai"] == 20 * 1024**2

    client = TestClient(app)
    auth = {"X-ERI-Admin-Key": os.environ["ERI_ADMIN_KEY"]}
    # The capacity guard normally creates the directory. In the mocked
    # sparse-file path, create the isolated test directory explicitly.
    Path(os.environ["ERI_UPLOAD_DIR"]).mkdir(parents=True, exist_ok=True)

    def begin(kind: str, size: int):
        return client.post("/api/v1/admin/upload-sessions", headers=auth, json={
            "kind": kind, "filename": "large." + kind, "size_bytes": size,
            "name": "Publisher-approved package", "price_vnd": 1000,
            "authorized": True, "fast": True,
        })

    # Sparse files make these checks fast; real device/cloud storage must
    # be sufficient separately.
    for kind in ("ipa", "apk"):
        for size in (724_100_000, 2 * 1024**3 + 1, 5 * 1024**3 + 1):
            with patch("app._eri_check_upload_capacity", return_value=None):
                result = begin(kind, size)
            assert result.status_code == 201, (kind, size, result.text)
            assert result.json()["size_bytes"] == size

    # No fixed 413 cap, but a clear HTTP 507 if the disk cannot hold it.
    with patch("app.shutil.disk_usage", return_value=SimpleNamespace(free=256 * 1024**2)):
        insufficient = begin("ipa", 724_100_000)
    assert insufficient.status_code == 507, insufficient.text
    assert "Insufficient server disk" in insufficient.text

    with patch("app.shutil.disk_usage", return_value=SimpleNamespace(free=2 * 1024**3)):
        cap = client.get("/api/v1/admin/upload-capacity", headers=auth)
    assert cap.status_code == 200 and cap.json()["max_mod_file_bytes"] is None
    assert cap.json()["reserve_bytes"] == ERI_UPLOAD_DISK_RESERVE_BYTES
    assert client.get("/api/v1/admin/upload-capacity").status_code == 403

    too_large_ai = begin("ai", 20 * 1024**2 + 1)
    assert too_large_ai.status_code == 413, too_large_ai.text
    DeliveryBegin(filename="large.ipa", size_bytes=5 * 1024**3 + 1)
    try:
        DeliveryBegin(filename="absurd.ipa", size_bytes=2**63)
    except ValidationError:
        pass
    else:
        raise AssertionError("SQLite signed integer range must remain enforced")

    vault = MemoryVault()
    assert vault._valid_part("files", 511)
    assert vault._valid_part("files", 512)
    assert vault._valid_part("files", MAX_FILE_CHUNKS - 1)
    assert not vault._valid_part("files", MAX_FILE_CHUNKS)
    assert vault._valid_part("snapshots", 127)
    assert not vault._valid_part("snapshots", 128)
    try:
        vault.delete_file("a" * 32, MAX_FILE_CHUNKS * CHUNK_BYTES + 1)
    except VaultError:
        pass
    else:
        raise AssertionError("Index namespace must be bounded")

    ios_code = (Path(__file__).resolve().parents[1] /
                "apps/ERIAdmin/ERIAdminApp.swift").read_text(encoding="utf-8")
    assert "private let eriModUploadMaxBytes = Int.max" in ios_code
    assert "case .ipa, .apk: return eriModUploadMaxBytes" in ios_code
    print("Capacity-aware large IPA/APK checks passed; no large bytes allocated")
