"""Admin-only read-only ERI IPA signing overview.

Signing material stays private in the existing per-customer signing job folders.
This endpoint intentionally never returns P12, profiles, passwords, file paths,
download tokens, or signed download URLs.
"""
import importlib.util
import json
import os
import re
import time
from collections import Counter

from fastapi import APIRouter, Depends, Query

from signing_jobs import ROOT, LOCK, _purge_old

_JOB_NAME = re.compile(r"^[0-9a-f]{32}$")
_STATES = {"uploading", "processing", "completed", "failed"}
_MAX_JOBS = 1000


def register_admin_ipa_studio(app, connection, require_admin):
    router = APIRouter()

    @router.get("/api/v1/admin/ipa-studio/signing-jobs",
                dependencies=[Depends(require_admin)])
    def list_admin_signing_jobs(limit: int = Query(default=50, ge=1, le=100)):
        records = []
        with LOCK:
            _purge_old()
            if ROOT.is_dir():
                for folder in ROOT.iterdir():
                    if len(records) >= _MAX_JOBS:
                        break
                    if not folder.is_dir() or not _JOB_NAME.fullmatch(folder.name):
                        continue
                    try:
                        if (folder / "state.json").stat().st_size > 32768:
                            continue
                        data = json.loads((folder / "state.json").read_text(encoding="utf-8"))
                    except (OSError, ValueError, TypeError):
                        continue
                    if data.get("job_id") != folder.name:
                        continue
                    state = str(data.get("state", "unknown"))
                    if state not in _STATES:
                        state = "failed"
                    records.append({
                        "job_id": folder.name,
                        "customer_id": int(data.get("owner", 0)),
                        "filename": str(data.get("filename", "IPA"))[:240],
                        "state": state,
                        "size_bytes": max(0, int(data.get("size_bytes", 0))),
                        "created_at": float(data.get("created", 0)),
                        "progress": 100 if state == "completed" else (
                            0 if state == "failed" else None),
                    })
        records.sort(key=lambda row: row["created_at"], reverse=True)
        counts = Counter(row["state"] for row in records)
        # Only account names are shown. Do not expose the user's signing artifacts,
        # credentials, private storage paths, access tokens or account contacts.
        ids = sorted({row["customer_id"] for row in records[:limit] if row["customer_id"] > 0})
        names = {}
        if ids:
            with connection() as db:
                keys = ",".join("?" for _ in ids)
                for entry in db.execute("SELECT id,username FROM customers WHERE id IN (" + keys + ")", ids):
                    names[int(entry["id"])] = entry["username"]
        public = []
        for row in records[:limit]:
            public.append({
                "job_id": row["job_id"],
                "requester": names.get(row["customer_id"], "Không xác định"),
                "filename": row["filename"],
                "state": row["state"],
                "size_bytes": row["size_bytes"],
                "created_at": row["created_at"],
            })
        return {
            "items": public,
            "total": len(records),
            "counts": dict(counts),
            "signing_available": importlib.util.find_spec("ipasign") is not None,
            "retention": "Tệp tác vụ và link IPA ký có thời hạn; máy chủ có thể xóa khi khởi động lại.",
        }

    app.include_router(router)
