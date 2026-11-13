"""Authenticated IPA inspection, ZIP-password removal and cross-platform IPA signing.

FairPlay-encrypted Mach-O executables cannot be decrypted from an IPA alone.
Signing requires a valid P12/private key and matching Apple provisioning profile.
Temporary inputs are deleted after each request; completed downloads expire.
"""
import importlib.util
import json
import os
import posixpath
import plistlib
import re
import secrets
import shutil
import struct
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from urllib.parse import quote, urlencode, urlsplit
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, Response
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

MAX_IPA = 512 * 1024 * 1024
MAX_CERT = 10 * 1024 * 1024
MAX_EXPANDED = 2 * 1024 * 1024 * 1024
TICKET_TTL = 15 * 60
SIGNED_TICKET_TTL = 60 * 60  # Large OTA installs may need several range requests.
PENDING_LIMIT = 3
PUBLIC_HTTPS = "https://eri-api-dag8.onrender.com"
_tickets = {}
_signed_tickets = {}
_lock = threading.Lock()
_work_slots = threading.BoundedSemaphore(value=1)


def _write_upload(upload: UploadFile, target: Path, max_bytes: int, suffix: str):
    filename = Path((upload.filename or "").replace("\\", "/")).name
    if not filename.lower().endswith(suffix):
        raise HTTPException(422, "Chọn đúng tệp " + suffix)
    count = 0
    with target.open("xb") as out:
        os.chmod(target, 0o600)
        while chunk := upload.file.read(1024 * 1024):
            count += len(chunk)
            if count > max_bytes:
                raise HTTPException(413, "File vượt giới hạn cho phép")
            out.write(chunk)
    if count == 0:
        raise HTTPException(422, "File rỗng")
    return count


def _validate_ipa(archive: zipfile.ZipFile):
    total, app_dirs = 0, set()
    for info in archive.infolist():
        name = info.filename
        path = Path(name)
        if (path.is_absolute() or ".." in path.parts or
                "\\" in name or "\x00" in name):
            raise HTTPException(422, "IPA có đường dẫn không an toàn")
        # Apple frameworks commonly use symlinks (e.g. Versions/Current -> A).
        # Accept only links whose resolved destination stays inside Payload/.
        if ((info.external_attr >> 16) & 0o170000) == 0o120000:
            if info.file_size > 2048:
                raise HTTPException(422, "Liên kết IPA quá dài")
            raw = archive.read(info)
            try:
                target = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise HTTPException(422, "Liên kết IPA không hợp lệ")
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
            if (not target or posixpath.isabs(target) or "\\x00" in target
                    or not resolved.startswith("Payload/")):
                raise HTTPException(422, "IPA chứa liên kết ra ngoài ứng dụng")
        total += info.file_size
        if total > MAX_EXPANDED:
            raise HTTPException(413, "IPA giải nén vượt 2 GiB")
        match = re.fullmatch(r"Payload/([^/]+\.app)/Info\.plist", name)
        if match:
            app_dirs.add(match.group(1))
    if len(app_dirs) != 1:
        raise HTTPException(422, "IPA cần có đúng một Payload/*.app/Info.plist")
    return next(iter(app_dirs))


