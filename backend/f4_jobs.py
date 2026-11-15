"""Authorized F4TEAM job bridge for ERI Store.

This bridges a provider's documented job API; it does not decrypt Apple's DRM.
Nothing runs unless the operator supplies an explicitly authorized server-side
F4 API token. Never place provider tokens, cookies, or CSRF secrets in the IPA
or browser. F4's public browser endpoint may reject machine-to-machine tokens;
that rejection is reported, not bypassed.
"""
import json
import os
import re
import secrets
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

F4_ORIGIN = "https://decrypt.160-191-242-70.sslip.io"
F4_HOSTS = {"decrypt.160-191-242-70.sslip.io", "160-191-242-70.sslip.io"}
ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{8,128}\Z")
LOCAL_ID = re.compile(r"[a-f0-9]{32}\Z")
CURRENT_DOWNLOAD = re.compile(r"/api/decrypt/download/[a-fA-F0-9]{32}\Z")
LEGACY_DOWNLOAD = re.compile(
    r"/dl_dec/[A-Za-z0-9_-]{8,128}/[0-9]{8,15}/[A-Za-z0-9._%-]+[.]ipa\Z"
)


class F4JobInput(BaseModel):
    target: str = Field(min_length=2, max_length=500)
    country: str = Field(default="auto", max_length=4)
    version: str = Field(default="", max_length=60)


def _configured():
    # The F4 operator must authorize this token for POST /api/decrypt/start.
    return bool(os.getenv("ERI_F4_API_TOKEN", "").strip())


def _download_url(value):
    """Only a provider-owned, fixed-route IPA reference is accepted."""
    if not isinstance(value, str) or len(value) > 800:
        return None
    if CURRENT_DOWNLOAD.fullmatch(value) or LEGACY_DOWNLOAD.fullmatch(value):
        return F4_ORIGIN + value
    url = urlsplit(value)
    if (url.scheme == "https" and url.hostname in F4_HOSTS
            and url.port in (None, 443) and not url.username
            and not url.password and not url.query and not url.fragment
            and (CURRENT_DOWNLOAD.fullmatch(url.path)
                 or LEGACY_DOWNLOAD.fullmatch(url.path))):
        return value
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def _call_f4(path, payload=None):
    if not _configured():
        raise HTTPException(503, "ERI chưa cấu hình quyền gọi tác vụ F4TEAM")
    if not (path == "/api/decrypt/start"
            or re.fullmatch(r"/api/decrypt/job/[A-Za-z0-9_-]{8,128}", path)):
        raise HTTPException(400, "Đường dẫn tác vụ F4 không hợp lệ")

    token = os.getenv("ERI_F4_API_TOKEN", "").strip()
    headers = {
        "Accept": "application/json",
        "Authorization": "Bearer " + token,
        "User-Agent": "ERIStore-Authorized-F4Bridge/1.0",
    }
    csrf = os.getenv("ERI_F4_CSRF_TOKEN", "").strip()
    if csrf:
        headers["X-CSRF-TOKEN"] = csrf
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(
        F4_ORIGIN + path, data=body, headers=headers,
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.build_opener(_NoRedirect()).open(req, timeout=12) as response:
            if response.status != 200:
                raise HTTPException(502, "F4TEAM không trả phản hồi hợp lệ")
            raw = response.read(128 * 1024 + 1)
        if len(raw) > 128 * 1024:
            raise HTTPException(502, "Phản hồi F4 vượt giới hạn")
        data = json.loads(raw)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise HTTPException(
                503, "F4TEAM từ chối quyền tạo tác vụ: cần quyền API/CSRF hợp lệ từ F4"
            ) from exc
        if exc.code == 404:
            raise HTTPException(503, "F4TEAM chưa cung cấp endpoint tác vụ cho ERI") from exc
        raise HTTPException(502, "F4TEAM trả HTTP " + str(exc.code)) from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, UnicodeError) as exc:
        raise HTTPException(503, "Không kết nối được API tác vụ F4TEAM") from exc
    if not isinstance(data, dict):
        raise HTTPException(502, "Phản hồi tác vụ F4 không đúng định dạng")
    if data.get("ok") is False:
        # Do not propagate arbitrary text or sensitive provider responses.
        raise HTTPException(502, "F4TEAM không chấp nhận yêu cầu xử lý IPA")
    return data


def _provider_status(data):
    """No invented percentages and no done state without a validated URL."""
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    state = str(data.get("status") or data.get("state") or result.get("status") or "").lower()
    candidate = (
        result.get("dl_url") or result.get("download_url")
        or data.get("dl_url") or data.get("download_url")
    )
    if not candidate and isinstance(data.get("vault_cached"), dict):
        candidate = data["vault_cached"].get("dl_url")
    url = _download_url(candidate)
    message = str(data.get("stage_text") or data.get("message") or "")[:180]
    message = "".join(ch for ch in message if ch.isprintable())
    if url and (state in ("done", "completed", "success", "succeeded")
                or data.get("cached") is True):
        return ("completed", 100, url, "F4TEAM trả về link IPA (ERI chưa xác minh file)")
    if state in ("failed", "error", "cancelled", "canceled"):
        return ("failed", None, None, "F4TEAM thông báo xử lý thất bại")
    if state in ("done", "completed", "success", "succeeded") and not url:
        return ("failed", None, None, "F4TEAM báo hoàn tất nhưng không có link IPA hợp lệ")
    raw_progress = data.get("progress", data.get("pct"))
    measured = None
    if type(raw_progress) in (int, float) and 0 <= raw_progress < 100:
        measured = int(raw_progress)
    return ("processing", measured, None, message or "Đang chờ trạng thái tác vụ từ F4TEAM")


