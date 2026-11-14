"""ERI independent authorized-IPA worker.

This worker downloads only admin-approved HTTPS files from explicitly allowlisted
hosts, checks SHA-256, IPA bundle/version and Mach-O encryption commands, then
publishes an actually present unencrypted IPA. It NEVER downloads from the Apple
Search API, bypasses FairPlay or changes cryptid in a protected executable.
"""
import hashlib
import ipaddress
import os
import plistlib
import re
import secrets
import socket
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

MAX_IPA_BYTES = 512 * 1024 * 1024
MAX_COMMAND_BYTES = 8 * 1024 * 1024
MAX_ENTRIES = 20000
WORKER_ENABLED = os.getenv("ERI_DECRYPT_WORKER_ENABLED", "0") == "1"
HOSTS = frozenset(x.strip().lower() for x in
                  os.getenv("ERI_DECRYPT_SOURCE_HOSTS", "").split(",") if x.strip())
wake = threading.Event()
stop = threading.Event()
thread = None


def _now():
    return datetime.now(timezone.utc).isoformat()


class Source(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    bundle_id: str = Field(min_length=5, max_length=255)
    version: str = Field(min_length=1, max_length=60)
    download_url: str = Field(min_length=12, max_length=2048)
    sha256: str = Field(min_length=64, max_length=64)
    authorized: bool = False


def _safe_url(url):
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    if (parsed.scheme != "https" or host not in HOSTS or parsed.port not in (None, 443)
            or parsed.username or parsed.password or not parsed.path.startswith("/")
            or not HOSTS):
        raise ValueError("Nguồn IPA HTTPS chưa được phép trong cấu hình ERI")
    # Restrict source traffic to public addresses, including IPv6. The allowed
    # host list must be controlled by server admins, never by shop customers.
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global
                                for item in addresses):
            raise ValueError("Máy chủ nguồn không có địa chỉ công khai")
    except (socket.gaierror, OSError) as exc:
        raise ValueError("Không phân giải được máy chủ IPA đã đăng ký") from exc
    return url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _mach_o_unencrypted(chunk):
    """Read Mach-O load commands; reject FairPlay cryptid != 0 or unknown binary."""
    magic = chunk[:4]
    shapes = {
        b"\xce\xfa\xed\xfe": ("<", 28),
        b"\xcf\xfa\xed\xfe": ("<", 32),
        b"\xfe\xed\xfa\xce": (">", 28),
        b"\xfe\xed\xfa\xcf": (">", 32),
    }
    if magic not in shapes:
        raise ValueError("Binary chính không phải Mach-O đã hỗ trợ (ARM64/ARM)")
    endian, header_size = shapes[magic]
    if len(chunk) < header_size:
        raise ValueError("Mach-O bị cắt cụt")
    ncmds, sizeofcmds = struct.unpack_from(endian + "II", chunk, 16)
    if ncmds > 100000 or sizeofcmds > MAX_COMMAND_BYTES:
        raise ValueError("Mach-O có load commands vượt giới hạn")
    if header_size + sizeofcmds > len(chunk):
        raise ValueError("Mach-O thiếu vùng load commands")
    pos = header_size
    bound = header_size + sizeofcmds
    for _ in range(ncmds):
        if pos + 8 > bound:
            raise ValueError("Mach-O load command sai định dạng")
        command, size = struct.unpack_from(endian + "II", chunk, pos)
        if size < 8 or pos + size > bound:
            raise ValueError("Mach-O load command vượt phạm vi")
        if command in (0x21, 0x2C):
            if size < 20:
                raise ValueError("Mach-O encryption command không hợp lệ")
            cryptid = struct.unpack_from(endian + "I", chunk, pos + 16)[0]
            if cryptid != 0:
                raise ValueError("IPA còn FairPlay cryptid != 0; worker không gỡ DRM")
        pos += size
    if pos != bound:
        raise ValueError("Mach-O load commands thừa dữ liệu")


