"""Staging checks for IPA/APK files above the former 512 MiB ceiling.

Runs against a temporary SQLite database and sparse files; never touches the
production Render or Supabase projects and never generates a 2 GiB payload.
"""
import os
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory(prefix="eri-large-ipa-") as folder:
    os.environ["ERI_DB_PATH"] = str(Path(folder) / "store.sqlite3")
    os.environ["ERI_UPLOAD_DIR"] = str(Path(folder) / "uploads")
    os.environ["ERI_ADMIN_KEY"] = "ci-staging-large-ipa-key-0123456789"
    os.environ["ERI_VAULT_MODE"] = "off"

    from fastapi.testclient import TestClient
    from pydantic import ValidationError
    from app import app, UPLOAD_LIMITS, MOD_UPLOAD_MAX_BYTES, DELIVERY_MAX_BYTES, DeliveryBegin
    from eri_vault import MAX_PRODUCT_BYTES, CHUNK_BYTES, VaultError
    from test_eri_vault import MemoryVault

    assert MOD_UPLOAD_MAX_BYTES == 2 * 1024**3
    assert UPLOAD_LIMITS["ipa"] == UPLOAD_LIMITS["apk"] == MOD_UPLOAD_MAX_BYTES
    assert MAX_PRODUCT_BYTES == DELIVERY_MAX_BYTES == MOD_UPLOAD_MAX_BYTES

    client = TestClient(app)
    headers = {"X-ERI-Admin-Key": os.environ["ERI_ADMIN_KEY"]}

    def start(kind: str, size: int):
        return client.post("/api/v1/admin/upload-sessions", headers=headers, json={
            "kind": kind, "filename": "large." + kind,
            "size_bytes": size, "name": "Large authorized mod",
            "price_vnd": 1000, "authorized": True, "fast": True
        })

    for kind in ("ipa", "apk"):
        # Sparse preallocation verifies metadata and the 129th chunk boundary,
        # plus the 724.1 MB file size reported by the iOS user.
        for size in (512 * 1024**2 + 1, 724_100_000):
            accepted = start(kind, size)
            assert accepted.status_code == 201, (kind, size, accepted.text)
            assert accepted.json()["size_bytes"] == size
        denied = start(kind, MOD_UPLOAD_MAX_BYTES + 1)
        assert denied.status_code == 413, (kind, denied.text)

    DeliveryBegin(filename="large.ipa", size_bytes=MOD_UPLOAD_MAX_BYTES)
    try:
        DeliveryBegin(filename="too-large.ipa", size_bytes=MOD_UPLOAD_MAX_BYTES + 1)
    except ValidationError:
        pass
    else:
        raise AssertionError("Delivery must reject >2 GiB")

    vault = MemoryVault()
    # The cloud worker needs index 128 and above to be permitted before
    # production can accept a file larger than 512 MiB.
    vault.delete_file("a" * 32, 512 * 1024**2 + 1)
    try:
        vault.delete_file("a" * 32, MOD_UPLOAD_MAX_BYTES + 1)
    except VaultError:
        pass
    else:
        raise AssertionError("Private vault must reject >2 GiB")

    app_source = (Path(__file__).resolve().parents[1] /
                  "apps/ERIAdmin/ERIAdminApp.swift").read_text(encoding="utf-8")
    assert "case .ipa, .apk: return eriModUploadMaxBytes" in app_source
    assert "private let eriModUploadMaxBytes = 2 * 1024 * 1024 * 1024" in app_source
    print("Large IPA/APK metadata and vault bounds: OK (no large payload allocated)")
