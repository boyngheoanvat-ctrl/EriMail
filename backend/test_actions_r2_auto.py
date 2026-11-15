"""Offline regressions for auto GitHub Actions -> private R2 -> ERI customer."""
import hashlib
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

with tempfile.TemporaryDirectory(prefix="eri-auto-r2-") as root:
    folder = Path(root)
    os.environ["ERI_DB_PATH"] = str(folder / "eri.sqlite3")
    os.environ["ERI_UPLOAD_DIR"] = str(folder / "uploads")
    os.environ["ERI_SIGN_JOBS_DIR"] = str(folder / "jobs")
    os.environ["ERI_SIGNED_IPA_DIR"] = str(folder / "signed")
    os.environ["ERI_ADMIN_KEY"] = "eri-auto-r2-test-admin-key-01234567890123"
    os.environ["ERI_IPA_SOURCE_HOSTS"] = "files.eri.example"
    os.environ["ERI_GH_ACTIONS_REPO"] = "boyngheoanvat-ctrl/EriMail"
    os.environ["ERI_GH_ACTIONS_TOKEN"] = "local-test-gh-token"
    os.environ["ERI_R2_ACCOUNT_ID"] = "1" * 32
    os.environ["ERI_R2_BUCKET"] = "eri-private-ipa"
    os.environ["ERI_R2_ACCESS_KEY_ID"] = "local-test-r2-access-key"
    os.environ["ERI_R2_SECRET_ACCESS_KEY"] = "local-test-r2-secret"
    os.environ["ERI_ACTIONS_CALLBACK_TOKEN"] = "local-test-worker-callback-token"
    os.environ["ERI_AUTO_ACTIONS_ENABLED"] = "true"

    from fastapi.testclient import TestClient
    from app import app
    import decrypt_catalog
    import eri_auto_ipa as auto
    import actions_r2_publish as publisher
    import actions_ipa_matrix as matrix

    source_id = "eri-football-1"
    bundle = "com.example.football"
    version = "2.0.0"
    digest = "b" * 64
    source = {
        "id": source_id,
        "name": "Football League",
        "bundle_id": bundle,
        "version": version,
        "url": "https://files.eri.example/football.ipa",
        "sha256": digest,
        "distribution_authorized": True,
    }
    manifest = folder / "approved.json"
    manifest.write_text(json.dumps({"items": [source]}), encoding="utf-8")
    assert matrix.get_ids(manifest) == [source_id]
    assert matrix.get_ids(manifest, source_id) == [source_id]
    try:
        matrix.get_ids(manifest, "missing")
        raise AssertionError("Unapproved ID should not dispatch")
    except ValueError:
        pass

    artifact = folder / ("eri-" + source_id + ".ipa")
    artifact.write_bytes(b"ERI verified sample IPA bytes")
    sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
    report = {
        "source_id": source_id, "bundle_id": bundle, "version": version,
        "sha256": sha, "bytes": artifact.stat().st_size, "file_name": artifact.name,
        "fairplay_encrypted": False,
        "signature_preserved": True,
        "distribution_authorized_in_manifest": True,
    }
    validation = folder / "validation.json"
    validation.write_text(json.dumps(report), encoding="utf-8")

    class NotFound(Exception):
        response = {"Error": {"Code": "404"}}

    class FakeR2:
        def __init__(self):
            self.objects = {}
            self.uploads = 0
        def head_object(self, Bucket, Key):
            result = self.objects.get((Bucket, Key))
            if result is None:
                raise NotFound()
            return result
        def upload_file(self, path, bucket, key, ExtraArgs=None, Config=None):
            assert Path(path).read_bytes() == artifact.read_bytes()
            assert ExtraArgs["Metadata"]["sha256"] == sha
            assert Config.multipart_chunksize >= 5 * 1024 * 1024
            self.objects[(bucket, key)] = {
                "ContentLength": artifact.stat().st_size,
                "Metadata": ExtraArgs["Metadata"],
            }
            self.uploads += 1
        def generate_presigned_url(self, method, Params, ExpiresIn):
            assert method == "get_object" and ExpiresIn == 900
            return ("https://" + "1" * 32 + ".r2.cloudflarestorage.com/"
                    + Params["Bucket"] + "/" + Params["Key"] + "?X-Amz-Expires=900")

    fake_r2 = FakeR2()
    published = publisher.publish(validation, folder / "published.json", client=fake_r2)
    assert published["private"] is True
    assert published["bytes"] == artifact.stat().st_size
    assert fake_r2.uploads == 1
    publisher.publish(validation, client=fake_r2)
    assert fake_r2.uploads == 1, "Hash-addressed upload must be idempotent"
    assert publisher.notify_eri(published) is False
    assert publisher._entry(validation)["source_id"] == source_id
    bad = dict(report)
    bad["distribution_authorized_in_manifest"] = False
    validation.write_text(json.dumps(bad), encoding="utf-8")
    try:
        publisher.publish(validation, client=fake_r2)
        raise AssertionError("Unauthorized report accepted")
    except ValueError:
        pass

    client = TestClient(app)
    create = client.post("/api/v1/auth/register", json={
        "username": "autor2owner", "display_name": "Auto R2",
        "password": "very-long-password", "contact": "autor2@example.invalid",
    })
    assert create.status_code == 201, create.text
    auth = {"Authorization": "Bearer " + create.json()["token"]}
    apple = {"results": [{
        "kind": "software", "trackId": 6447961802,
        "trackName": "Football League", "artistName": "Example Developer",
        "bundleId": bundle, "version": version,
        "trackViewUrl": "https://apps.apple.com/vn/app/id6447961802",
        "artworkUrl100": "https://is1-ssl.mzstatic.com/image/thumb/x/100x100.jpg",
        "primaryGenreName": "Games", "price": 0, "fileSizeBytes": "2048",
    }]}
    base = "/api/v1/tools/ipa/decrypt"
    with patch.object(auto, "MANIFEST_PATH", manifest):
        with patch.object(decrypt_catalog, "_apple_json", return_value=apple), \
             patch.object(auto, "_github", return_value={"ok": True}) as github:
            request = client.post(base + "/requests", headers=auth, json={
                "target": "https://apps.apple.com/vn/app/id6447961802",
                "country": "vn", "version": version,
            })
            assert request.status_code == 201, request.text
            job_id = request.json()["job_id"]
            assert request.json()["state"] == "processing"
            github.assert_called_once()
            assert github.call_args.args[2]["inputs"] == {
                "source_id": source_id, "job_id": job_id
            }
            repeated = client.post(base + "/requests", headers=auth, json={
                "target": "https://apps.apple.com/vn/app/id6447961802",
                "country": "vn", "version": version,
            })
            assert repeated.status_code == 201 and repeated.json()["reused"] is True
            assert github.call_count == 1, "Duplicates must not incur new jobs"

        assert client.get(base + "/auto-status").json()["enabled"] is True
        assert client.get(base + "/remote-url/" + job_id).status_code == 401
        assert client.get(base + "/remote-url/" + job_id, headers=auth).status_code == 404

        report_body = dict(published)
        report_body["sha256"] = digest
        report_body["object_key"] = publisher.object_key({
            "bundle_id": bundle, "version": version, "sha256": digest
        })
        fake_r2.objects[(published["bucket"], report_body["object_key"])] = {
            "ContentLength": published["bytes"],
            "Metadata": {"sha256": digest},
        }
        callback = "/api/v1/worker/ipa/complete"
        assert client.post(callback, json=report_body).status_code == 403
        worker_auth = {"X-ERI-Worker-Token": os.environ["ERI_ACTIONS_CALLBACK_TOKEN"]}
        bad_report = dict(report_body)
        bad_report["object_key"] = "ipa/evil.ipa"
        assert client.post(callback, headers=worker_auth, json=bad_report).status_code == 422
        with patch.object(auto, "_r2_client", return_value=fake_r2):
            result = client.post(callback, headers=worker_auth, json=report_body)
            assert result.status_code == 200, result.text
            finished = client.get(base + "/requests/" + job_id, headers=auth)
            assert finished.status_code == 200
            assert finished.json()["state"] == "completed"
            assert finished.json()["progress"] == 100
            assert finished.json()["result"]["requires_signed_url"] is True
            signed = client.get(base + "/remote-url/" + job_id, headers=auth)
            assert signed.status_code == 200, signed.text
            assert signed.json()["ready"] is True
            assert ".r2.cloudflarestorage.com/" in signed.json()["download_url"]

        stranger = client.post("/api/v1/auth/register", json={
            "username": "r2stranger", "display_name": "Stranger",
            "password": "very-long-password", "contact": "stranger@example.invalid",
        })
        assert stranger.status_code == 201, stranger.text
        stranger_auth = {"Authorization": "Bearer " + stranger.json()["token"]}
        assert client.get(base + "/remote-url/" + job_id, headers=stranger_auth).status_code == 404
    print("ERI auto publisher/R2: approved sources, auto dispatch, callback, signed links, auth OK")
