"""Admin ↔ STORE integration test; no Apple ID or real certificate required."""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

with tempfile.TemporaryDirectory(prefix="eri-admin-studio-") as root:
    os.environ["ERI_DB_PATH"] = str(Path(root) / "test.sqlite3")
    os.environ["ERI_UPLOAD_DIR"] = str(Path(root) / "uploads")
    os.environ["ERI_SIGN_JOBS_DIR"] = str(Path(root) / "sign-jobs")
    os.environ["ERI_SIGNED_IPA_DIR"] = str(Path(root) / "signed")
    os.environ["ERI_ADMIN_KEY"] = "test-admin-ipa-studio-0123456789012345"

    from fastapi.testclient import TestClient
    from app import app
    import decrypt_catalog
    from signing_jobs import ROOT

    client = TestClient(app)
    admin = {"X-ERI-Admin-Key": os.environ["ERI_ADMIN_KEY"]}

    guest = client.post("/api/v1/auth/register", json={
        "username": "studio-guest", "display_name": "Studio guest",
        "password": "long-test-password-123", "contact": "private@example.invalid",
    })
    assert guest.status_code == 201, guest.text
    customer = {"Authorization": "Bearer " + guest.json()["token"]}
    path = "/api/v1/admin/ipa-studio/signing-jobs"

    assert client.get(path).status_code == 403
    assert client.get(path, headers=customer).status_code == 403
    empty = client.get(path, headers=admin)
    assert empty.status_code == 200, empty.text
    assert empty.json()["total"] == 0

    # A customer creates a real resumable upload job, without including keys.
    created = client.post("/api/v1/tools/ipa/sign-jobs", headers=customer,
                          json={"filename": "ERI-Test.ipa", "size_bytes": 4096})
    assert created.status_code == 201, created.text
    job_id = created.json()["job_id"]
    folder = ROOT / job_id
    state_file = folder / "state.json"
    state = json.loads(state_file.read_text())
    assert state["owner"] > 0
    # Test that a future worker extension cannot leak unexpected secret fields.
    state["password"] = "SUPER_SECRET_P12_PASSWORD"
    state["p12"] = "/private/cert.p12"
    state["api_token"] = "PRIVATE_SIGNING_TOKEN"
    state["result"] = {"download_path": "/private/signed/123"}
    state_file.write_text(json.dumps(state))

    overview = client.get(path, headers=admin)
    assert overview.status_code == 200, overview.text
    result = overview.json()
    assert result["total"] == 1
    row = result["items"][0]
    assert row == {
        "job_id": job_id, "requester": "studio-guest", "filename": "ERI-Test.ipa",
        "state": "uploading", "size_bytes": 4096, "created_at": state["created"]
    }
    serialized = json.dumps(result)
    assert "SUPER_SECRET" not in serialized
    assert "PRIVATE_SIGNING_TOKEN" not in serialized
    assert "cert.p12" not in serialized
    assert "download_path" not in serialized

    # An App Store link creates an ERI request, not a decrypted IPA.
    apple = {"results": [{
        "kind": "software", "trackId": 1150288115,
        "trackName": "Example Arena", "artistName": "Test publisher",
        "bundleId": "com.example.arena", "version": "1.2.0",
        "trackViewUrl": "https://apps.apple.com/vn/app/example-arena/id1150288115",
        "artworkUrl100": "", "price": 0,
    }]}
    with patch.object(decrypt_catalog, "_apple_json", return_value=apple):
        requested = client.post("/api/v1/tools/ipa/decrypt/requests",
            headers=customer,
            json={"target": "https://apps.apple.com/vn/app/example-arena/id1150288115",
                  "country": "vn", "version": "1.2.0"})
    assert requested.status_code == 201, requested.text
    assert requested.json()["state"] == "waiting_for_authorized_file"
    assert requested.json()["result"] is None
    admin_requests = client.get("/api/v1/admin/decrypt/requests", headers=admin)
    assert admin_requests.status_code == 200
    assert admin_requests.json()["items"][0]["requester"] == "studio-guest"
    assert client.get("/api/v1/admin/decrypt/requests", headers=customer).status_code == 403

    print("ERI ADMIN & ERI STORE: user-scoped IPA requests, signing queue and secret redaction OK")
