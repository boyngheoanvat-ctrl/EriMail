"""ERI's independent, authorized-IPA processing worker.

Apple's public Search API gives metadata, not a decrypted IPA. This worker
downloads *pre-approved* IPA sources from operator-controlled hosts, checks the
SHA-256 and actual Mach-O encryption state, and publishes only verified files.
It never calls another decrypt service or claims to remove FairPlay DRM.
"""
import hashlib
import ipaddress
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException
from ipa_tools import _inspect_archive

MAX_BYTES = int(os.getenv("ERI_PIPELINE_MAX_IPA_BYTES", str(512 * 1024 * 1024)))
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="eri-ipa")
_active = set()
_guard = threading.Lock()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _hosts():
    return {host.strip().lower().rstrip(".") for host in
            os.getenv("ERI_IPA_SOURCE_HOSTS", "").split(",") if host.strip()}


def validate_url(url):
    """Prevent SSRF; including redirect hops, DNS names must be explicitly allowed."""
    allowed = _hosts()
    parts = urlsplit(str(url))
    host = (parts.hostname or "").lower().rstrip(".")
    if not allowed:
        raise ValueError("Chưa cấu hình ERI_IPA_SOURCE_HOSTS trên máy chủ")
    if (parts.scheme != "https" or host not in allowed or parts.username or
            parts.password or parts.fragment or parts.port not in (None, 443)):
        raise ValueError("Nguồn IPA phải là HTTPS thuộc tên miền ERI đã cho phép")
    try:
        ipaddress.ip_address(host)
        raise ValueError("Không dùng địa chỉ IP làm nguồn IPA")
    except ValueError as exc:
        if str(exc) == "Không dùng địa chỉ IP làm nguồn IPA":
            raise
    if len(url) > 1800:
        raise ValueError("Đường dẫn nguồn IPA quá dài")
    return url


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def setup(connection):
    with connection() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS ipa_decrypt_sources ("
            "bundle_id TEXT NOT NULL, version TEXT NOT NULL,"
            "name TEXT NOT NULL, url TEXT NOT NULL, sha256 TEXT NOT NULL,"
            "created_at TEXT NOT NULL,"
            "PRIMARY KEY(bundle_id,version))"
        )


def put_source(connection, *, name, bundle_id, version, url, sha256):
    validate_url(url)
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,255}", bundle_id):
        raise ValueError("Bundle ID không hợp lệ")
    if not re.fullmatch(r"[A-Za-z0-9._+-]{1,60}", version):
        raise ValueError("Phiên bản không hợp lệ")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
        raise ValueError("SHA-256 phải đủ 64 ký tự hex")
    if not 1 <= len(name.strip()) <= 160:
        raise ValueError("Tên ứng dụng không hợp lệ")
    with connection() as db:
        db.execute(
            "INSERT INTO ipa_decrypt_sources(bundle_id,version,name,url,sha256,created_at)"
            " VALUES(?,?,?,?,?,?) ON CONFLICT(bundle_id,version) DO UPDATE SET"
            " name=excluded.name,url=excluded.url,sha256=excluded.sha256,"
            " created_at=excluded.created_at",
            (bundle_id, version, name.strip(), url, sha256.lower(), _now())
        )


def _status(connection, job_id, state, progress, message):
    with connection() as db:
        db.execute(
            "UPDATE ipa_decrypt_requests SET state=?,progress=?,message=?,updated_at=?"
            " WHERE id=?",
            (state, progress, message[:240], _now(), job_id)
        )


def schedule(connection, upload_dir, job_id):
    """Only schedule jobs with an approved source; avoid a fake waiting queue."""
    with connection() as db:
        row = db.execute(
            "SELECT r.id,r.state FROM ipa_decrypt_requests r "
            "JOIN ipa_decrypt_sources s ON s.bundle_id=r.bundle_id "
            "AND s.version=r.version WHERE r.id=?", (job_id,)
        ).fetchone()
    if row is None or row["state"] == "completed":
        return False
    with _guard:
        if job_id in _active:
            return True
        _active.add(job_id)
    try:
        _executor.submit(_run, connection, Path(upload_dir), job_id)
        return True
    except RuntimeError:
        with _guard:
            _active.discard(job_id)
        return False


def schedule_matching(connection, upload_dir, bundle_id, version):
    with connection() as db:
        rows = db.execute(
            "SELECT id FROM ipa_decrypt_requests WHERE bundle_id=? AND version=?"
            " AND state!='completed' LIMIT 100",
            (bundle_id, version)
        ).fetchall()
    for row in rows:
        schedule(connection, upload_dir, row["id"])


