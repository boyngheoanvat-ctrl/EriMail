"""Offline tests for the ERI GitHub Actions admin bridge."""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

with tempfile.TemporaryDirectory(prefix="eri-gh-bridge-") as temp:
    os.environ["ERI_DB_PATH"] = str(Path(temp) / "eri.sqlite3")
    os.environ["ERI_UPLOAD_DIR"] = str(Path(temp) / "uploads")
    os.environ["ERI_ADMIN_KEY"] = "eri-test-admin-key-01234567890123456789"
    os.environ["ERI_IPA_SOURCE_HOSTS"] = "files.eri.example"
    os.environ.pop("ERI_GH_ACTIONS_TOKEN", None)
    os.environ.pop("ERI_GH_ACTIONS_REPO", None)

    from fastapi.testclient import TestClient
    from app import app
    import eri_actions_bridge as bridge

    client = TestClient(app)
    endpoint = "/api/v1/admin/decrypt/actions"
    key = {"X-ERI-Admin-Key": os.environ["ERI_ADMIN_KEY"]}
    catalog = Path(temp) / "sources.json"
    catalog.write_text(json.dumps({"items": [
        {"id": "sample", "name": "Sample", "bundle_id": "com.eri.sample",
         "version": "1.0", "url": "https://files.eri.example/sample.ipa",
         "sha256": "a" * 64, "distribution_authorized": True},
        {"id": "not-approved", "name": "Draft", "distribution_authorized": False}
    ]}), encoding="utf-8")

    with patch.object(bridge, "SOURCE_MANIFEST", catalog):
        assert client.get(endpoint + "/sources").status_code == 403
        sources = client.get(endpoint + "/sources", headers=key)
        assert sources.status_code == 200, sources.text
        assert [item["source_id"] for item in sources.json()["items"]] == ["sample"]
        assert client.post(endpoint + "/dispatch", json={"source_id": "sample"}).status_code == 403
        assert client.post(endpoint + "/dispatch", headers=key,
                           json={"source_id": "not-approved"}).status_code == 422
        assert client.post(endpoint + "/dispatch", headers=key,
                           json={"source_id": "sample"}).status_code == 503

        with patch.dict(os.environ, {
            "ERI_GH_ACTIONS_REPO": "boyngheoanvat-ctrl/EriMail",
            "ERI_GH_ACTIONS_TOKEN": "test-token-placeholder",
        }):
            with patch.object(bridge, "_github", return_value={"ok": True}) as dispatch:
                resp = client.post(endpoint + "/dispatch", headers=key,
                                   json={"source_id": "sample"})
                assert resp.status_code == 202, resp.text
                assert resp.json()["accepted"] is True
                assert dispatch.call_args.args[0] == "POST"
                assert dispatch.call_args.args[2]["inputs"] == {"source_id": "sample"}
                assert "test-token-placeholder" not in resp.text

            mocked_runs = {"workflow_runs": [{
                "id": 123, "status": "completed", "conclusion": "success",
                "html_url": "https://github.com/boyngheoanvat-ctrl/EriMail/actions/runs/123",
                "created_at": "2026-11-15T00:00:00Z"
            }]}
            with patch.object(bridge, "_github", return_value=mocked_runs):
                assert client.get(endpoint + "/runs").status_code == 403
                result = client.get(endpoint + "/runs", headers=key)
                assert result.status_code == 200, result.text
                assert result.json()["items"][0]["conclusion"] == "success"

    print("ERI Actions admin bridge: auth, approved source and GitHub token isolation OK")
