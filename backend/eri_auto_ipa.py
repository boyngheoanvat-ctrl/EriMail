"""ERI automatic publisher bridge: approved source -> Actions -> R2 -> customer.

Only publisher-authorized sources in a reviewed catalog can be dispatched.
GitHub/R2 credentials never reach browsers. A completed IPA is downloadable
with an ephemeral signed URL only by the customer who requested it.
The App Store Search API remains metadata-only and does not decrypt FairPlay.
"""
import hmac
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from actions_ipa_worker import selected_source
from actions_r2_publish import MAX_R2_OBJECT, object_key
from eri_actions_bridge import _github

MANIFEST_PATH = Path(__file__).with_name("eri_actions_sources.json")
ID = re.compile(r"[a-f0-9]{32}\Z")
DISPATCH_COOLDOWN = 6 * 60 * 60


class WorkerCompletion(BaseModel):
    ok: bool
    private: bool
    source_id: str = Field(max_length=64)
    bundle_id: str = Field(max_length=255)
    version: str = Field(max_length=60)
    sha256: str = Field(min_length=64, max_length=64)
    bytes: int = Field(gt=0)
    bucket: str = Field(max_length=64)
    object_key: str = Field(max_length=1024)
    job_id: str | None = Field(default=None, max_length=32)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _configured():
    keys = (
        "ERI_GH_ACTIONS_REPO", "ERI_GH_ACTIONS_TOKEN",
        "ERI_R2_ACCOUNT_ID", "ERI_R2_BUCKET",
        "ERI_R2_ACCESS_KEY_ID", "ERI_R2_SECRET_ACCESS_KEY",
        "ERI_ACTIONS_CALLBACK_TOKEN",
    )
    return os.getenv("ERI_AUTO_ACTIONS_ENABLED", "").lower() == "true" and all(
        os.getenv(key, "").strip() for key in keys
    )


def _manifest_entry(bundle_id, version):
    try:
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return None
        matches = [
            item for item in items if isinstance(item, dict)
            and item.get("distribution_authorized") is True
            and isinstance(item.get("bundle_id"), str)
            and item["bundle_id"].casefold() == str(bundle_id).casefold()
            and item.get("version") == version
        ]
        if len(matches) != 1:
            return None
        return selected_source(MANIFEST_PATH, matches[0]["id"])
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError):
        return None