def verify_ipa(path, bundle_id, version):
    """Read archive metadata without extracting or executing third-party code."""
    try:
        with zipfile.ZipFile(path, "r") as archive:
            entries = archive.infolist()
            if not entries or len(entries) > MAX_ENTRIES:
                raise ValueError("Số lượng mục trong IPA không hợp lệ")
            if any(x.filename.startswith("/") or "\\" in x.filename or
                   ".." in x.filename.split("/") for x in entries):
                raise ValueError("IPA có đường dẫn tệp không an toàn")
            roots = [x.filename for x in entries if
                     re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", x.filename)]
            if len(roots) != 1:
                raise ValueError("IPA phải có đúng một Payload/*.app/Info.plist")
            metadata_entry = archive.getinfo(roots[0])
            if metadata_entry.file_size > 1024 * 1024:
                raise ValueError("Info.plist quá lớn")
            info = plistlib.loads(archive.read(metadata_entry))
            if not isinstance(info, dict):
                raise ValueError("Info.plist không hợp lệ")
            actual_bundle = str(info.get("CFBundleIdentifier") or "")
            actual_version = str(info.get("CFBundleShortVersionString")
                                 or info.get("CFBundleVersion") or "")
            if actual_bundle != bundle_id or actual_version != version:
                raise ValueError("Bundle ID hoặc phiên bản IPA không khớp nguồn đã duyệt")
            executable = str(info.get("CFBundleExecutable") or "")
            if not re.fullmatch(r"[A-Za-z0-9_.+-]{1,128}", executable):
                raise ValueError("IPA thiếu CFBundleExecutable hợp lệ")
            root = roots[0][:-len("Info.plist")]
            member = root + executable
            if member not in archive.namelist():
                raise ValueError("IPA không chứa Mach-O chính")
            # IPA containing account metadata must be sanitized by its owner
            # before submission; do not claim to remove account records.
            if any(x.filename == "iTunesMetadata.plist" or
                   "/SC_Info/" in x.filename for x in entries):
                raise ValueError("IPA còn metadata tài khoản/FairPlay; cần nguồn sạch")
            with archive.open(member) as binary:
                header = binary.read(MAX_COMMAND_BYTES + 32)
            _mach_o_unencrypted(header)
    except (zipfile.BadZipFile, EOFError, plistlib.InvalidFileException) as exc:
        raise ValueError("Tệp IPA/Info.plist bị hỏng") from exc
    return True


def _download(row, upload_dir, connection):
    _safe_url(row["download_url"])
    candidate = upload_dir / ("." + row["id"] + ".ipa.partial")
    digest = hashlib.sha256()
    total = 0
    opener = urllib.request.build_opener(_NoRedirect())
    request = urllib.request.Request(row["download_url"], headers={
        "User-Agent": "ERI-AuthorizedIPA-Worker/1.0",
        "Accept": "application/octet-stream"})
    try:
        with opener.open(request, timeout=30) as response:
            declared = response.headers.get("Content-Length")
            expected = int(declared) if declared and declared.isdecimal() else 0
            if expected > MAX_IPA_BYTES:
                raise ValueError("Nguồn IPA vượt giới hạn 512 MiB")
            with candidate.open("xb") as output:
                os.chmod(candidate, 0o600)
                last_report = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_IPA_BYTES:
                        raise ValueError("Tệp IPA vượt giới hạn 512 MiB")
                    digest.update(chunk)
                    output.write(chunk)
                    if total - last_report >= 4 * 1024 * 1024:
                        last_report = total
                        pct = min(95, (total * 95 // expected)) if expected else 0
                        with connection() as db:
                            db.execute("UPDATE eri_decrypt_sources SET progress=?,message=?,lease_until=? WHERE id=?",
                                       (pct, "Đã nhận " + str(total) + " byte từ nguồn được phép",
                                        time.time() + 1800, row["id"]))
        if total == 0 or digest.hexdigest() != row["sha256"]:
            raise ValueError("Nguồn IPA trống hoặc SHA-256 không khớp")
        verify_ipa(candidate, row["bundle_id"], row["version"])
        return candidate, total, digest.hexdigest()
    except Exception:
        candidate.unlink(missing_ok=True)
        raise


def _claim(connection):
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT * FROM eri_decrypt_sources WHERE state='queued' "
            "OR (state='processing' AND lease_until < ?) "
            "ORDER BY created_at LIMIT 1", (time.time(),)).fetchone()
        if row is not None:
            db.execute(
                "UPDATE eri_decrypt_sources SET state='processing',progress=0,"
                "message='Đang tải IPA từ nguồn đã được ERI cấp phép',lease_until=?,updated_at=? WHERE id=?",
                (time.time() + 1800, _now(), row["id"]))
    return row


def process_next_source(connection, upload_dir):
    """Process exactly one queued source. Callable in deterministic API tests."""
    row = _claim(connection)
    if row is None:
        return False
    upload_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    candidate = None
    final = None
    try:
        candidate, size, sha = _download(row, upload_dir, connection)
        storage_id = secrets.token_hex(16)
        final = upload_dir / (storage_id + ".ipa")
        os.replace(candidate, final)
        candidate = None
        with connection() as db:
            # Avoid publishing a duplicate if an admin fulfilled it in parallel.
            existed = db.execute(
                "SELECT i.id FROM ipa i JOIN private_product_uploads p ON "
                "p.product_id=i.id AND p.kind='ipa' "
                "WHERE i.bundle_id=? AND i.version=? AND i.authorized=1 AND i.price_vnd=0 "
                "AND i.platform='ipa' LIMIT 1",
                (row["bundle_id"], row["version"])).fetchone()
            if existed:
                product_id = existed["id"]
            else:
                cursor = db.execute(
                    "INSERT INTO ipa(name,version,bundle_id,download_url,sha256,authorized,price_vnd,platform) "
                    "VALUES(?,?,?,?,?,1,0,'ipa')",
                    (row["name"], row["version"], row["bundle_id"], "private:" + storage_id, sha))
                product_id = cursor.lastrowid
                db.execute(
                    "INSERT INTO private_product_uploads(id,kind,product_id,original_name,"
                    "storage_name,size_bytes,sha256,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (storage_id, "ipa", product_id, row["name"] + "-v" + row["version"] + ".ipa",
                     final.name, size, sha, _now()))
                db.execute(
                    "INSERT OR REPLACE INTO eri_decrypt_verified_products(product_id,checked_at) VALUES(?,?)",
                    (product_id, _now()))
            db.execute(
                "UPDATE eri_decrypt_sources SET state='completed',progress=100,"
                "message='Đã kiểm tra Mach-O và lưu IPA thật trong kho ERI',"
                "product_id=?,updated_at=?,lease_until=0 WHERE id=?",
                (product_id, _now(), row["id"]))
        if existed:
            final.unlink(missing_ok=True)
        return True
    except Exception as exc:
        if final:
            final.unlink(missing_ok=True)
        # Do not publish source URLs, signed download tokens or tracebacks.
        detail = str(exc) if isinstance(exc, ValueError) else "Nguồn IPA không truy cập hoặc xử lý được"
        with connection() as db:
            db.execute(
                "UPDATE eri_decrypt_sources SET state='failed',progress=0,message=?,"
                "updated_at=?,lease_until=0 WHERE id=?",
                (detail[:240], _now(), row["id"]))
        return True


def source_state(connection, bundle_id, version):
    with connection() as db:
        entry = db.execute(
            "SELECT state,progress,message FROM eri_decrypt_sources "
            "WHERE bundle_id=? AND version=?",
            (bundle_id, version)).fetchone()
    return dict(entry) if entry else None


def verified_products(connection):
    with connection() as db:
        return {x[0] for x in db.execute(
            "SELECT product_id FROM eri_decrypt_verified_products").fetchall()}


def register_ipa_verified_worker(app, connection, upload_dir, require_admin):
    with connection() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS eri_decrypt_sources (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, bundle_id TEXT NOT NULL,
              version TEXT NOT NULL, download_url TEXT NOT NULL, sha256 TEXT NOT NULL,
              state TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
              message TEXT NOT NULL, product_id INTEGER,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              lease_until REAL NOT NULL DEFAULT 0,
              UNIQUE(bundle_id,version)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS eri_decrypt_verified_products (
              product_id INTEGER PRIMARY KEY, checked_at TEXT NOT NULL
            )
        """)
    router = APIRouter()

    @router.get("/api/v1/admin/decrypt/worker", dependencies=[Depends(require_admin)])
    def worker_status():
        with connection() as db:
            rows = db.execute(
                "SELECT state,COUNT(*) AS total FROM eri_decrypt_sources GROUP BY state").fetchall()
        return {"enabled": WORKER_ENABLED, "source_hosts_configured": bool(HOSTS),
                "counts": {x["state"]: x["total"] for x in rows},
                "notice": "Chỉ xử lý IPA được ERI cho phép phân phối; không gỡ FairPlay."}

    @router.get("/api/v1/admin/decrypt/sources", dependencies=[Depends(require_admin)])
    def list_sources():
        with connection() as db:
            rows = db.execute("SELECT id,name,bundle_id,version,state,progress,message,product_id,"
                              "created_at,updated_at FROM eri_decrypt_sources "
                              "ORDER BY created_at DESC LIMIT 100").fetchall()
        return {"items": [dict(x) for x in rows]}

    @router.post("/api/v1/admin/decrypt/sources",
                 dependencies=[Depends(require_admin)], status_code=201)
    def add_source(payload: Source):
        if not re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+){2,}", payload.bundle_id):
            raise HTTPException(422, "Bundle ID không hợp lệ")
        if not re.fullmatch(r"[A-Za-z0-9._+-]{1,60}", payload.version):
            raise HTTPException(422, "Phiên bản không hợp lệ")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", payload.sha256):
            raise HTTPException(422, "SHA-256 phải là 64 ký tự hex")
        if not payload.authorized:
            raise HTTPException(422, "Cần xác nhận quyền phân phối IPA")
        try:
            _safe_url(payload.download_url)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        with connection() as db:
            previous = db.execute(
                "SELECT id FROM eri_decrypt_sources WHERE bundle_id=? AND version=?",
                (payload.bundle_id, payload.version)).fetchone()
            sid = previous["id"] if previous else secrets.token_hex(16)
            if previous:
                db.execute(
                    "UPDATE eri_decrypt_sources SET name=?,download_url=?,sha256=?,state='queued',"
                    "progress=0,message='Đã nhận nguồn IPA được phép',updated_at=?,lease_until=0 "
                    "WHERE id=?",
                    (payload.name, payload.download_url, payload.sha256.lower(), _now(), sid))
            else:
                now = _now()
                db.execute(
                    "INSERT INTO eri_decrypt_sources(id,name,bundle_id,version,download_url,sha256,"
                    "state,progress,message,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,'queued',0,'Đã nhận nguồn IPA được phép',?,?)",
                    (sid, payload.name, payload.bundle_id, payload.version,
                     payload.download_url, payload.sha256.lower(), now, now))
        wake.set()
        return {"id": sid, "state": "queued", "worker_enabled": WORKER_ENABLED}

    app.include_router(router)

    @app.on_event("startup")
    def start_worker():
        global thread
        if not WORKER_ENABLED or thread is not None:
            return
        stop.clear()
        def loop():
            while not stop.is_set():
                try:
                    if process_next_source(connection, upload_dir):
                        continue
                except Exception:
                    # The next tick retries any leased claim after expiry.
                    pass
                wake.wait(10)
                wake.clear()
        thread = threading.Thread(target=loop, name="eri-ipa-worker", daemon=True)
        thread.start()

    @app.on_event("shutdown")
    def stop_worker():
        global thread
        stop.set()
        wake.set()
        if thread is not None:
            thread.join(timeout=4)
            thread = None
