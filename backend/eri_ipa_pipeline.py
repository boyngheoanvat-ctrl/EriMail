"""ERI's independent, authorized-IPA processing worker.

Apple's public Search API gives metadata, not a decrypted IPA. This worker
downloads *pre-approved* IPA sources from operator-controlled hosts, checks the
SHA-256 and actual Mach-O encryption state, and publishes only verified files.
It never calls another decrypt service or claims to remove FairPlay DRM.
"""
import hashlib
import json
import ipaddress
import os
import re
import secrets
import shutil
import subprocess
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
_feed_lock = threading.Lock()
_feed_last_check = None
FEED_MAX_BYTES = 1024 * 1024
FEED_MIN_SECONDS = 60


def auto_download_enabled():
    """Enable on-demand App Store acquisition ONLY for explicitly permitted apps.

    ipatool authenticates with Apple's store; it does NOT remove FairPlay.
    Credentials belong in a dedicated, persistent private worker profile.
    """
    return os.getenv("ERI_IPATOOL_AUTO_DOWNLOAD", "").strip() == "1"


def can_auto_acquire(bundle_id):
    if not auto_download_enabled():
        return False
    permitted = {item.strip().casefold() for item in
                 os.getenv("ERI_AUTO_IPA_BUNDLES", "").split(",") if item.strip()}
    return (bundle_id or "").casefold() in permitted and bool(
        shutil.which(os.getenv("ERI_IPATOOL_BIN", "ipatool")))


def feed_configured():
    return bool(os.getenv("ERI_IPA_SOURCE_MANIFEST_URL", "").strip())


def sync_source_feed(connection, upload_dir=None, force=False):
    """Discover ERI-owned sources over allowlisted HTTPS. Never a decrypt API.

    Only metadata with explicit distribution authorization is trusted.
    """
    global _feed_last_check
    url = os.getenv("ERI_IPA_SOURCE_MANIFEST_URL", "").strip()
    if not url:
        return {"configured": False, "registered": 0}
    with _feed_lock:
        now = time.monotonic()
        if (not force and _feed_last_check is not None
                and now - _feed_last_check < FEED_MIN_SECONDS):
            return {"configured": True, "cached": True, "registered": 0}
        validate_url(url)
        opener = urllib.request.build_opener(_SafeRedirect())
        request = urllib.request.Request(
            url, headers={"Accept": "application/json",
                          "User-Agent": "ERI-IPA-Worker/1.0"})
        with opener.open(request, timeout=12) as response:
            raw = response.read(FEED_MAX_BYTES + 1)
        if len(raw) > FEED_MAX_BYTES:
            raise ValueError("Danh mục nguồn IPA ERI vượt 1 MiB")
        feed = json.loads(raw.decode("utf-8"))
        sources = feed.get("items") if isinstance(feed, dict) else None
        if not isinstance(sources, list) or len(sources) > 200:
            raise ValueError("Danh mục IPA ERI không hợp lệ (tối đa 200 nguồn)")
        registered = 0
        for source in sources:
            if not isinstance(source, dict) or source.get("distribution_authorized") is not True:
                continue
            try:
                put_source(
                    connection, name=source["name"], bundle_id=source["bundle_id"],
                    version=source["version"], url=source["url"], sha256=source["sha256"]
                )
                registered += 1
                if upload_dir is not None:
                    schedule_matching(connection, upload_dir, source["bundle_id"], source["version"])
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                # Publish only authorized, structurally valid manifest entries.
                # Keep the reason in logs for debugging, without printing source URLs.
                import logging
                logging.warning("ERI source feed skipped item: %s (%s)",
                                type(exc).__name__, str(exc)[:120])
                continue
        # A failed first fetch must not poison the cache for 60 seconds.
        _feed_last_check = time.monotonic()
        return {"configured": True, "registered": registered}




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
    """Schedule either approved ERI-hosted IPA or permitted authenticated ipatool."""
    with connection() as db:
        row = db.execute(
            "SELECT r.id,r.state,r.bundle_id,s.url AS source_url "
            "FROM ipa_decrypt_requests r LEFT JOIN ipa_decrypt_sources s "
            "ON s.bundle_id=r.bundle_id COLLATE NOCASE AND s.version=r.version "
            "WHERE r.id=?", (job_id,)
        ).fetchone()
    if row is None or row["state"] == "completed":
        return False
    if not row["source_url"] and not can_auto_acquire(row["bundle_id"]):
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
            "SELECT id FROM ipa_decrypt_requests WHERE bundle_id=? COLLATE NOCASE AND version=?"
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


