"""Offline staging tests; no Render/Supabase credentials or live customer data."""
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from eri_vault import ERIVault, VaultError, CHUNK_BYTES

class MemoryVault(ERIVault):
    def __init__(self):
        super().__init__(secret="A"*64)
        self.objects = {}
        self.pointer = None

    def _request(self, method, route, body=None, content_type=None, checksum=None):
        if route == "/pointer":
            if method == "GET":
                if self.pointer is None:
                    raise FileNotFoundError(route)
                return json.dumps(self.pointer).encode()
            data = json.loads(body)
            for i in range(data["chunks"]):
                key=f'/snapshots/{data["id"]}/{i}'
                if key not in self.objects:
                    return b'{"ok":false}'
            self.pointer = data
            return json.dumps({"ok":True,"generation":data["generation"]}).encode()
        if method == "PUT":
            assert checksum == hashlib.sha256(body).hexdigest()
            self.objects[route] = body
            return json.dumps({"ok":True,"size":len(body),"sha256":checksum}).encode()
        if method == "HEAD":
            if route not in self.objects:
                raise FileNotFoundError(route)
            return b""
        if method == "DELETE":
            self.objects.pop(route, None)
            return b'{"ok":true}'
        if method == "GET":
            if route not in self.objects:
                raise FileNotFoundError(route)
            return self.objects[route]
        raise ValueError(method)

class VaultTests(unittest.TestCase):
    def test_no_snapshot_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            vault=MemoryVault()
            with self.assertRaises(VaultError):
                vault.restore_sqlite(Path(folder)/"accounts.sqlite3")

    def test_private_product_roundtrip_and_corruption(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            original=root/"input.ipa"
            payload=b"IPA_TEST"*1050000
            original.write_bytes(payload)
            vault=MemoryVault()
            item=vault.upload_file(original,"a"*32)
            self.assertEqual(item["chunks"],3)
            output=root/"restored.ipa"
            vault.restore_file(output,item["id"],item["size"],item["sha256"])
            self.assertEqual(output.read_bytes(),payload)
            vault.objects["/files/"+item["id"]+"/0"]=b"bad"
            with self.assertRaises(VaultError):
                vault.restore_file(root/"bad.ipa",item["id"],item["size"],item["sha256"])
            self.assertFalse((root/"bad.ipa").exists())

    def test_sqlite_snapshot_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"customer.sqlite3"
            with sqlite3.connect(path) as c:
                c.execute("create table customers (id integer primary key, username text)")
                c.execute("insert into customers(username) values('existing')")
            vault=MemoryVault()
            info=vault.snapshot_sqlite(path)
            self.assertGreater(info["generation"],0)
            with sqlite3.connect(path) as c:
                c.execute("delete from customers")
            vault.restore_sqlite(path)
            with sqlite3.connect(path) as c:
                self.assertEqual(c.execute("select username from customers").fetchone()[0],"existing")
            next_info=vault.snapshot_sqlite(path)
            self.assertGreater(next_info["generation"],info["generation"])
            third = vault.snapshot_sqlite(path)
            assert third["previous"]["id"] == next_info["id"]
            assert not vault.chunk_exists(info["id"],kind="snapshots")
            assert vault.chunk_exists(next_info["id"],kind="snapshots")
            assert vault.chunk_exists(third["id"],kind="snapshots")

    def test_upload_part_and_recovery_metadata(self):
        vault = MemoryVault()
        obj_id = "e" * 32
        vault.put_session(obj_id, {"upload_id":obj_id, "size_bytes":12_000_000, "fast": True})
        assert vault.get_session(obj_id)["fast"] is True
        assert not vault.chunk_exists(obj_id)
        vault.put_upload_part(obj_id, 0, b"first")
        assert vault.chunk_exists(obj_id)
        assert vault.get_upload_part(obj_id, 0) == b"first"
        vault.delete_file(obj_id, 5)
        assert not vault.chunk_exists(obj_id)

    def test_refuse_path_traversal(self):
        vault=MemoryVault()
        with self.assertRaises(VaultError):
            vault._put("files","../../escape",0,b"malicious")

if __name__ == "__main__":
    unittest.main()