def _download(connection, job_id, url, target, expected_sha):
    opener = urllib.request.build_opener(_SafeRedirect())
    req = urllib.request.Request(
        validate_url(url), headers={"Accept": "application/octet-stream, application/zip",
                                    "User-Agent": "ERI-IPA-Worker/1.0"})
    checksum = hashlib.sha256()
    count = 0
    last_update = 0
    with opener.open(req, timeout=30) as response:
        length = response.headers.get("Content-Length", "")
        total = int(length) if length.isdecimal() else 0
        if total > MAX_BYTES:
            raise ValueError("Tệp vượt dung lượng máy chủ cho phép")
        with target.open("xb") as out:
            os.chmod(target, 0o600)
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                count += len(chunk)
                if count > MAX_BYTES:
                    raise ValueError("Tệp vượt dung lượng máy chủ cho phép")
                out.write(chunk)
                checksum.update(chunk)
                now = time.monotonic()
                if now - last_update >= 0.4:
                    pct = min(90, int(count / total * 90)) if total else None
                    _status(connection, job_id, "processing", pct,
                            "Đang nhận IPA: %.1f MB" % (count / 1_000_000))
                    last_update = now
    if count == 0 or (total and count != total):
        raise ValueError("Nguồn IPA tải chưa hoàn chỉnh")
    if checksum.hexdigest() != expected_sha:
        raise ValueError("SHA-256 không trùng với nguồn đã đăng ký")
    return count, checksum.hexdigest()


def _run(connection, upload_dir, job_id):
    partial = None
    try:
        with connection() as db:
            row = db.execute(
                "SELECT r.id,r.app_id,r.country,r.bundle_id,r.version,"
                "s.name,s.url,s.sha256 FROM ipa_decrypt_requests r "
                "JOIN ipa_decrypt_sources s ON s.bundle_id=r.bundle_id "
                "AND s.version=r.version WHERE r.id=?", (job_id,)
            ).fetchone()
        if row is None:
            return
        upload_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        partial = upload_dir / ("." + secrets.token_hex(16) + ".ipa.partial")
        _status(connection, job_id, "processing", 0, "Đang kết nối nguồn IPA ERI")
        size, digest = _download(connection, job_id, row["url"], partial, row["sha256"])
        _status(connection, job_id, "processing", 92, "Đang kiểm tra cấu trúc IPA")
        inspection = _inspect_archive(partial)
        meta = inspection.get("app") or {}
        if inspection.get("zip_password_protected"):
            raise ValueError("IPA có mật khẩu ZIP; cần nguồn không khóa")
        if inspection.get("fairplay_encrypted") is not False:
            raise ValueError("IPA còn mã hóa FairPlay hoặc không xác minh được Mach-O")
        if meta.get("bundle_id", "").casefold() != row["bundle_id"].casefold():
            raise ValueError("Bundle ID bên trong IPA không trùng yêu cầu")
        if meta.get("version") != row["version"]:
            raise ValueError("Phiên bản IPA không trùng yêu cầu")
        _status(connection, job_id, "processing", 98, "Đã xác minh, đang đưa IPA vào kho")
        storage_name = secrets.token_hex(16) + ".ipa"
        output = upload_dir / storage_name
        os.replace(partial, output)
        partial = None
        try:
            with connection() as db:
                # Reuse the same authorized file for all customers requesting this version.
                existing = db.execute(
                    "SELECT i.id FROM ipa i JOIN private_product_uploads p "
                    "ON p.product_id=i.id AND p.kind='ipa' "
                    "WHERE i.bundle_id=? AND i.version=? AND i.authorized=1 "
                    "AND i.price_vnd=0 LIMIT 1",
                    (row["bundle_id"], row["version"])
                ).fetchone()
                if not existing:
                    store_link = ("https://apps.apple.com/" + row["country"].lower()
                                  + "/app/id" + str(row["app_id"]))
                    cur = db.execute(
                        "INSERT INTO ipa(name,version,bundle_id,download_url,sha256,"
                        "authorized,price_vnd,description,platform)"
                        " VALUES(?,?,?,?,?,1,0,'','ipa')",
                        (row["name"], row["version"], row["bundle_id"], store_link, digest)
                    )
                    db.execute(
                        "INSERT INTO private_product_uploads"
                        "(id,kind,product_id,original_name,storage_name,size_bytes,sha256,created_at)"
                        " VALUES(?,'ipa',?,?,?,?,?,?)",
                        (secrets.token_hex(16), cur.lastrowid,
                         re.sub(r"[^A-Za-z0-9._-]", "_", row["name"])[:70]
                         + "_v" + row["version"] + ".ipa",
                         storage_name, size, digest, _now())
                    )
            if existing:
                output.unlink(missing_ok=True)
            _status(connection, job_id, "completed", 100, "Hoàn tất: IPA đã kiểm tra và có thể tải")
        except Exception:
            output.unlink(missing_ok=True)
            raise
    except (ValueError, HTTPException, OSError, urllib.error.URLError) as exc:
        message = exc.detail if isinstance(exc, HTTPException) else str(exc)
        _status(connection, job_id, "failed", None, "Lỗi xử lý IPA: " + message)
    except Exception:
        _status(connection, job_id, "failed", None, "Lỗi nội bộ khi xử lý IPA")
    finally:
        if partial is not None:
            partial.unlink(missing_ok=True)
        with _guard:
            _active.discard(job_id)