def _acquire_with_ipatool(connection, job_id, app_id, target, expected_bytes=0):
    """Fetch an app already licensed to the worker Apple Account.

    This is acquisition, not decryption. Do not add --purchase (no accidental
    purchase/consent). Authentication is provisioned outside the website.
    """
    binary = os.getenv("ERI_IPATOOL_BIN", "ipatool")
    executable = shutil.which(binary)
    if not executable:
        raise ValueError("Máy chủ ERI chưa cài ipatool")
    if not isinstance(app_id, int) or app_id <= 0:
        raise ValueError("App Store ID không hợp lệ")
    command = [executable, "--non-interactive", "download", "--app-id",
               str(app_id), "--platform", "iphone", "--output", str(target)]
    timeout = min(3600, max(60, int(os.getenv("ERI_IPATOOL_TIMEOUT_SEC", "900"))))
    _status(connection, job_id, "processing", None,
            "ERI đang tải IPA bằng tài khoản App Store đã cấp quyền")
    proc = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            close_fds=True)
    try:
        deadline = time.monotonic() + timeout
        last_bytes = -1
        while proc.poll() is None:
            if time.monotonic() >= deadline:
                raise ValueError("Quá thời gian tải IPA từ App Store")
            current_bytes = target.stat().st_size if target.exists() else 0
            if current_bytes > MAX_BYTES:
                raise ValueError("IPA vượt dung lượng tối đa cho phép")
            if current_bytes and current_bytes != last_bytes:
                # Apple's public package size can differ from actual download.
                # Percentage is an estimate based on bytes physically written.
                if expected_bytes and expected_bytes > 0:
                    pct = min(89, int(current_bytes * 90 / expected_bytes))
                    note = "Đã tải %.1f / %.1f MB (ước tính)" % (
                        current_bytes / 1e6, expected_bytes / 1e6)
                else:
                    pct = None
                    note = "Đã nhận %.1f MB từ App Store" % (current_bytes / 1e6)
                _status(connection, job_id, "processing", pct, note)
                last_bytes = current_bytes
            time.sleep(0.5)
        if proc.returncode != 0:
            raise ValueError(
                "ipatool không tải được IPA; kiểm tra đăng nhập Apple Account, "
                "quyền sở hữu ứng dụng hoặc phiên bản App Store")
        if not target.is_file() or target.stat().st_size == 0:
            raise ValueError("ipatool không tạo ra tệp IPA")
        if target.stat().st_size > MAX_BYTES:
            raise ValueError("IPA vượt dung lượng tối đa cho phép")
        digest = hashlib.sha256()
        with target.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return target.stat().st_size, digest.hexdigest()
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)


def _run(connection, upload_dir, job_id):
    partial = None
    try:
        with connection() as db:
            row = db.execute(
                "SELECT r.id,r.app_id,r.country,r.bundle_id,r.version,"
                "r.estimated_size_bytes,COALESCE(s.name,r.name) AS name,s.url,s.sha256 "
                "FROM ipa_decrypt_requests r "
                "LEFT JOIN ipa_decrypt_sources s ON s.bundle_id=r.bundle_id COLLATE NOCASE "
                "AND s.version=r.version WHERE r.id=?", (job_id,)
            ).fetchone()
        if row is None:
            return
        upload_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        partial = upload_dir / ("." + secrets.token_hex(16) + ".ipa.partial")
        if row["url"]:
            _status(connection, job_id, "processing", 0, "Đang kết nối nguồn IPA ERI")
            size, digest = _download(connection, job_id, row["url"], partial, row["sha256"])
        elif can_auto_acquire(row["bundle_id"]):
            size, digest = _acquire_with_ipatool(
                connection, job_id, row["app_id"], partial,
                row["estimated_size_bytes"] or 0)
        else:
            _status(connection, job_id, "waiting_for_source", None,
                    "ERI chưa có nguồn IPA có quyền phân phối")
            return
        _status(connection, job_id, "processing", 92, "Đang xác minh cấu trúc IPA")
        inspection = _inspect_archive(partial)
        meta = inspection.get("app") or {}
        if inspection.get("zip_password_protected"):
            raise ValueError("IPA có mật khẩu ZIP; cần nguồn không khóa")
        if inspection.get("fairplay_encrypted") is not False:
            raise ValueError(
                "IPA tải về vẫn mã hóa FairPlay hoặc không xác minh được cryptid=0. "
                "ipatool chỉ tải tệp; chưa có worker iOS giải mã được cấp quyền")
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
                    "SELECT p.storage_name,p.size_bytes FROM ipa i "
                    "JOIN private_product_uploads p ON p.product_id=i.id AND p.kind='ipa' "
                    "WHERE i.bundle_id=? COLLATE NOCASE AND i.version=? AND i.authorized=1 "
                    "AND i.price_vnd=0 AND i.sha256=? AND p.sha256=? "
                    "ORDER BY i.id DESC LIMIT 1",
                    (row["bundle_id"], row["version"], digest, digest)
                ).fetchone()
                # A stale or different build must never discard a newly verified IPA.
                if existing and not (
                    (upload_dir / existing["storage_name"]).is_file()
                    and (upload_dir / existing["storage_name"]).stat().st_size == size
                ):
                    existing = None
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
