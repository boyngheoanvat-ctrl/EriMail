"""Public ERI Store must list published IPA/APK without ERI ADMIN or cloud HEAD calls."""
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

with tempfile.TemporaryDirectory(prefix="eri-shop-offline-admin-") as temp:
    os.environ["ERI_DB_PATH"] = str(Path(temp) / "shop.sqlite3")
    os.environ["ERI_UPLOAD_DIR"] = str(Path(temp) / "private")
    os.environ["ERI_VAULT_MODE"] = "off"
    os.environ["ERI_ADMIN_KEY"] = "eri-shop-offline-ci-admin-key-0123456789"
    os.environ["ERI_PAYMENT_MODE"] = "static_momo"
    from fastapi.testclient import TestClient
    import app as backend

    client = TestClient(backend.app)
    with backend.connection() as db:
        for i in range(20):
            kind = "ipa" if i % 2 == 0 else "apk"
            ident = ("%032x" % (i + 100))
            cursor = db.execute(
                "INSERT INTO ipa(name,version,bundle_id,download_url,sha256,"
                "authorized,price_vnd,description,platform) "
                "VALUES(?,?,?,?,?,1,?,?,?)",
                ("Catalog app " + str(i), "1.0", "vn.eri.example",
                 "private:" + ident, "a"*64, 0, "Previously uploaded and published",kind)
            )
            storage_name = ("r2." + ident + "." + kind) if i % 3 == 0 else (ident + "." + kind)
            db.execute(
                "INSERT INTO private_product_uploads(id,kind,product_id,original_name,"
                "storage_name,size_bytes,sha256,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (ident,kind,cursor.lastrowid,"app."+kind,storage_name,
                 240_000_000,"a"*64,"2026-11-21T00:00:00+00:00")
            )
    # The admin is absent; the object storage is unresponsive. A storefront
    # listing must not issue ANY cloud HEAD request for those twenty objects.
    def blocked(*args,**kwargs):
        raise AssertionError("Public catalog triggered forbidden remote availability call")

    start=time.monotonic()
    with patch.object(backend, "_eri_vault", object()), \
         patch.object(backend, "eri_r2_object_exists", side_effect=blocked), \
         patch.object(backend, "_eri_mod_file", side_effect=blocked):
        response=client.get("/api/v1/mods")
    elapsed=time.monotonic()-start
    assert response.status_code==200, response.text
    items=response.json()["items"]
    assert len(items)==20, len(items)
    for product in items:
        assert product["download_path"] == "/api/v1/mods/" + str(product["id"]) + "/download"
        assert product["platform"] in ("ipa","apk")
        assert "previously" not in product["name"].lower()
    assert elapsed<2.0, "The public catalog should not wait for object store network calls"

    # Legacy local-only deployments, lacking the private vault, MUST still
    # hide download buttons for files that were lost with /tmp.
    with patch.object(backend, "_eri_vault", None):
        response=client.get("/api/v1/mods")
    assert response.status_code==200,response.text
    missing=[item for item in response.json()["items"] if item["id"]%3!=0]
    assert any("download_path" not in item for item in response.json()["items"])
    # The durable writer can hold its lock while a remote snapshot uploads.
    # Public catalog GET must not queue behind that lock.
    import threading
    from concurrent.futures import ThreadPoolExecutor
    entered=threading.Event()
    released=threading.Event()
    def hold_writer_guard():
        with backend._eri_db_guard:
            entered.set()
            released.wait(timeout=10)
    with ThreadPoolExecutor(max_workers=1) as executor:
        task=executor.submit(hold_writer_guard)
        assert entered.wait(timeout=3)
        try:
            start=time.monotonic()
            response=client.get("/api/v1/mods")
            elapsed=time.monotonic()-start
            assert response.status_code==200, response.text
            assert len(response.json()["items"])==20
            assert elapsed<2, "Public catalog blocked by Admin backup/writer lock"
        finally:
            released.set()
        task.result(timeout=10)

    # Paid listings must remain discoverable (and use payment flow) without
    # the local Admin app. Metadata read never depends on its runtime.
    with backend.connection() as db:
        db.execute("UPDATE ipa SET price_vnd=25000 WHERE id=(SELECT MIN(id) FROM ipa)")
    paid=client.get("/api/v1/mods").json()["items"]
    assert any(x["price_vnd"]==25000 for x in paid)
    print("PASS: 20 durable IPA/APK listings load without Admin or cloud HEAD; local-only missing file remains protected")
