#!/usr/bin/env python3
"""ERI IPA Studio: App Store download with ipatool, then real macOS IPA signing.

ipatool downloads an App Store package but DOES NOT decrypt Apple FairPlay.
Signing will stop for encrypted/unknown Mach-O binaries or account metadata.
No Apple, F4, or third-party decrypt API is used by this script.
"""
import argparse
import json
import os
import plistlib
import re
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

APPSTORE_ID = re.compile(r"/(?:[a-z]{2}/)?app/(?:[^/?#]+/)?id([0-9]{6,15})(?:/)?$", re.I)
MAX_ZIP_BYTES = 2 * 1024**3
MAX_EXPANDED_BYTES = 6 * 1024**3
MAX_BINARY_HEADER = 4 * 1024 * 1024


class IPAError(Exception):
    """A user-actionable validation error without sensitive data."""


def app_id_from_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if (parts.scheme != "https" or parts.hostname != "apps.apple.com"
            or parts.port not in (None, 443) or parts.username or parts.password):
        raise IPAError("Chỉ nhận link HTTPS của apps.apple.com")
    match = APPSTORE_ID.fullmatch(parts.path)
    if not match:
        raise IPAError("Link App Store thiếu /id... hợp lệ")
    return match.group(1)


def _mach_o_encryption(blob: bytes):
    # Independent of FastAPI, so GitHub Actions can run with system Python.
    magic = blob[:4]
    shapes = {
        b"\xce\xfa\xed\xfe": ("<", 28),
        b"\xcf\xfa\xed\xfe": ("<", 32),
        b"\xfe\xed\xfa\xce": (">", 28),
        b"\xfe\xed\xfa\xcf": (">", 32),
    }
    if magic in shapes:
        endian, header_size = shapes[magic]
        if len(blob) < header_size:
            return None
        command_count, command_bytes = struct.unpack_from(endian + "II", blob, 16)
        if command_count > 4096 or command_bytes > MAX_BINARY_HEADER - header_size:
            return None
        cursor = header_size
        end = header_size + command_bytes
        if len(blob) < end:
            return None
        encrypted = False
        for _ in range(command_count):
            if cursor + 8 > end:
                return None
            command, size = struct.unpack_from(endian + "II", blob, cursor)
            if size < 8 or cursor + size > end:
                return None
            if command in (0x21, 0x2C):
                if size < 20:
                    return None
                encrypted |= struct.unpack_from(endian + "I", blob, cursor + 16)[0] != 0
            cursor += size
        return encrypted if cursor == end else None

    # Universal Mach-O: inspect ALL listed CPU slices (not only the first).
    if magic in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf",
                 b"\xbe\xba\xfe\xca", b"\xbf\xba\xfe\xca"):
        endian = ">" if magic[:1] == b"\xca" else "<"
        is64 = magic in (b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca")
        if len(blob) < 8:
            return None
        count = struct.unpack_from(endian + "I", blob, 4)[0]
        stride = 32 if is64 else 20
        if count > 32 or 8 + count * stride > len(blob):
            return None
        results = []
        for i in range(count):
            offset = 8 + i * stride
            start = struct.unpack_from(endian + ("Q" if is64 else "I"), blob, offset + 8)[0]
            if start >= len(blob):
                return None
            results.append(_mach_o_encryption(blob[start:]))
        if any(result is True for result in results):
            return True
        return False if results and all(result is False for result in results) else None
    return None


def inspect_ipa(path: Path) -> dict:
    if not path.is_file() or path.suffix.lower() != ".ipa" or path.stat().st_size > MAX_ZIP_BYTES:
        raise IPAError("Thiếu IPA hoặc tệp IPA lớn hơn giới hạn 2 GiB")
    try:
        with zipfile.ZipFile(path) as archive:
            contents = archive.infolist()
            if len(contents) > 20000:
                raise IPAError("IPA chứa quá nhiều tệp")
            total = 0
            for item in contents:
                name = item.filename
                if (name.startswith("/") or "\\" in name or ".." in name.split("/")
                        or ((item.external_attr >> 16) & 0o170000) == 0o120000):
                    raise IPAError("IPA có đường dẫn hoặc liên kết không an toàn")
                total += item.file_size
                if total > MAX_EXPANDED_BYTES:
                    raise IPAError("IPA có dung lượng giải nén quá lớn")
                if item.flag_bits & 1:
                    raise IPAError("IPA đang được mã hóa mật khẩu ZIP")
                if name == "iTunesMetadata.plist" or "/SC_Info/" in name:
                    raise IPAError("IPA còn metadata tài khoản/FairPlay, không thể ký lại an toàn")
            info_paths = [x.filename for x in contents if
                          re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", x.filename)]
            if len(info_paths) != 1:
                raise IPAError("IPA phải chứa đúng một Payload/*.app/Info.plist")
            if archive.getinfo(info_paths[0]).file_size > 2 * 1024 * 1024:
                raise IPAError("Info.plist quá lớn")
            info = plistlib.loads(archive.read(info_paths[0]))
            bundle_id = str(info.get("CFBundleIdentifier") or "")
            executable = str(info.get("CFBundleExecutable") or "")
            version = str(info.get("CFBundleShortVersionString") or info.get("CFBundleVersion") or "")
            if (not re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]{2,254}", bundle_id)
                    or not re.fullmatch(r"[A-Za-z0-9._+-]{1,128}", executable)):
                raise IPAError("Thiếu Bundle ID hoặc Mach-O executable hợp lệ")
            exe_path = info_paths[0].rsplit("/", 1)[0] + "/" + executable
            if exe_path not in archive.namelist():
                raise IPAError("IPA thiếu executable chính")
            with archive.open(exe_path) as stream:
                header = stream.read(MAX_BINARY_HEADER)
            encrypted = _mach_o_encryption(header)
            if encrypted is True:
                raise IPAError(
                    "IPA còn FairPlay (cryptid != 0). ipatool tải IPA nhưng không giải mã; "
                    "không thể ký để chạy trên thiết bị khác.")
            if encrypted is None:
                raise IPAError("Không xác minh được trạng thái mã hóa Mach-O; từ chối ký")
            return {"bundle_id": bundle_id, "version": version, "cryptid": 0}
    except (zipfile.BadZipFile, zipfile.LargeZipFile, plistlib.InvalidFileException,
            KeyError, EOFError, struct.error) as exc:
        raise IPAError("IPA ZIP / Mach-O không hợp lệ") from exc


def _run_quiet(argv, *, env=None, description):
    # Never echo commands containing Apple credentials, 2FA codes, cert paths or passwords.
    try:
        process = subprocess.run(argv, env=env, capture_output=True, timeout=1800, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise IPAError(description + ": không thể khởi động hoặc quá thời gian") from exc
    if process.returncode != 0:
        raise IPAError(description + " thất bại. Xem quyền truy cập hoặc cấu hình, không ghi mật khẩu vào log.")


def download_with_ipatool(url: str, destination: Path, external_version_id: str = ""):
    app_id = app_id_from_url(url)
    apple_id = os.getenv("ERI_APPLE_ID", "")
    password = os.getenv("ERI_APPLE_PASSWORD", "")
    passphrase = os.getenv("ERI_IPATOOL_KEYCHAIN_PASSPHRASE", "")
    if not apple_id or not password or not passphrase:
        raise IPAError("Cần ERI_APPLE_ID, ERI_APPLE_PASSWORD và ERI_IPATOOL_KEYCHAIN_PASSPHRASE")
    if external_version_id and not re.fullmatch(r"[0-9]{5,20}", external_version_id):
        raise IPAError("External Version ID phải là dãy số")
    prefix = ["ipatool", "--non-interactive", "--keychain-passphrase", passphrase]
    auth = prefix + ["auth", "login", "--email", apple_id, "--password", password]
    otp = os.getenv("ERI_APPLE_2FA_CODE", "")
    if otp:
        if not re.fullmatch(r"[0-9]{6,8}", otp):
            raise IPAError("ERI_APPLE_2FA_CODE không hợp lệ")
        auth += ["--auth-code", otp]
    _run_quiet(auth, description="Xác thực App Store bằng ipatool")
    download = prefix + ["download", "--app-id", app_id, "--platform", "iphone",
                         "--output", str(destination)]
    if external_version_id:
        download += ["--external-version-id", external_version_id]
    # No --purchase: this job must not automatically acquire paid licenses.
    _run_quiet(download, description="ipatool tải ứng dụng có quyền truy cập")
    if not destination.is_file() or destination.stat().st_size == 0:
        raise IPAError("ipatool không xuất file IPA")


def main():
    parser = argparse.ArgumentParser(description="ERI ipatool + authorized IPA signing")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--app-store-url", help="HTTPS apps.apple.com/.../id123")
    source.add_argument("--ipa", type=Path, help="Path to authorized, DRM-free IPA")
    parser.add_argument("--external-version-id", default="")
    parser.add_argument("--p12", type=Path, required=True)
    parser.add_argument("--mobileprovision", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("Ký IPA yêu cầu macOS và codesign (GitHub Actions macos-15)")
    if not os.getenv("ERI_P12_PASSWORD"):
        parser.error("Thiếu ERI_P12_PASSWORD")
    try:
        with tempfile.TemporaryDirectory(prefix="eri-ipatool-") as tmp:
            if args.app_store_url:
                ipa = Path(tmp) / "downloaded.ipa"
                print("ERI: xác thực và tải qua ipatool...")
                download_with_ipatool(args.app_store_url, ipa, args.external_version_id)
            else:
                ipa = args.ipa
            metadata = inspect_ipa(ipa)
            print("ERI: IPA đã xác minh cryptid=0; đang ký với chứng chỉ đã cung cấp...")
            worker = Path(__file__).with_name("sign_worker.py")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            _run_quiet([sys.executable, str(worker), str(ipa), str(args.p12),
                        str(args.mobileprovision), str(args.output)],
                       description="codesign IPA")
            result = inspect_ipa(args.output)
            if result["bundle_id"] != metadata["bundle_id"]:
                args.output.unlink(missing_ok=True)
                raise IPAError("Bundle ID bị thay đổi ngoài dự kiến khi ký")
            print(json.dumps({"status": "signed", "output": args.output.name,
                              "bundle_id": result["bundle_id"], "version": result["version"],
                              "fairplay": "not_encrypted"}, ensure_ascii=False))
    except IPAError as exc:
        print("ERI: " + str(exc), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
