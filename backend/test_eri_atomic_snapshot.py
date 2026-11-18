"""Customer/commerce writes must not outlive a failed cloud snapshot.

Uses only an isolated temporary SQLite DB and an in-memory vault.
"""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

with tempfile.TemporaryDirectory(prefix="eri-atomic-durability-") as folder:
    root = Path(folder)
    os.environ["ERI_DB_PATH"] = str(root / "store.sqlite3")
    os.environ["ERI_UPLOAD_DIR"] = str(root / "uploads")
    os.environ["ERI_ADMIN_KEY"] = "ci-atomic-test-key-0123456789-abcdefgh"
    os.environ["ERI_VAULT_MODE"] = "off"
    from fastapi.testclient import TestClient
    import app as er
    from test_eri_vault import MemoryVault
    from eri_vault import VaultError

    vault = MemoryVault()
    er._eri_vault = vault
    client = TestClient(er.app)

    def register(username):
        return client.post("/api/v1/auth/register", json={
            "username": username,
            "password": "long-private-test-passphrase",
            "display_name": "Test Buyer",
            "contact": "buyer@example.invalid",
        })

    first = register("firstcustomer")
    assert first.status_code == 201, first.text
    pointer = vault.read_snapshot_pointer()
    assert pointer is not None
    with er.connection() as db:
        assert db.execute("select count(*) from customers").fetchone()[0] == 1

    # A remote error must roll back this request, not commit it locally.
    with patch.object(vault, "snapshot_sqlite", side_effect=VaultError("simulated outage")):
        failed = register("secondcustomer")
    assert failed.status_code == 503, failed.text
    assert vault.read_snapshot_pointer() == pointer
    with er.connection() as db:
        assert db.execute("select count(*) from customers").fetchone()[0] == 1

    # A retry after the vault recovers succeeds exactly once.
    recovered = register("secondcustomer")
    assert recovered.status_code == 201, recovered.text
    with er.connection() as db:
        assert db.execute("select count(*) from customers").fetchone()[0] == 2

    # After deleting the ephemeral local DB, the durable snapshot has both.
    er.DB.unlink()
    vault.restore_sqlite(er.DB)
    with er.connection() as db:
        names = {row[0] for row in db.execute("select username from customers")}
    assert names == {"firstcustomer", "secondcustomer"}, names
    print("Atomic snapshot: failed cloud write rolls back, restored DB preserves accounts")
