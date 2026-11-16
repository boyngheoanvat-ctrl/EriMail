"""Admin can edit published products without replacing files or rewriting orders."""
import os
import tempfile
import unittest
from pathlib import Path

_ROOT = tempfile.TemporaryDirectory(prefix="eri-admin-edit-ci-")
os.environ["ERI_DB_PATH"] = str(Path(_ROOT.name)/"eri.sqlite3")
os.environ["ERI_UPLOAD_DIR"] = str(Path(_ROOT.name)/"uploads")
os.environ["ERI_VAULT_MODE"] = "off"
os.environ["ERI_ADMIN_KEY"] = "ci-catalog-edit-secret-0123456789012345678"
os.environ["ERI_PAYMENT_MODE"] = "static_momo"
os.environ["ERI_AUTO_ACTIONS_ENABLED"] = "false"

from fastapi.testclient import TestClient
from app import app, connection

client = TestClient(app)
admin = {"X-ERI-Admin-Key":os.environ["ERI_ADMIN_KEY"]}


class CatalogEditTests(unittest.TestCase):
    def create(self, path, data):
        response=client.post(path,headers=admin,json=data)
        self.assertEqual(response.status_code,201,response.text)
        return response.json()["id"]

    def test_edit_in_place_retains_orders_and_upload(self):
        mod=self.create("/api/v1/admin/ipa", {
            "name":"Old Mod", "version":"1.2.3", "bundle_id":"com.eri.example",
            "download_url":"https://example.org/app.ipa",
            "sha256":"a"*64,"authorized":True,"price_vnd":10000
        })
        ai=self.create("/api/v1/admin/products",{
            "name":"Old AI","description":"Giao cũ", "price_vnd":100000,"active":True
        })
        cert=self.create("/api/v1/admin/certificates",{
            "name":"Old Cert","issuer":"Old Issuer","description":"Cert cũ",
            "price_vnd":200000,"active":True
        })
        # A previously published upload is referenced by the existing product.
        # Editing cannot delete, replace, or rotate the private object.
        with connection() as db:
            db.execute(
                "INSERT INTO private_product_uploads(id,kind,product_id,original_name,"
                "storage_name,size_bytes,sha256,created_at) VALUES(?,?,?,?,?,?,?,?)",
                ("b"*32,"ipa",mod,"sample.ipa","b"*32+".ipa",12345,"a"*64,
                 "2026-11-16T00:00:00+00:00")
            )

        user=client.post("/api/v1/auth/register",json={
            "username":"existingbuyer", "password":"StrongAccountPassword2026!",
            "display_name":"Existing Buyer", "contact":"user@example.invalid"
        })
        self.assertEqual(user.status_code,201,user.text)
        auth={"Authorization":"Bearer "+user.json()["token"]}
        orders={}
        for kind,pid,amount in (("mod",mod,10000),("ai",ai,130000),("certificate",cert,260000)):
            reply=client.post("/api/v1/orders",headers=auth,json={"kind":kind,"product_id":pid})
            self.assertEqual(reply.status_code,201,reply.text)
            self.assertEqual(reply.json()["amount_vnd"],amount)
            orders[kind]=reply.json()

        self.assertEqual(client.patch(f"/api/v1/admin/catalog/ipa/{mod}",json={
            "name":"New Mod","description":"New mod description","price_vnd":0
        }).status_code,403)
        for kind,pid,payload in (
            ("ipa",mod,{"name":"New Mod","description":"New mod description","price_vnd":0}),
            ("ai",ai,{"name":"New AI","description":"Delivery via account","price_vnd":300000}),
            ("certificate",cert,{"name":"New Cert","description":"New certificate details",
                                 "price_vnd":50000,"issuer":"Eri Nguyễn"})):
            result=client.patch(f"/api/v1/admin/catalog/{kind}/{pid}",headers=admin,json=payload)
            self.assertEqual(result.status_code,200,result.text)
            self.assertTrue(result.json()["updated"])
            self.assertEqual(result.json()["item"]["id"],pid)
            self.assertEqual(result.json()["price_applies_to"],"future_orders_only")

        mod_row=client.get("/api/v1/admin/catalog/ipa",headers=admin).json()["items"][0]
        self.assertEqual(mod_row["version"],"1.2.3")
        self.assertEqual(mod_row["name"],"New Mod")
        self.assertEqual(mod_row["description"],"New mod description")
        self.assertEqual(mod_row["price_vnd"],0)
        self.assertEqual(client.get("/api/v1/mods").json()["items"][0]["price_vnd"],0)

        ai_row=client.get("/api/v1/products").json()["items"][0]
        self.assertEqual(ai_row["name"],"New AI")
        self.assertEqual(ai_row["price_vnd"],390000)
        cert_row=client.get("/api/v1/certificates").json()["items"][0]
        self.assertEqual(cert_row["name"],"New Cert")
        self.assertEqual(cert_row["issuer"],"Eri Nguyễn")
        self.assertEqual(cert_row["price_vnd"],65000)

        with connection() as db:
            file=db.execute("SELECT id,product_id,storage_name,sha256 "
                            "FROM private_product_uploads WHERE product_id=?",(mod,)).fetchone()
            self.assertEqual(file["id"],"b"*32)
            self.assertEqual(file["storage_name"],"b"*32+".ipa")
            self.assertEqual(file["sha256"],"a"*64)

        # Existing orders remain immutable snapshots after catalog price edits.
        for kind,original in orders.items():
            old=client.get("/api/v1/orders/"+original["id"],headers=auth)
            self.assertEqual(old.status_code,200,old.text)
            self.assertEqual(old.json()["amount_vnd"],original["amount_vnd"])
            self.assertEqual(old.json()["product_name"],original["product_name"])
            self.assertEqual(old.json()["status"],original["status"])

        for kind,pid,new_price in (("ai",ai,390000),("certificate",cert,65000)):
            order=client.post("/api/v1/orders",headers=auth,json={"kind":kind,"product_id":pid})
            self.assertEqual(order.status_code,201,order.text)
            self.assertEqual(order.json()["amount_vnd"],new_price)

        # Going FREE does not rewrite the old paid order and disallows paid checkout.
        free=client.post("/api/v1/orders",headers=auth,json={"kind":"mod","product_id":mod})
        self.assertEqual(free.status_code,422,free.text)

        for kind,pid,payload in (
            ("ipa",mod,{"name":" ","description":"","price_vnd":10}),
            ("ai",ai,{"name":"a","description":"","price_vnd":0}),
            ("certificate",cert,{"name":"x","description":"","price_vnd":1,"issuer":" "}),
            ("ipa",mod,{"name":"x","description":"","price_vnd":1,"storage_name":"attacker.ipa"}),
            ("ipa",mod,{"name":"x","description":"","price_vnd":-1}),
        ):
            invalid=client.patch(f"/api/v1/admin/catalog/{kind}/{pid}",headers=admin,json=payload)
            self.assertEqual(invalid.status_code,422,invalid.text)
        other=client.patch(f"/api/v1/admin/catalog/apk/{mod}",headers=admin,
                           json={"name":"Wrong","description":"","price_vnd":5})
        self.assertEqual(other.status_code,404,other.text)
        self.assertEqual(client.get("/api/v1/admin/catalog/ipa",headers=admin).json()["items"][0]["name"],"New Mod")

if __name__=="__main__":
    unittest.main()
