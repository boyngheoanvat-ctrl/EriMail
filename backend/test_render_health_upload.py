"""Regression: finishing a large upload must not freeze Render /health.

No actual production upload, credentials, files, or cloud storage are touched.
The long-running finalizer is replaced with a sleeping test worker to reproduce
the old async event-loop starvation while keeping the test deterministic.
"""
import asyncio
import os
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

with tempfile.TemporaryDirectory(prefix="eri-health-finalize-") as directory:
    os.environ["ERI_DB_PATH"] = str(Path(directory) / "customer.sqlite3")
    os.environ["ERI_UPLOAD_DIR"] = str(Path(directory) / "uploads")
    os.environ["ERI_ADMIN_KEY"] = "ci-health-check-only-key-0123456789-abcdefgh"
    os.environ["ERI_VAULT_MODE"] = "off"
    import httpx
    import app as er

    started = threading.Event()
    release = threading.Event()

    def simulated_slow_ipa_verification(upload_id):
        assert upload_id == "a" * 32
        started.set()
        assert release.wait(timeout=4), "Test timed out waiting to finish"
        return {"id": 11, "name": "Test IPA", "uploaded": True}

    async def main():
        transport = httpx.ASGITransport(app=er.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.object(er, "_eri_finish_chunk_upload_blocking",
                              side_effect=simulated_slow_ipa_verification):
                finish = asyncio.create_task(client.post(
                    "/api/v1/admin/upload-sessions/" + "a" * 32 + "/complete",
                    headers={"X-ERI-Admin-Key": os.environ["ERI_ADMIN_KEY"]},
                ))
                try:
                    assert await asyncio.wait_for(
                        asyncio.to_thread(started.wait, 1.5), timeout=2
                    ), "Finalizer did not start"
                    # Under the old implementation, the event loop froze
                    # during hashing and blocked this health request.
                    health = await asyncio.wait_for(client.get("/health"), timeout=0.75)
                    assert health.status_code == 200, health.text
                    catalog = await asyncio.wait_for(client.get("/api/v1/mods"), timeout=0.75)
                    assert catalog.status_code == 200, catalog.text
                finally:
                    release.set()
                done = await asyncio.wait_for(finish, timeout=2)
                assert done.status_code == 201, done.text
                assert done.json()["uploaded"]
        print("Render health and product API stay responsive during slow IPA finalize: PASS")

    asyncio.run(main())