def setup(connection):
    with connection() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS eri_remote_ipa ("
            "bundle_id TEXT NOT NULL COLLATE NOCASE, version TEXT NOT NULL,"
            "source_id TEXT NOT NULL, sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL,"
            "object_key TEXT NOT NULL, created_at TEXT NOT NULL,"
            "PRIMARY KEY(bundle_id, version))"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS eri_actions_dispatched ("
            "bundle_id TEXT NOT NULL COLLATE NOCASE, version TEXT NOT NULL,"
            "source_id TEXT NOT NULL, dispatched_at INTEGER NOT NULL,"
            "PRIMARY KEY(bundle_id,version))"
        )


def remote_for(connection, bundle_id, version):
    with connection() as db:
        row = db.execute(
            "SELECT * FROM eri_remote_ipa WHERE bundle_id=? COLLATE NOCASE "
            "AND version=?", (bundle_id, version)
        ).fetchone()
    return dict(row) if row else None


def maybe_dispatch(connection, job_id, bundle_id, version):
    """One workflow per app/version per six hours, not one per user click."""
    if not _configured() or not ID.fullmatch(str(job_id)):
        return False
    if remote_for(connection, bundle_id, version):
        return True
    source = _manifest_entry(bundle_id, version)
    if not source:
        return False
    now = int(time.time())
    with connection() as db:
        previous = db.execute(
            "SELECT dispatched_at FROM eri_actions_dispatched "
            "WHERE bundle_id=? COLLATE NOCASE AND version=?",
            (bundle_id, version)
        ).fetchone()
        if previous and now - previous["dispatched_at"] < DISPATCH_COOLDOWN:
            db.execute(
                "UPDATE ipa_decrypt_requests SET state='processing',"
                "progress=NULL,message='GitHub Actions đang kiểm tra nguồn IPA',"
                "updated_at=? WHERE id=? AND state='waiting_for_source'",
                (_now(), job_id)
            )
            return True
        db.execute(
            "INSERT INTO eri_actions_dispatched(bundle_id,version,source_id,dispatched_at)"
            " VALUES(?,?,?,?) ON CONFLICT(bundle_id,version) DO UPDATE SET "
            "source_id=excluded.source_id,dispatched_at=excluded.dispatched_at",
            (bundle_id, version, source["id"], now)
        )
    try:
        _github("POST", "actions/workflows/process-ipa.yml/dispatches", {
            "ref": "main", "inputs": {
                "source_id": source["id"], "job_id": job_id
            }
        })
    except (HTTPException, OSError, ValueError):
        with connection() as db:
            db.execute(
                "DELETE FROM eri_actions_dispatched WHERE bundle_id=? "
                "COLLATE NOCASE AND version=? AND dispatched_at=?",
                (bundle_id, version, now)
            )
        return False
    with connection() as db:
        db.execute(
            "UPDATE ipa_decrypt_requests SET state='processing',progress=NULL,"
            "message='ERI đã chuyển nguồn IPA sang GitHub Actions',updated_at=? "
            "WHERE bundle_id=? COLLATE NOCASE AND version=? "
            "AND state='waiting_for_source'",
            (_now(), bundle_id, version)
        )
    return True


def _r2_client():
    import boto3
    from botocore.config import Config
    account = os.getenv("ERI_R2_ACCOUNT_ID", "").strip()
    return boto3.client(
        "s3",
        endpoint_url="https://" + account + ".r2.cloudflarestorage.com",
        region_name="auto",
        aws_access_key_id=os.getenv("ERI_R2_ACCESS_KEY_ID", ""),
        aws_secret_access_key=os.getenv("ERI_R2_SECRET_ACCESS_KEY", ""),
        config=Config(signature_version="s3v4",
                      retries={"max_attempts": 4, "mode": "standard"}),
    )


def install_auto_ipa(app, connection, require_customer):
    setup(connection)
    router = APIRouter()

    @router.get("/api/v1/tools/ipa/decrypt/auto-status")
    def public_status():
        try:
            items = json.loads(MANIFEST_PATH.read_text(encoding="utf-8")).get("items", [])
        except (OSError, ValueError, AttributeError):
            items = []
        count = sum(
            1 for x in items if isinstance(x, dict)
            and x.get("distribution_authorized") is True
        )
        return {
            "enabled": _configured(),
            "approved_sources": count,
            "provider": "ERI GitHub Actions + private Cloudflare R2",
            "requires_authorized_ipa_source": True,
            "fairplay_decryption_supported": False,
        }

    @router.post("/api/v1/worker/ipa/complete")
    def completed(
        body: WorkerCompletion,
        x_eri_worker_token: str | None = Header(default=None),
    ):
        expected_token = os.getenv("ERI_ACTIONS_CALLBACK_TOKEN", "").strip()
        if not expected_token or not x_eri_worker_token or not hmac.compare_digest(
                expected_token, x_eri_worker_token):
            raise HTTPException(403, "Worker không được cấp quyền")
        if not body.ok or not body.private or body.bytes > MAX_R2_OBJECT:
            raise HTTPException(422, "Kết quả IPA không hợp lệ")
        source = _manifest_entry(body.bundle_id, body.version)
        if (not source or source["id"] != body.source_id
                or source["sha256"].lower() != body.sha256.lower()):
            raise HTTPException(422, "IPA không trùng nguồn đã được ERI phê duyệt")
        expected_key = object_key({
            "bundle_id": body.bundle_id,
            "version": body.version,
            "sha256": body.sha256.lower(),
        })
        if body.object_key != expected_key or body.bucket != os.getenv("ERI_R2_BUCKET", ""):
            raise HTTPException(422, "R2 object key hoặc bucket không hợp lệ")
        if body.job_id and not ID.fullmatch(body.job_id):
            raise HTTPException(422, "Mã tác vụ không hợp lệ")
        try:
            head = _r2_client().head_object(Bucket=body.bucket, Key=expected_key)
        except Exception as exc:
            raise HTTPException(503, "Không xác minh được file trong R2") from exc
        if (head.get("ContentLength") != body.bytes
                or head.get("Metadata", {}).get("sha256") != body.sha256.lower()):
            raise HTTPException(422, "Tệp R2 không trùng SHA-256 và kích thước")
        with connection() as db:
            db.execute(
                "INSERT INTO eri_remote_ipa "
                "(bundle_id,version,source_id,sha256,size_bytes,object_key,created_at)"
                " VALUES(?,?,?,?,?,?,?) ON CONFLICT(bundle_id,version)"
                " DO UPDATE SET source_id=excluded.source_id,sha256=excluded.sha256,"
                "size_bytes=excluded.size_bytes,object_key=excluded.object_key,"
                "created_at=excluded.created_at",
                (body.bundle_id, body.version, body.source_id, body.sha256.lower(),
                 body.bytes, expected_key, _now())
            )
            db.execute(
                "UPDATE ipa_decrypt_requests SET state='completed',progress=100,"
                "message='IPA đã được xác minh và lưu vào R2',updated_at=?"
                " WHERE bundle_id=? COLLATE NOCASE AND version=?",
                (_now(), body.bundle_id, body.version)
            )
        return {"ok": True, "registered": True}

    @router.get("/api/v1/tools/ipa/decrypt/remote-url/{job_id}")
    def signed_download(job_id: str, customer=Depends(require_customer)):
        if not ID.fullmatch(job_id):
            raise HTTPException(404, "Không tìm thấy IPA")
        with connection() as db:
            row = db.execute(
                "SELECT r.bundle_id,r.version,v.object_key,v.sha256,v.size_bytes "
                "FROM ipa_decrypt_requests r JOIN eri_remote_ipa v "
                "ON v.bundle_id=r.bundle_id COLLATE NOCASE AND v.version=r.version "
                "WHERE r.id=? AND r.customer_id=?",
                (job_id, customer["id"])
            ).fetchone()
        if not row:
            raise HTTPException(404, "IPA chưa sẵn sàng cho tài khoản này")
        if not _configured():
            raise HTTPException(503, "Kho R2 chưa được cấu hình đầy đủ")
        filename = "ERI-" + row["bundle_id"] + "-" + row["version"] + ".ipa"
        try:
            url = _r2_client().generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": os.environ["ERI_R2_BUCKET"],
                    "Key": row["object_key"],
                    "ResponseContentType": "application/octet-stream",
                    "ResponseContentDisposition": 'attachment; filename="' + filename + '"',
                }, ExpiresIn=900,
            )
        except Exception as exc:
            raise HTTPException(503, "Không tạo được link tải R2") from exc
        parts = urlsplit(url)
        host = os.environ["ERI_R2_ACCOUNT_ID"].lower() + ".r2.cloudflarestorage.com"
        if parts.scheme != "https" or parts.hostname != host:
            raise HTTPException(502, "R2 trả đường dẫn không đáng tin cậy")
        return {
            "ready": True,
            "download_url": url,
            "expires_in_seconds": 900,
            "size_bytes": row["size_bytes"],
        }

    app.include_router(router)
    return router
