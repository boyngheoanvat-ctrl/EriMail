"""Staging integration: customer credentials and IPA remain after /tmp loss."""
import hashlib
import io
import os
import plistlib
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from test_eri_vault import MemoryVault
from eri_vault import CHUNK_BYTES, VaultError


class PersistenceIntegration(unittest.TestCase):
    def test_customer_and_admin_ipa_survive_restart(self):
        with tempfile.TemporaryDirectory(prefix="eri-vault-api-test-") as root:
            base = Path(root)
            env = {
                "ERI_DB_PATH":str(base / "eri.sqlite3"),
                "ERI_UPLOAD_DIR":str(base / "uploads"),
                "ERI_ADMIN_KEY":"eri-staging-test-admin-key-012345678901234567",
                "ERI_VAULT_MODE":"off",
                "ERI_AUTO_ACTIONS_ENABLED":"false",
                "ERI_SIGN_JOBS_DIR":str(base / "signed-jobs"),
                "ERI_SIGNED_IPA_DIR":str(base / "signed"),
            }
            with patch.dict(os.environ, env):
                from fastapi.testclient import TestClient
                import app as er
                vault = MemoryVault()
                previous = er._eri_vault
                er._eri_vault = vault
                try:
                    api = TestClient(er.app)
                    username = "cloudcustomer"
                    password = "long-and-private-account-password"
                    create = api.post("/api/v1/auth/register", json={
                        "username":username,"display_name":"Cloud Customer",
                        "password":password,"contact":"private@example.invalid"
                    })
                    self.assertEqual(create.status_code,201,create.text)
                    self.assertTrue(vault.read_snapshot_pointer(), "register must save durable DB")
                    session = create.json()["token"]

                    # Simulate Render losing its entire ephemeral SQLite filesystem.
                    er.DB.unlink()
                    vault.restore_sqlite(er.DB)
                    login = api.post("/api/v1/auth/login", json={
                        "username":username, "password":password
                    })
                    self.assertEqual(login.status_code,200,login.text)
                    self.assertTrue(login.json().get("token"))
                    still_logged_in = api.get("/api/v1/auth/me",
                                              headers={"Authorization":"Bearer "+session})
                    self.assertEqual(still_logged_in.status_code,200,still_logged_in.text)

                    # An IPA that crosses one 4-MiB boundary, as in a real upload.
                    body = io.BytesIO()
                    info = plistlib.dumps({
                        "CFBundleIdentifier":"vn.eri.store.example",
                        "CFBundleShortVersionString":"2.1.0",
                        "CFBundleVersion":"21",
                        "CFBundleDisplayName":"Test IPA"
                    })
                    with zipfile.ZipFile(body,"w",compression=zipfile.ZIP_STORED) as archive:
                        archive.writestr("Payload/Test.app/Info.plist",info)
                        archive.writestr("Payload/Test.app/data.bin",os.urandom(CHUNK_BYTES+2000))
                    data = body.getvalue()
                    admin = {"X-ERI-Admin-Key":env["ERI_ADMIN_KEY"]}
                    start = api.post("/api/v1/admin/upload-sessions",
                                     headers=admin,json={
                        "kind":"ipa","filename":"admin.ipa","size_bytes":len(data),
                        "name":"Test Admin IPA","price_vnd":0,"description":"Vault test",
                        "authorized":True,"fast":True
                    })
                    self.assertEqual(start.status_code,201,start.text)
                    upload_id = start.json()["upload_id"]
                    self.assertTrue(vault.get_session(upload_id)["fast"])
                    prefix = "/api/v1/admin/upload-sessions/" + upload_id
                    first=data[:CHUNK_BYTES]
                    part=api.post(prefix+"/fast-parts/0",content=first,headers={
                        **admin,"X-ERI-Chunk-SHA256":hashlib.sha256(first).hexdigest()
                    })
                    self.assertEqual(part.status_code,200,part.text)
                    self.assertTrue(vault.chunk_exists(upload_id,0))
                    self.assertEqual(api.get(prefix+"/fast-parts",headers=admin).json()["received"],[0])

                    # Simulate losing every on-disk upload chunk/session but
                    # keep remote cloud objects and the DB snapshot untouched.
                    if er.UPLOAD_DIR.exists():
                        shutil.rmtree(er.UPLOAD_DIR)
                    resumed = api.get(prefix+"/fast-parts",headers=admin)
                    self.assertEqual(resumed.status_code,200,resumed.text)
                    self.assertEqual(resumed.json()["received"],[0])
                    for idx in range(1,(len(data)+CHUNK_BYTES-1)//CHUNK_BYTES):
                        chunk=data[idx*CHUNK_BYTES:(idx+1)*CHUNK_BYTES]
                        ack=api.post(prefix+f"/fast-parts/{idx}",content=chunk,headers={
                            **admin,"X-ERI-Chunk-SHA256":hashlib.sha256(chunk).hexdigest()
                        })
                        self.assertEqual(ack.status_code,200,ack.text)
                    completed=api.post(prefix+"/complete",headers=admin)
                    self.assertEqual(completed.status_code,201,completed.text)
                    product_id=completed.json()["id"]
                    self.assertTrue(vault.read_snapshot_pointer())
                    # The final completed IPA is still retrievable after losing /tmp.
                    final_path=er.UPLOAD_DIR/(upload_id+".ipa")
                    self.assertTrue(final_path.exists())
                    final_path.unlink()
                    downloaded=api.get(f"/api/v1/mods/{product_id}/download")
                    self.assertEqual(downloaded.status_code,200,downloaded.text[:300])
                    self.assertEqual(downloaded.content,data)
                    deleted=api.delete(f"/api/v1/admin/catalog/ipa/{product_id}",headers=admin)
                    self.assertEqual(deleted.status_code,200,deleted.text)
                    self.assertEqual(deleted.json()["cloud_cleanup_pending"],0)
                    self.assertFalse(vault.chunk_exists(upload_id,0))
                    # Account credentials remain in snapshots even after IPA deletion.
                    er.DB.unlink()
                    vault.restore_sqlite(er.DB)
                    again=api.post("/api/v1/auth/login",json={
                        "username":username,"password":password
                    })
                    self.assertEqual(again.status_code,200,again.text)
                finally:
                    er._eri_vault=previous

    def test_missing_snapshot_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="eri-vault-no-seed-") as tmp:
            vault=MemoryVault()
            with self.assertRaises(VaultError):
                vault.restore_sqlite(Path(tmp)/"empty.sqlite3")


if __name__ == "__main__":
    unittest.main()
