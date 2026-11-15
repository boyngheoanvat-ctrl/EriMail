#!/usr/bin/env python3
"""ERI-owned GitHub Actions IPA processor.

Only processes approved, hash-pinned IPA sources in a reviewed manifest.
Does not obtain App Store binaries or remove Apple's FairPlay DRM.
Original IPA bytes and code signatures are preserved, never repackaged.
"""
import argparse
import hashlib
import ipaddress
import json
import os
import re
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from ipa_tools import _inspect_archive

MAX_DOWNLOAD = int(os.getenv("ERI_ACTIONS_MAX_IPA_BYTES", str(1024 * 1024 * 1024)))
MAX_UNPACKED = int(os.getenv("ERI_ACTIONS_MAX_UNPACKED_BYTES", str(2 * 1024 * 1024 * 1024)))
MAX_MEMBERS = 20000
CHUNK_SIZE = 1024 * 1024


def permitted_hosts():
    return {
        x.strip().lower().rstrip(".")
        for x in os.getenv("ERI_IPA_SOURCE_HOSTS", "").split(",")
        if x.strip()
    }


def validate_source(url):
    if not isinstance(url, str) or not 10 <= len(url) <= 1800:
        raise ValueError("Nguồn IPA không hợp lệ")
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    if (parts.scheme != "https" or not host or host not in permitted_hosts()
            or parts.port not in (None, 443) or parts.username
            or parts.password or parts.fragment):
        raise ValueError("Chỉ nhận HTTPS từ host IPA do ERI cho phép")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("Không nhận IP literal làm nguồn IPA")
    if not parts.path.lower().endswith((".ipa", ".zip")):
        raise ValueError("Nguồn phải trỏ đến file IPA hoặc ZIP được cấp quyền")
    return url


def selected_source(manifest_file, source_id):
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", source_id or ""):
        raise ValueError("source_id không hợp lệ")
    root = json.loads(Path(manifest_file).read_text(encoding="utf-8"))
    items = root.get("items") if isinstance(root, dict) else None
    if not isinstance(items, list) or len(items) > 1000:
        raise ValueError("Manifest phải có items là danh sách tối đa 1000 nguồn")
    matches = [x for x in items if isinstance(x, dict) and x.get("id") == source_id]
    if len(matches) != 1:
        raise ValueError("Không tìm thấy đúng một nguồn được duyệt")
    item = matches[0]
    if item.get("distribution_authorized") is not True:
        raise ValueError("Nguồn chưa xác nhận quyền phân phối")
    for key, pattern in (
        ("bundle_id", r"[A-Za-z0-9._-]{3,255}"),
        ("version", r"[A-Za-z0-9._+-]{1,60}"),
        ("sha256", r"[0-9a-fA-F]{64}"),
    ):
        if not isinstance(item.get(key), str) or not re.fullmatch(pattern, item[key]):
            raise ValueError("Nguồn thiếu " + key + " hợp lệ")
    validate_source(item.get("url"))
    return item


class RestrictedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_source(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def retrieve(url, destination):
    hasher = hashlib.sha256()
    received = 0
    req = urllib.request.Request(validate_source(url), headers={
        "Accept": "application/octet-stream, application/zip",
        "User-Agent": "ERI-IPA-Actions/1.0",
    })
    opener = urllib.request.build_opener(RestrictedRedirect())
    with opener.open(req, timeout=30) as response:
        length = response.headers.get("Content-Length", "")
        if length.isdecimal() and int(length) > MAX_DOWNLOAD:
            raise ValueError("IPA vượt giới hạn kích thước")
        with destination.open("xb") as stream:
            os.chmod(destination, 0o600)
            while chunk := response.read(CHUNK_SIZE):
                received += len(chunk)
                if received > MAX_DOWNLOAD:
                    raise ValueError("IPA vượt giới hạn kích thước")
                stream.write(chunk)
                hasher.update(chunk)
    if received == 0:
        raise ValueError("Nguồn trả về file rỗng")
    if length.isdecimal() and int(length) != received:
        raise ValueError("Nguồn tải chưa hoàn chỉnh")
    return hasher.hexdigest(), received


def verify_ipa(path, expected_bundle, expected_version):
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_MEMBERS:
            raise ValueError("IPA có quá nhiều thành phần")
        expanded = 0
        for info in entries:
            if info.flag_bits & 1:
                raise ValueError("IPA có thành phần ZIP mã hóa")
            expanded += info.file_size
            if expanded > MAX_UNPACKED:
                raise ValueError("IPA giải nén vượt giới hạn")
            if (info.file_size > 1024 * 1024 and
                    info.compress_size > 0 and
                    info.file_size / info.compress_size > 2000):
                raise ValueError("IPA có tỷ lệ nén bất thường")
            if info.file_size and not info.compress_size:
                raise ValueError("IPA có entry bất thường")
        bad = archive.testzip()
        if bad:
            raise ValueError("IPA lỗi CRC ZIP")
    report = _inspect_archive(path)
    app = report.get("app") or {}
    if report.get("zip_password_protected"):
        raise ValueError("IPA được bảo vệ bằng mật khẩu")
    if report.get("fairplay_encrypted") is not False:
        raise ValueError("IPA còn FairPlay hoặc không thể xác minh Mach-O")
    if str(app.get("bundle_id", "")).casefold() != expected_bundle.casefold():
        raise ValueError("Bundle ID trong IPA không khớp")
    if app.get("version") != expected_version:
        raise ValueError("Phiên bản trong IPA không khớp")
    return app


def process(manifest_file, source_id, output_dir):
    entry = selected_source(manifest_file, source_id)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="eri-actions-") as temporary:
        temporary_path = Path(temporary) / "source.ipa"
        actual_sha, byte_size = retrieve(entry["url"], temporary_path)
        if actual_sha.lower() != entry["sha256"].lower():
            raise ValueError("SHA-256 không khớp nguồn được duyệt")
        app = verify_ipa(temporary_path, entry["bundle_id"], entry["version"])
        final = output_dir / ("eri-" + source_id + ".ipa")
        if final.exists() or final.is_symlink():
            raise ValueError("Tệp đầu ra đã tồn tại")
        shutil.copyfile(temporary_path, final)
        manifest = {
            "source_id": source_id,
            "name": str(entry.get("name") or app.get("name") or source_id)[:160],
            "bundle_id": entry["bundle_id"],
            "version": entry["version"],
            "bytes": byte_size,
            "sha256": actual_sha,
            "fairplay_encrypted": False,
            "signature_preserved": True,
            "distribution_authorized_in_manifest": True,
            "file_name": final.name,
            "note": "IPA nguyên bản từ nguồn đã duyệt; chưa kiểm tra quyền cài đặt trên thiết bị.",
        }
        (output_dir / "validation.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--manifest", default="backend/eri_actions_sources.json")
    parser.add_argument("--out", default="actions-ipa-output")
    args = parser.parse_args()
    result = process(args.manifest, args.source_id, args.out)
    print(json.dumps(result, ensure_ascii=False))
