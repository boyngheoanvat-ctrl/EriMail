"""Offline migration inventory tests; no live data or vault key."""
import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from eri_vault import VaultError
from migrate_live_storage import inventory, publish


class InventoryTests(unittest.TestCase):
    def test_required_tables_and_contents(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            db=root/"live.sqlite3"
            files=root/"uploads"
            files.mkdir()
            with sqlite3.connect(db) as conn:
                conn.executescript("""
                create table customers (id integer primary key, username text);
                create table private_product_uploads
                (storage_name text, size_bytes integer, sha256 text);
                create table order_deliveries
                (storage_name text, size_bytes integer, sha256 text);
                insert into customers(username) values('existing-account');
                """)
            name="b"*32+".ipa"
            content=b"private verified sample"
            (files/name).write_bytes(content)
            with sqlite3.connect(db) as conn:
                conn.execute("insert into private_product_uploads values(?,?,?)",
                             (name,len(content),hashlib.sha256(content).hexdigest()))
            count, objects=inventory(db,files)
            self.assertEqual(count,1)
            self.assertEqual(len(objects),1)
            (files/name).unlink()
            with self.assertRaises(VaultError):
                inventory(db,files)

    def test_no_blank_db_export(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(VaultError):
                inventory(Path(folder)/"missing.sqlite3",Path(folder))


if __name__=="__main__":
    unittest.main()