def _slice_encryption(data: bytes, position: int) -> bool | None:
    if position + 32 > len(data):
        return None
    magic = data[position:position + 4]
    if magic in (b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf"):
        endian = ">"
    elif magic in (b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe"):
        endian = "<"
    else:
        return None
    is64 = magic in (b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe")
    header_bytes = 32 if is64 else 28
    commands = struct.unpack_from(endian + "I", data, position + 16)[0]
    total_bytes = struct.unpack_from(endian + "I", data, position + 20)[0]
    if commands > 4096 or total_bytes > 2 * 1024 * 1024:
        return None
    cursor = position + header_bytes
    limit = cursor + total_bytes
    if limit > len(data):
        return None
    encrypted = False
    for _ in range(commands):
        if cursor + 8 > limit:
            return None
        cmd, size = struct.unpack_from(endian + "II", data, cursor)
        if size < 8 or cursor + size > limit:
            return None
        if cmd in (0x21, 0x2c):
            if size < 20:
                return None
            encrypted |= struct.unpack_from(endian + "I", data, cursor + 16)[0] != 0
        cursor += size
    return encrypted


def _macho_encrypted(data: bytes) -> bool | None:
    if len(data) < 32:
        return None
    magic = data[:4]
    if magic in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf",
                 b"\xbe\xba\xfe\xca", b"\xbf\xba\xfe\xca"):
        endian = ">" if magic[0] == 0xca else "<"
        count = struct.unpack_from(endian + "I", data, 4)[0]
        is64 = magic in (b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca")
        step = 32 if is64 else 20
        if count > 32 or len(data) < 8 + count * step:
            return None
        results = []
        for i in range(count):
            start = 8 + i * step
            offset = struct.unpack_from(endian + ("Q" if is64 else "I"), data, start + 8)[0]
            results.append(_slice_encryption(data, offset))
        if any(x is True for x in results):
            return True
        return False if results and all(x is False for x in results) else None
    return _slice_encryption(data, 0)


def _inspect_archive(path: Path):
    try:
        with zipfile.ZipFile(path) as source:
            app = _validate_ipa(source)
            locked = any((i.flag_bits & 1) != 0 for i in source.infolist())
            metadata = {}
            encrypted = None
            if not locked:
                try:
                    content = source.read("Payload/" + app + "/Info.plist")
                    if len(content) > 2 * 1024 * 1024:
                        raise ValueError("Info.plist too large")
                    info = plistlib.loads(content)
                    metadata = {
                        "name": str(info.get("CFBundleDisplayName", info.get("CFBundleName", app)))[:160],
                        "bundle_id": str(info.get("CFBundleIdentifier", ""))[:255],
                        "version": str(info.get("CFBundleShortVersionString", ""))[:60],
                    }
                    executable = info.get("CFBundleExecutable", "")
                    if isinstance(executable, str) and re.fullmatch(r"[A-Za-z0-9._+-]{1,200}", executable):
                        with source.open("Payload/" + app + "/" + executable) as stream:
                            encrypted = _macho_encrypted(stream.read(4 * 1024 * 1024))
                except (KeyError, OSError, ValueError, plistlib.InvalidFileException,
                        zipfile.BadZipFile, RuntimeError):
                    pass
            return {
                "zip_password_protected": locked,
                "fairplay_encrypted": encrypted,
                "app": metadata,
                "can_decrypt_zip": locked,
                "note": (
                    "Tệp ZIP của IPA có đặt mật khẩu; nhập đúng mật khẩu để bỏ lớp mã hóa ZIP."
                    if locked else
                    "Đã kiểm tra IPA. FairPlay trên tệp thực thi không thể được giải mã chỉ bằng cách tải IPA lên web."
                ),
            }
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError):
        raise HTTPException(422, "Không phải tệp IPA ZIP hợp lệ")


def _register_output(file: Path):
    with _lock:
        now = time.time()
        for key, (path, expiry) in list(_tickets.items()):
            if expiry < now:
                _tickets.pop(key, None)
                Path(path).unlink(missing_ok=True)
        if len(_tickets) >= PENDING_LIMIT:
            raise HTTPException(503, "Máy chủ đang giữ nhiều file chờ tải. Vui lòng thử lại sau.")
        token = secrets.token_urlsafe(32)
        dest = Path(tempfile.gettempdir()) / ("eri-tool-" + token + ".ipa")
        os.replace(file, dest)
        os.chmod(dest, 0o600)
        _tickets[token] = (str(dest), now + TICKET_TTL)
    return {"download_path": "/api/v1/tools/ipa/download/" + token,
            "expires_in_seconds": TICKET_TTL}


def _purge_signed_locked():
    now = time.time()
    for token, (path, expiry, _) in list(_signed_tickets.items()):
        if expiry <= now:
            _signed_tickets.pop(token, None)
            Path(path).unlink(missing_ok=True)


def _manifest_details(output: Path):
    try:
        with zipfile.ZipFile(output) as archive:
            app = _validate_ipa(archive)
            name = "Payload/" + app + "/Info.plist"
            info = archive.getinfo(name)
            if info.file_size > 2 * 1024 * 1024:
                raise HTTPException(422, "Info.plist quá lớn")
            metadata = plistlib.loads(archive.read(name))
    except (KeyError, ValueError, plistlib.InvalidFileException, zipfile.BadZipFile):
        raise HTTPException(422, "Không đọc được thông tin IPA đã ký")
    bundle = str(metadata.get("CFBundleIdentifier", ""))
    version = str(metadata.get("CFBundleVersion") or metadata.get("CFBundleShortVersionString") or "1.0")
    title = str(metadata.get("CFBundleDisplayName") or metadata.get("CFBundleName") or app[:-4])
    if not re.fullmatch(r"[A-Za-z0-9.-]{3,255}", bundle) or len(version) > 80 or len(title) > 160:
        raise HTTPException(422, "Thông tin Bundle ID hoặc phiên bản không hợp lệ")
    return {"bundle_id": bundle, "version": version, "title": title}


def _register_signed_output(file: Path, metadata: dict):
    with _lock:
        _purge_signed_locked()
        if len(_signed_tickets) + len(_tickets) >= PENDING_LIMIT:
            raise HTTPException(503, "Máy chủ đang giữ nhiều IPA chờ tải, thử lại sau.")
        token = secrets.token_urlsafe(32)
        dest = Path(tempfile.gettempdir()) / ("eri-signed-" + token + ".ipa")
        os.replace(file, dest)
        os.chmod(dest, 0o600)
        _signed_tickets[token] = (str(dest), time.time() + SIGNED_TICKET_TTL, metadata)
    base_path = "/api/v1/tools/ipa/install/" + token
    manifest_url = PUBLIC_HTTPS + base_path + "/manifest.plist"
    return {
        "download_path": base_path + "/app.ipa",
        "manifest_path": base_path + "/manifest.plist",
        "install_url": "itms-services://?action=download-manifest&url=" + quote(manifest_url, safe=""),
        "expires_in_seconds": SIGNED_TICKET_TTL,
    }


def _resolve_signed(token: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,64}", token):
        raise HTTPException(404, "Không tìm thấy IPA đã ký")
    with _lock:
        _purge_signed_locked()
        item = _signed_tickets.get(token)
    if item is None or not Path(item[0]).is_file():
        raise HTTPException(410, "IPA đã ký đã hết hạn hoặc máy chủ khởi động lại. Vui lòng ký lại.")
    return item


def _appstore_reference(link: str) -> tuple[int, str]:
    """Allow App Store product URLs only; never fetch the user-provided host."""
    if not 25 <= len(link) <= 500:
        raise HTTPException(422, "Link App Store không hợp lệ")
    parsed = urlsplit(link.strip())
    if (parsed.scheme != "https" or parsed.netloc != "apps.apple.com"
            or parsed.username or parsed.password or parsed.fragment):
        raise HTTPException(422, "Chỉ chấp nhận đường dẫn HTTPS từ apps.apple.com")
    match = re.fullmatch(
        r"/(?:(?P<country>[a-zA-Z]{2})/)?app/(?:[a-zA-Z0-9._%-]{1,180}/)?id(?P<app_id>[0-9]{6,15})/?",
        parsed.path,
    )
    if not match:
        raise HTTPException(422, "Link phải có dạng apps.apple.com/vn/app/ten-ung-dung/id123456789")
    return int(match.group("app_id")), (match.group("country") or "vn").upper()


def _lookup_appstore(link: str):
    """Apple Lookup API exposes metadata; it does not expose IPA binaries."""
    app_id, country = _appstore_reference(link)
    query = urlencode({"id": app_id, "country": country, "entity": "software"})
    address = "https://itunes.apple.com/lookup?" + query
    request = urllib.request.Request(address, headers={
        "Accept": "application/json",
        "User-Agent": "ERIStore-AppLookup/1.0",
    })
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read(512 * 1024 + 1)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise HTTPException(503, "Không kết nối được dịch vụ thông tin App Store")
    if len(raw) > 512 * 1024:
        raise HTTPException(502, "Phản hồi App Store quá lớn")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError):
        raise HTTPException(502, "Dữ liệu từ App Store không hợp lệ")
    matches = data.get("results", []) if isinstance(data, dict) else []
    app = next((entry for entry in matches
                if isinstance(entry, dict) and entry.get("trackId") == app_id
                and entry.get("kind") in ("software", "mac-software")), None)
    if not app:
        raise HTTPException(404, "Ứng dụng không có trong App Store khu vực này")
    result_url = app.get("trackViewUrl") or link
    if urlsplit(result_url).netloc != "apps.apple.com":
        result_url = link
    return {
        "app_id": app_id,
        "name": str(app.get("trackName", ""))[:160],
        "developer": str(app.get("artistName", ""))[:160],
        "bundle_id": str(app.get("bundleId", ""))[:255],
        "version": str(app.get("version", ""))[:60],
        "country": country,
        "app_store_url": result_url,
        "ipa_available": False,
        "decrypted_ipa_available": False,
        "message": "Đã tìm thấy ứng dụng. App Store không cung cấp file IPA đã giải mã qua URL công khai. Bạn có thể cài từ App Store hoặc dùng tệp IPA mà bạn có quyền cung cấp.",
    }


def register_ipa_tools(app, require_customer):
    router = APIRouter()

    @router.get("/api/v1/tools/ipa/appstore")
    def appstore_lookup(url: str = Query(min_length=25, max_length=500)):
        return _lookup_appstore(url)

    @router.get("/api/v1/tools/ipa/status")
    def tool_status():
        return {
            "signing_available": importlib.util.find_spec("ipasign") is not None,
            "zip_password_decrypt_available": True,
            "fairplay_decrypt_available": False,
            "max_ipa_bytes": MAX_IPA,
        }

    @router.post("/api/v1/tools/ipa/analyze")
    def analyze(ipa: UploadFile = File(...), customer=Depends(require_customer)):
        with tempfile.TemporaryDirectory(prefix="eri-inspect-") as root:
            source = Path(root) / "input.ipa"
            _write_upload(ipa, source, MAX_IPA, ".ipa")
            return _inspect_archive(source)

    @router.post("/api/v1/tools/ipa/decrypt")
    def decrypt_zip(ipa: UploadFile = File(...), password: str = Form(""),
                    customer=Depends(require_customer)):
        if not password or len(password) > 256:
            raise HTTPException(422, "Cần mật khẩu ZIP hợp lệ (không phải mật khẩu Apple ID)")
        if not _work_slots.acquire(blocking=False):
            raise HTTPException(503, "Một tác vụ IPA khác đang chạy. Vui lòng thử lại.")
        try:
            with tempfile.TemporaryDirectory(prefix="eri-decrypt-") as root:
                source_path = Path(root) / "input.ipa"
                output_path = Path(root) / "decrypted.ipa"
                _write_upload(ipa, source_path, MAX_IPA, ".ipa")
                with zipfile.ZipFile(source_path) as source:
                    _validate_ipa(source)
                    if not any(i.flag_bits & 1 for i in source.infolist()):
                        raise HTTPException(422, "IPA không được khóa bằng mật khẩu ZIP. Không thể tháo mã hóa FairPlay theo cách này.")
                    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED,
                                         compresslevel=4, allowZip64=True) as dest:
                        try:
                            for member in source.infolist():
                                clone = zipfile.ZipInfo(member.filename, member.date_time)
                                clone.external_attr = member.external_attr
                                clone.compress_type = zipfile.ZIP_DEFLATED
                                clone.flag_bits = member.flag_bits & ~1
                                if member.is_dir():
                                    dest.writestr(clone, b"")
                                else:
                                    with source.open(member, pwd=password.encode("utf-8")) as reader:
                                        with dest.open(clone, "w", force_zip64=True) as writer:
                                            shutil.copyfileobj(reader, writer, 1024 * 1024)
                        except (RuntimeError, NotImplementedError, ValueError, OSError, zipfile.BadZipFile):
                            raise HTTPException(422, "Không giải mã được ZIP: sai mật khẩu hoặc định dạng ZIP không được hỗ trợ.")
                with zipfile.ZipFile(output_path) as check:
                    _validate_ipa(check)
                response = _register_output(output_path)
                return {**response, "message": "Đã bỏ lớp mật khẩu ZIP. Không gỡ FairPlay khỏi ứng dụng."}
        finally:
            _work_slots.release()

    @router.post("/api/v1/tools/ipa/sign")
    def sign_ipa(ipa: UploadFile = File(...), p12: UploadFile = File(...),
                 mobileprovision: UploadFile = File(...),
                 password: str = Form(...), customer=Depends(require_customer)):
        if importlib.util.find_spec("ipasign") is None:
            raise HTTPException(503, "Máy chủ chưa cài bộ ký IPA.")
        if not password or len(password) > 512:
            raise HTTPException(422, "Mật khẩu P12 không hợp lệ")
        if not _work_slots.acquire(blocking=False):
            raise HTTPException(503, "Một tác vụ IPA khác đang chạy. Vui lòng thử lại.")
        try:
            import ipasign
            with tempfile.TemporaryDirectory(prefix="eri-sign-") as root:
                folder = Path(root)
                source = folder / "input.ipa"
                certificate = folder / "cert.p12"
                profile = folder / "profile.mobileprovision"
                output = folder / "signed.ipa"
                _write_upload(ipa, source, MAX_IPA, ".ipa")
                _write_upload(p12, certificate, MAX_CERT, ".p12")
                _write_upload(mobileprovision, profile, MAX_CERT, ".mobileprovision")
                details = _inspect_archive(source)
                if details["zip_password_protected"]:
                    raise HTTPException(422, "IPA có mật khẩu ZIP, hãy giải mã ZIP trước khi ký.")
                if details["fairplay_encrypted"] is True:
                    raise HTTPException(422, "Ứng dụng còn mã hóa FairPlay. Cần IPA đã giải mã hợp pháp trước khi ký.")
                try:
                    signed = ipasign.Key(str(certificate), str(profile), password).sign(
                        str(source), str(output))
                except Exception as exc:
                    # Do not expose private key data, passwords, or signer internals.
                    raise HTTPException(422, "Ký IPA thất bại: kiểm tra P12, mật khẩu, provisioning profile và Bundle ID.") from exc
                if not output.is_file() or output.stat().st_size == 0:
                    raise HTTPException(422, "Bộ ký không tạo ra IPA hợp lệ")
                with zipfile.ZipFile(output) as verified:
                    _validate_ipa(verified)
                details = _manifest_details(output)
                if details["bundle_id"] != str(signed.bundle_id):
                    raise HTTPException(422, "Bundle ID trong IPA ký không khớp")
                response = _register_signed_output(output, details)
                return {
                    **response,
                    "message": "IPA đã được ký. Có thể tải về hoặc bấm Cài lên iPhone bằng Safari.",
                    "bundle_id": details["bundle_id"],
                    "app_name": details["title"],
                    "app_version": details["version"],
                }
        finally:
            _work_slots.release()

    @router.get("/api/v1/tools/ipa/install/{token}/manifest.plist")
    def install_manifest(token: str):
        _, _, metadata = _resolve_signed(token)
        ipa_url = PUBLIC_HTTPS + "/api/v1/tools/ipa/install/" + token + "/app.ipa"
        manifest = {
            "items": [{
                "assets": [{"kind": "software-package", "url": ipa_url}],
                "metadata": {
                    "bundle-identifier": metadata["bundle_id"],
                    "bundle-version": metadata["version"],
                    "kind": "software",
                    "title": metadata["title"],
                },
            }],
        }
        return Response(
            content=plistlib.dumps(manifest, fmt=plistlib.FMT_XML),
            media_type="text/xml",
            headers={"Cache-Control": "private, no-store",
                     "X-Content-Type-Options": "nosniff"},
        )

    @router.get("/api/v1/tools/ipa/install/{token}/app.ipa")
    def install_ipa(token: str):
        path, _, _ = _resolve_signed(token)
        # iOS can perform several download or range requests. Do not consume
        # this ticket on the first request, unlike one-time browser downloads.
        return FileResponse(
            path, filename="ERI-signed.ipa",
            media_type="application/octet-stream",
            headers={"Cache-Control": "private, no-store",
                     "X-Content-Type-Options": "nosniff"},
        )

    @router.get("/api/v1/tools/ipa/download/{token}")
    def download(token: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,64}", token):
            raise HTTPException(404, "Không tìm thấy file")
        with _lock:
            item = _tickets.pop(token, None)
        if item is None:
            raise HTTPException(404, "File đã hết hạn hoặc đã được tải")
        path, expiry = item
        if time.time() >= expiry or not Path(path).is_file():
            Path(path).unlink(missing_ok=True)
            raise HTTPException(410, "File đã hết hạn")
        return FileResponse(
            path, filename="ERI-processed.ipa", media_type="application/octet-stream",
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                     "Referrer-Policy": "no-referrer"},
            background=BackgroundTask(lambda: Path(path).unlink(missing_ok=True))
        )

    app.include_router(router)
