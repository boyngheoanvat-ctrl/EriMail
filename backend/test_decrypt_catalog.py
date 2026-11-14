"""ERI Decrypt live-inventory and user-scoped request regression tests.

All Apple replies are mocked: no Apple network or real third-party IPA copies.
"""
import hashlib
import io
import os
import plistlib
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

with tempfile.TemporaryDirectory(prefix="eri-decrypt-") as root:
    os.environ["ERI_DB_PATH"] = str(Path(root) / "eri-test.sqlite3")
    os.environ["ERI_UPLOAD_DIR"] = str(Path(root) / "uploads")
    os.environ["ERI_ADMIN_KEY"] = "ci-decrypt-admin-0123456789-0123456789"
    os.environ["ERI_SIGN_JOBS_DIR"] = str(Path(root) / "jobs")
    os.environ["ERI_SIGNED_IPA_DIR"] = str(Path(root) / "signed")

    from fastapi.testclient import TestClient
    from app import app, connection, UPLOAD_DIR
    import decrypt_catalog

    client = TestClient(app)
    register = client.post("/api/v1/auth/register", json={
        "username":"decrypttest", "display_name":"Decrypt tester",
        "password":"long-password-test-123", "contact":"test@example.invalid"
    })
    assert register.status_code == 201, register.text
    auth = {"Authorization":"Bearer " + register.json()["token"]}

    apple = {
        "results":[{
            "kind":"software", "trackId":6447961802,
            "trackName":"Football League 2026",
            "artistName":"Example Publisher",
            "bundleId":"com.example.football",
            "version":"2.0.0", "trackViewUrl":"https://apps.apple.com/vn/app/football-league/id6447961802",
            "artworkUrl100":"https://is1-ssl.mzstatic.com/image/thumb/demo/100x100bb.jpg",
            "primaryGenreName":"Games", "price":0,
            "fileSizeBytes":"2048"
        }]
    }

    base = "/api/v1/tools/ipa/decrypt"
    assert client.get(base + "/vault").status_code == 200
    assert client.get(base + "/vault").json()["total"] == 0
    assert client.get(base + "/search?q=https://untrusted.example/abc").status_code == 422
    assert client.get(base + "/search?q=x").status_code == 422
    assert client.get(base + "/search?q=abc&country=xx").status_code == 422

    with patch.object(decrypt_catalog, "_apple_json", return_value=apple) as mock:
        # Search by name, App Store URL, and Bundle ID must all find the same app.
        by_name = client.get(base + "/search",params={"q":"Football League","country":"vn"})
        assert by_name.status_code == 200, by_name.text
        assert by_name.json()["total"] == 1
        assert by_name.json()["items"][0]["ipa_available"] is False
        assert by_name.json()["items"][0]["icon_url"].startswith("https://is1-ssl.mzstatic.com")
        by_url = client.get(base + "/search",params={"q":"https://apps.apple.com/vn/app/football-league/id6447961802"})
        assert by_url.status_code == 200, by_url.text
        by_bundle = client.get(base + "/search",params={"q":"com.example.football","country":"vn"})
        assert by_bundle.status_code == 200, by_bundle.text
        assert mock.call_count == 3

        versions = client.get(base + "/versions",params={"target":"com.example.football","country":"vn"})
        assert versions.status_code == 200, versions.text
        assert versions.json()["versions"][0]["version"] == "2.0.0"
        assert versions.json()["versions"][0]["cached"] is None

        # Only authenticated customers can create or see their own request.
        no_auth = client.post(base + "/requests",json={"target":"com.example.football","country":"vn"})
        assert no_auth.status_code == 401, no_auth.text
        request = client.post(base + "/requests",headers=auth,
            json={"target":"https://apps.apple.com/vn/app/football-league/id6447961802",
                  "country":"vn","version":"2.0.0"})
        assert request.status_code == 201, request.text
        job_id = request.json()["job_id"]
        assert request.json()["state"] == "waiting_for_authorized_file"
        assert request.json()["progress"] == 0
        assert client.get(base + "/requests/" + job_id).status_code == 401
        assert client.get(base + "/requests/" + job_id,headers=auth).status_code == 200

        # Populate two physically stored archives, only one free and authorized.
        UPLOAD_DIR.mkdir(exist_ok=True,parents=True)
        ipa = io.BytesIO()
        with zipfile.ZipFile(ipa,"w") as archive:
            archive.writestr("Payload/Test.app/Info.plist",plistlib.dumps({
                "CFBundleIdentifier":"com.example.football",
                "CFBundleVersion":"2.0.0",
                "CFBundleShortVersionString":"2.0.0"
            }))
        ipa_bytes = ipa.getvalue()
        def create_product(price, allowed, name="Football League 2026", version="2.0.0"):
            with connection() as db:
                cur = db.execute(
                    "INSERT INTO ipa(name,version,bundle_id,download_url,sha256,authorized,price_vnd,platform) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (name,version,"com.example.football","private:test",
                     hashlib.sha256(ipa_bytes).hexdigest(),1 if allowed else 0,price,"ipa"))
                product_id = cur.lastrowid
                storage_id = format(product_id,"032x")
                filename = storage_id + ".ipa"
                (UPLOAD_DIR / filename).write_bytes(ipa_bytes)
                db.execute(
                    "INSERT INTO private_product_uploads(id,kind,product_id,original_name,"
                    "storage_name,size_bytes,sha256,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (storage_id,"ipa",product_id,"Test.ipa",filename,len(ipa_bytes),
                     hashlib.sha256(ipa_bytes).hexdigest(),"2026-11-14T09:00:00Z"))
                return product_id,filename

        paid_id,_ = create_product(100000,True)
        bad_id,_ = create_product(0,False)
        assert client.get(base + "/vault").json()["total"] == 0
        free_id,file_name = create_product(0,True)
        found = client.get(base + "/vault",params={"q":"football","sort":"title"})
        assert found.status_code == 200, found.text
        assert found.json()["total"] == 1
        assert found.json()["items"][0]["id"] == free_id
        assert found.json()["items"][0]["fairplay_verified"] is False
        assert found.json()["items"][0]["download_path"] == f"/api/v1/mods/{free_id}/download"
        from fastapi import HTTPException
        with patch.object(decrypt_catalog, "_apple_json",
                          side_effect=HTTPException(503, "Apple offline")):
            fallback = client.get(base + "/search", params={"q":"Football League","country":"vn"})
        assert fallback.status_code == 200, fallback.text
        assert fallback.json()["source"].startswith("ERI local vault")
        assert fallback.json()["items"][0]["cached"]["id"] == free_id
        # User request persisted in SQLite and becomes completed only once
        # administrator-supplied authorized IPA exists.
        completed = client.get(base + "/requests/" + job_id,headers=auth).json()
        assert completed["state"] == "completed"
        assert completed["progress"] == 100
        assert completed["result"]["id"] == free_id
        actual_download = client.get(completed["result"]["download_path"])
        assert actual_download.status_code == 200
        assert actual_download.content == ipa_bytes
        assert client.get(f"/api/v1/mods/{paid_id}/download").status_code == 404
        assert client.get(f"/api/v1/mods/{bad_id}/download").status_code == 404

        versions2 = client.get(base + "/versions",params={
            "target":"com.example.football","country":"vn"})
        assert versions2.status_code == 200
        assert versions2.json()["versions"][0]["cached"]["id"] == free_id

        other = client.post("/api/v1/auth/register",json={
            "username":"otherrequester","display_name":"Other",
            "password":"long-password-test-123","contact":"other@example.invalid"})
        assert other.status_code == 201, other.text
        wrong_auth={"Authorization":"Bearer " + other.json()["token"]}
        assert client.get(base + "/requests/" + job_id,headers=wrong_auth).status_code == 404

        # A stale DB record without a physical IPA file never generates a link.
        (UPLOAD_DIR / file_name).unlink()
        assert client.get(base + "/vault").json()["total"] == 0
        stale = client.get(base + "/requests/" + job_id,headers=auth).json()
        assert stale["progress"] == 0 and stale["result"] is None

    print("Apple regions/search/versions, real free vault, durable jobs and isolation: OK")