def _public(row):
    return {
        "job_id": row["id"], "name": row["name"], "bundle_id": row["bundle_id"],
        "version": row["version"], "country": row["country"],
        "state": row["state"], "progress": row["progress"],
        "message": row["message"],
        "download_url": row["download_url"] if row["state"] == "completed" else None,
        "source": "F4TEAM", "verified_by_eri": False,
        "created_at": row["created_at"],
    }


def install_f4_jobs(app, connection, require_customer, search_apps):
    with connection() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS f4_customer_jobs ("
            "id TEXT PRIMARY KEY, customer_id INTEGER NOT NULL,"
            "name TEXT NOT NULL, bundle_id TEXT NOT NULL,"
            "version TEXT NOT NULL, country TEXT NOT NULL,"
            "remote_id TEXT NOT NULL, state TEXT NOT NULL,"
            "progress INTEGER, message TEXT NOT NULL DEFAULT '',"
            "download_url TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_f4_customer_jobs_owner "
            "ON f4_customer_jobs(customer_id, created_at)"
        )
    router = APIRouter(prefix="/api/v1/tools/ipa/decrypt/f4")

    @router.get("/capabilities")
    def capabilities():
        return {
            "enabled": _configured(),
            "start_supported": _configured(),
            "notice": ("F4 API token đã cấu hình; kết quả còn phụ thuộc quyền phía F4."
                       if _configured() else
                       "Chưa có API token F4 được cấp quyền; chỉ lưu yêu cầu vào kho ERI."),
        }

    @router.post("/start", status_code=201)
    def start_job(body: F4JobInput, customer=Depends(require_customer)):
        if not _configured():
            raise HTTPException(503, "Chưa cấu hình API token tác vụ F4TEAM trên máy chủ ERI")
        apps = search_apps(body.target, body.country, limit=1)
        if not apps:
            raise HTTPException(404, "Không tìm thấy ứng dụng tương ứng trên App Store")
        app_info = apps[0]
        country = app_info["country"].lower()
        version = body.version or app_info["version"]
        if not re.fullmatch(r"[A-Za-z0-9._+-]{1,60}", version):
            raise HTTPException(422, "Phiên bản không hợp lệ")
        with connection() as db:
            prior = db.execute(
                "SELECT * FROM f4_customer_jobs WHERE customer_id=? AND bundle_id=? "
                "AND version=? ORDER BY created_at DESC LIMIT 1",
                (customer["id"], app_info["bundle_id"], version)
            ).fetchone()
            if prior is not None and prior["state"] in ("processing", "completed"):
                return {**_public(prior), "reused": True}
            if db.execute(
                "SELECT COUNT(*) FROM f4_customer_jobs WHERE customer_id=?",
                (customer["id"],)
            ).fetchone()[0] >= 30:
                raise HTTPException(429, "Tài khoản đã tạo tối đa 30 tác vụ F4")

        data = _call_f4("/api/decrypt/start", {
            "target": app_info["app_store_url"], "country": country,
            "mode": "ios", "strip_account_data": True,
            "force": False, "version": version, "external_version_id": "",
        })
        remote = data.get("job_id") or data.get("id")
        if not remote and isinstance(data.get("job"), dict):
            remote = data["job"].get("job_id")
        state, progress, download, message = _provider_status(data)
        if not remote and state == "completed":
            remote = "cached_" + secrets.token_hex(16)
        if not isinstance(remote, str) or not ID_PATTERN.fullmatch(remote):
            raise HTTPException(502, "F4TEAM chưa trả mã tác vụ hợp lệ")
        now = datetime.now(timezone.utc).isoformat()
        local_id = secrets.token_hex(16)
        with connection() as db:
            db.execute(
                "INSERT INTO f4_customer_jobs "
                "(id,customer_id,name,bundle_id,version,country,remote_id,"
                "state,progress,message,download_url,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (local_id, customer["id"], app_info["name"], app_info["bundle_id"],
                 version, country.upper(), remote, state, progress, message,
                 download, now, now)
            )
            row = db.execute("SELECT * FROM f4_customer_jobs WHERE id=?",
                             (local_id,)).fetchone()
        return _public(row)

    @router.get("/jobs")
    def list_jobs(customer=Depends(require_customer)):
        with connection() as db:
            rows = db.execute(
                "SELECT * FROM f4_customer_jobs WHERE customer_id=? "
                "ORDER BY created_at DESC LIMIT 30", (customer["id"],)
            ).fetchall()
        return {"items": [_public(row) for row in rows], "total": len(rows)}

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str, customer=Depends(require_customer)):
        if not LOCAL_ID.fullmatch(job_id):
            raise HTTPException(404, "Không tìm thấy tác vụ")
        with connection() as db:
            row = db.execute(
                "SELECT * FROM f4_customer_jobs WHERE id=? AND customer_id=?",
                (job_id, customer["id"])
            ).fetchone()
        if row is None:
            raise HTTPException(404, "Không tìm thấy tác vụ")
        if row["state"] in ("completed", "failed"):
            return _public(row)
        data = _call_f4("/api/decrypt/job/" + row["remote_id"])
        state, progress, download, message = _provider_status(data)
        with connection() as db:
            db.execute(
                "UPDATE f4_customer_jobs SET state=?,progress=?,message=?,"
                "download_url=?,updated_at=? WHERE id=? AND customer_id=?",
                (state, progress, message, download,
                 datetime.now(timezone.utc).isoformat(), job_id, customer["id"])
            )
            updated = db.execute(
                "SELECT * FROM f4_customer_jobs WHERE id=? AND customer_id=?",
                (job_id, customer["id"])
            ).fetchone()
        return _public(updated)

    app.include_router(router)
    return router
