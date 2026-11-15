"""Mocked end-to-end F4 provider job bridge tests. No F4 or Apple traffic."""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

with tempfile.TemporaryDirectory(prefix="eri-f4-jobs-") as root:
    os.environ["ERI_DB_PATH"] = str(Path(root) / "test.sqlite3")
    os.environ["ERI_UPLOAD_DIR"] = str(Path(root) / "uploads")
    os.environ["ERI_ADMIN_KEY"] = "test-admin-key-f4-0123456789"
    os.environ["ERI_SIGN_JOBS_DIR"] = str(Path(root) / "jobs")
    os.environ["ERI_SIGNED_IPA_DIR"] = str(Path(root) / "signed")
    os.environ.pop("ERI_F4_API_TOKEN", None)

    from fastapi.testclient import TestClient
    from app import app
    import decrypt_catalog
    import f4_jobs

    client = TestClient(app)
    route = "/api/v1/tools/ipa/decrypt/f4"
    apple = {"results": [{
        "kind": "software", "trackId": 6743144418,
        "trackName": "Nghịch Thủy Hàn",
        "artistName": "VNGGames Co., Ltd",
        "bundleId": "vng.game.nth",
        "version": "102.1.11820680",
        "trackViewUrl": "https://apps.apple.com/vn/app/id6743144418",
        "artworkUrl100": "https://is1-ssl.mzstatic.com/image/thumb/test/100x100bb.jpg",
        "primaryGenreName": "Games", "price": 0,
    }]}
    target = "https://apps.apple.com/vn/app/nghich-thuy-han/id6743144418?l=vi"
    register = client.post("/api/v1/auth/register", json={
        "username": "f4owner", "display_name": "F4 test",
        "password": "very-long-password", "contact": "f4@example.invalid",
    })
    assert register.status_code == 201, register.text
    auth = {"Authorization": "Bearer " + register.json()["token"]}
    payload = {"target": target, "country": "auto", "version": ""}

    assert client.get(route + "/capabilities").json()["enabled"] is False
    assert client.post(route + "/start", json=payload).status_code == 401
    assert client.post(route + "/start", json=payload, headers=auth).status_code == 503
    assert client.get(route + "/jobs", headers=auth).json()["total"] == 0
    assert f4_jobs._download_url("https://evil.example/ipa.ipa") is None
    assert f4_jobs._download_url("//evil.example/api/decrypt/download/" + "a" * 32) is None
    assert f4_jobs._download_url("/api/decrypt/download/" + "a" * 32) == (
        f4_jobs.F4_ORIGIN + "/api/decrypt/download/" + "a" * 32
    )
    assert f4_jobs._download_url(f4_jobs.F4_ORIGIN + "/api/decrypt/download/" + "a" * 32) is not None
    assert f4_jobs._download_url(f4_jobs.F4_ORIGIN + "/api/decrypt/download/" + "a" * 32 + "?x=y") is None

    with patch.dict(os.environ, {"ERI_F4_API_TOKEN": "provider-approved-test-token"}):
        caps = client.get(route + "/capabilities").json()
        assert caps["enabled"] is True
        assert "provider-approved-test-token" not in str(caps)

        start_response = {"ok": True, "job_id": "f4remotejob0123456789", "status": "queued"}
        with patch.object(decrypt_catalog, "_apple_json", return_value=apple), \
             patch.object(f4_jobs, "_call_f4", return_value=start_response) as call:
            response = client.post(route + "/start", json=payload, headers=auth)
            assert response.status_code == 201, response.text
            job = response.json()
            assert job["state"] == "processing"
            assert job["progress"] is None
            assert job["download_url"] is None
            assert job["verified_by_eri"] is False
            local = job["job_id"]
            assert local != start_response["job_id"]
            assert call.call_count == 1
            assert call.call_args.args[0] == "/api/decrypt/start"
            assert call.call_args.args[1]["mode"] == "ios"
            assert call.call_args.args[1]["strip_account_data"] is True
            assert call.call_args.args[1]["target"].startswith("https://apps.apple.com/")

        with patch.object(decrypt_catalog, "_apple_json", return_value=apple), \
             patch.object(f4_jobs, "_call_f4") as provider:
            repeat = client.post(route + "/start", json=payload, headers=auth)
            assert repeat.status_code == 201
            assert repeat.json()["job_id"] == local
            assert repeat.json()["reused"] is True
            provider.assert_not_called()

        assert client.get(route + "/jobs", headers=auth).json()["total"] == 1
        assert client.get(route + "/jobs/" + local).status_code == 401
        assert client.get(route + "/jobs/not-valid", headers=auth).status_code == 404

        other = client.post("/api/v1/auth/register", json={
            "username": "f4other", "display_name": "Other",
            "password": "very-long-password", "contact": "other@example.invalid",
        })
        other_auth = {"Authorization": "Bearer " + other.json()["token"]}
        assert client.get(route + "/jobs/" + local, headers=other_auth).status_code == 404
        assert client.get(route + "/jobs", headers=other_auth).json()["items"] == []

        with patch.object(f4_jobs, "_call_f4",
                          return_value={"ok": True, "status": "working", "progress": 26}):
            doing = client.get(route + "/jobs/" + local, headers=auth)
            assert doing.status_code == 200
            assert doing.json()["progress"] == 26
            assert doing.json()["state"] == "processing"

        with patch.object(f4_jobs, "_call_f4", return_value={
            "ok": True, "status": "done",
            "result": {"dl_url": "/api/decrypt/download/" + "b" * 32}
        }):
            done = client.get(route + "/jobs/" + local, headers=auth)
            assert done.status_code == 200, done.text
            assert done.json()["state"] == "completed"
            assert done.json()["progress"] == 100
            assert done.json()["download_url"] == (
                f4_jobs.F4_ORIGIN + "/api/decrypt/download/" + "b" * 32
            )
        assert client.get(route + "/jobs/" + local, headers=auth).json()["state"] == "completed"
        assert "provider-approved-test-token" not in str(client.get(route + "/jobs", headers=auth).json())

        bad = f4_jobs._provider_status({
            "ok": True, "status": "done", "dl_url": "https://bad.example/wrong.ipa"
        })
        assert bad[0] == "failed" and bad[2] is None
        real_failure = f4_jobs._provider_status({
            "ok": False, "status": "failed", "progress": 99
        })
        assert real_failure[0] == "failed" and real_failure[1] is None
        no_progress = f4_jobs._provider_status({"status": "working"})
        assert no_progress[1] is None

    # Account deletion must also remove its F4 job history and remote ID.
    from app import connection
    with connection() as db:
        account_id = db.execute(
            "SELECT id FROM customers WHERE username='f4owner'"
        ).fetchone()["id"]
    deleted = client.request(
        "DELETE", "/api/v1/admin/customers/" + str(account_id),
        headers={"X-ERI-Admin-Key": os.environ["ERI_ADMIN_KEY"]},
        json={"confirm_username": "f4owner"},
    )
    assert deleted.status_code == 200, deleted.text
    with connection() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM f4_customer_jobs WHERE customer_id=?",
            (account_id,),
        ).fetchone()[0] == 0

    print("F4 opt-in, scoped jobs, real progress, strict links, and auth isolation: OK")
