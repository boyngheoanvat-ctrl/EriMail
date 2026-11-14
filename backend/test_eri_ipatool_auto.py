"""Test on-demand ipatool acquisition without Apple credentials or network.

A fake executable emits controlled test IPA archives. This verifies an actual
worker file, its SHA/cryptid checks and the correct failure on FairPlay.
"""
import base64
import io
import os
import plistlib
import struct
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from unittest.mock import patch


def make_ipa(bundle_id, encrypted=False):
    exe = struct.pack("<IIIIIIII", 0xfeedfacf, 0x0100000c, 0, 2, 1, 24, 0, 0)
    exe += struct.pack("<IIIIII", 0x2c, 24, 0, 0, 1 if encrypted else 0, 0)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("Payload/Demo.app/Info.plist", plistlib.dumps({
            "CFBundleIdentifier": bundle_id,
            "CFBundleDisplayName": "ERI Test IPA",
            "CFBundleExecutable": "Demo",
            "CFBundleShortVersionString": "1.0",
        }))
        archive.writestr("Payload/Demo.app/Demo", exe)
    return out.getvalue()


with tempfile.TemporaryDirectory(prefix="eri-auto-ipatool-") as root:
    root = Path(root)
    os.environ.update({
        "ERI_DB_PATH": str(root / "eri.sqlite3"),
        "ERI_UPLOAD_DIR": str(root / "uploads"),
        "ERI_ADMIN_KEY": "fake-test-admin-key-1234567890-1234567890",
        "ERI_SIGN_JOBS_DIR": str(root / "sign-jobs"),
        "ERI_SIGNED_IPA_DIR": str(root / "signed"),
        "ERI_IPATOOL_AUTO_DOWNLOAD": "1",
        "ERI_AUTO_IPA_BUNDLES": "com.eri.auto,com.eri.encrypted",
        "ERI_IPA_SOURCE_MANIFEST_URL": "",
    })
    fake_tool = root / "fake-ipatool"
    fake_tool.write_text(
        "#!" + sys.executable + "\n"
        "import sys, os, base64\n"
        "args = sys.argv\n"
        "target = args[args.index('--output') + 1]\n"
        "app_id = args[args.index('--app-id') + 1]\n"
        "key = 'ERI_TEST_PLAIN' if app_id == '135792468' else 'ERI_TEST_ENCRYPTED'\n"
        "open(target, 'wb').write(base64.b64decode(os.environ[key]))\n"
    )
    fake_tool.chmod(0o700)
    os.environ["ERI_IPATOOL_BIN"] = str(fake_tool)
    os.environ["ERI_TEST_PLAIN"] = base64.b64encode(
        make_ipa("com.eri.auto")).decode()
    os.environ["ERI_TEST_ENCRYPTED"] = base64.b64encode(
        make_ipa("com.eri.encrypted", encrypted=True)).decode()

    from fastapi.testclient import TestClient
    from app import app
    import decrypt_catalog

    def fake_store(endpoint, params):
        bundle = params.get("bundleId")
        app_id = 135792468 if bundle == "com.eri.auto" else 135792469
        return {"results": [{
            "kind": "software", "trackId": app_id,
            "trackName": "ERI Automatic Demo", "artistName": "ERI",
            "bundleId": bundle, "version": "1.0",
            "trackViewUrl": "https://apps.apple.com/vn/app/id" + str(app_id),
            "price": 0,
        }]}

    client = TestClient(app)
    account = client.post("/api/v1/auth/register", json={
        "username": "autotester", "display_name": "Auto tester",
        "password": "strong-test-password-999",
        "contact": "auto@example.invalid",
    })
    assert account.status_code == 201, account.text
    headers = {"Authorization": "Bearer " + account.json()["token"]}
    route = "/api/v1/tools/ipa/decrypt"
    assert client.get(route + "/engine").json()["ipatool_auto_download_enabled"] is True

    def run_request(bundle):
        started = client.post(route + "/requests",
                              json={"target": bundle, "country": "vn"},
                              headers=headers)
        assert started.status_code == 201, started.text
        job_id = started.json()["job_id"]
        last = started.json()
        for _ in range(200):
            last = client.get(route + "/requests/" + job_id, headers=headers).json()
            if last["state"] in ("completed", "failed"):
                return last
            time.sleep(0.05)
        raise AssertionError("Background ipatool test job never finished: " + str(last))

    with patch.object(decrypt_catalog, "_apple_json", side_effect=fake_store):
        good = run_request("com.eri.auto")
        assert good["state"] == "completed", good
        assert good["progress"] == 100
        assert good["result"]["download_path"]
        actual = client.get(good["result"]["download_path"])
        assert actual.status_code == 200, actual.status_code
        assert actual.content == base64.b64decode(os.environ["ERI_TEST_PLAIN"])

        bad = run_request("com.eri.encrypted")
        assert bad["state"] == "failed", bad
        assert "FairPlay" in bad["message"], bad
        assert bad["result"] is None
        assert client.get(route + "/vault").json()["total"] == 1

        unapproved = client.post(route + "/requests",
                                 json={"target": "com.eri.other", "country": "vn"},
                                 headers=headers).json()
        assert unapproved["state"] == "waiting_for_source"
        assert unapproved["auto_acquisition_available"] is False
        assert unapproved["progress"] is None

    print("ERI ipatool self-acquisition: success, FairPlay block and allowlist OK")
