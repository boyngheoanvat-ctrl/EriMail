"""Automatic private Cloudflare R2 publication of a verified IPA.

The only input is a validation.json emitted after matching a reviewed source,
SHA-256, ZIP CRC, Bundle ID/version and Mach-O encryption. No source credentials
or public ACLs are ever committed. S3 multipart transfer avoids loading entire
IPAs into memory and does not duplicate the file on disk.
"""
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

MAX_R2_OBJECT = 5 * 1024 ** 4  # Physical Cloudflare R2 per-object limit.
PART_BYTES = 64 * 1024 ** 2


def _r2_settings():
    account = os.getenv("ERI_R2_ACCOUNT_ID", "").strip()
    bucket = os.getenv("ERI_R2_BUCKET", "").strip()
    access = os.getenv("ERI_R2_ACCESS_KEY_ID", "").strip()
    secret = os.getenv("ERI_R2_SECRET_ACCESS_KEY", "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{32}", account):
        raise ValueError("Thiếu ERI_R2_ACCOUNT_ID hợp lệ")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,62}", bucket):
        raise ValueError("Thiếu ERI_R2_BUCKET hợp lệ")
    if not access or not secret:
        raise ValueError("Thiếu API token R2 có quyền ghi Object")
    return account, bucket, access, secret


def _entry(report_path):
    info = json.loads(Path(report_path).read_text(encoding="utf-8"))
    if not isinstance(info, dict):
        raise ValueError("Báo cáo IPA không hợp lệ")
    source_id = info.get("source_id")
    sha = info.get("sha256")
    bundle = info.get("bundle_id")
    version = info.get("version")
    filename = info.get("file_name")
    size = info.get("bytes")
    if not (isinstance(source_id, str) and
            re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", source_id)):
        raise ValueError("source_id không hợp lệ")
    if not (isinstance(sha, str) and re.fullmatch(r"[a-f0-9]{64}", sha)):
        raise ValueError("SHA-256 không hợp lệ")
    if not (isinstance(bundle, str) and re.fullmatch(r"[A-Za-z0-9._-]{3,255}", bundle)):
        raise ValueError("Bundle ID không hợp lệ")
    if not (isinstance(version, str) and re.fullmatch(r"[A-Za-z0-9._+-]{1,60}", version)):
        raise ValueError("Phiên bản không hợp lệ")
    if filename != "eri-" + source_id + ".ipa":
        raise ValueError("Tên IPA ngoài đường dẫn được cấp")
    if type(size) is not int or not 0 < size <= MAX_R2_OBJECT:
        raise ValueError("Dung lượng vượt giới hạn R2")
    if info.get("fairplay_encrypted") is not False or info.get("signature_preserved") is not True:
        raise ValueError("IPA chưa được worker xác minh")
    if info.get("distribution_authorized_in_manifest") is not True:
        raise ValueError("Thiếu quyền phân phối trong manifest")
    return info


def _checksum(path):
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def object_key(info):
    # Content-addressed, deterministic object key; no user-provided filenames.
    return ("ipa/" + info["bundle_id"].lower() + "/" + info["version"]
            + "/" + info["sha256"] + ".ipa")


def _client():
    import boto3
    from botocore.config import Config
    account, _bucket, access, secret = _r2_settings()
    return boto3.client(
        "s3",
        endpoint_url="https://" + account + ".r2.cloudflarestorage.com",
        region_name="auto",
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 5, "mode": "standard"},
            connect_timeout=15, read_timeout=120,
        ),
    )


def publish(report_path, output_path=None, client=None):
    info = _entry(report_path)
    filename = Path(report_path).resolve().parent / info["file_name"]
    if not filename.is_file() or filename.is_symlink():
        raise ValueError("Không tìm thấy IPA gốc đã xác minh")
    if filename.stat().st_size != info["bytes"]:
        raise ValueError("Kích thước IPA khác báo cáo")
    if _checksum(filename) != info["sha256"]:
        raise ValueError("SHA-256 IPA không khớp báo cáo")
    _account, bucket, _access, _secret = _r2_settings()
    client = client or _client()
    key = object_key(info)
    metadata = {
        "sha256": info["sha256"],
        "bundle-id": info["bundle_id"],
        "version": info["version"],
        "source-id": info["source_id"],
    }

    exists = False
    try:
        head = client.head_object(Bucket=bucket, Key=key)
        exists = (head.get("ContentLength") == info["bytes"]
                  and head.get("Metadata", {}).get("sha256") == info["sha256"])
        if not exists:
            raise ValueError("R2 đã có object trùng tên nhưng không đúng SHA-256/dung lượng")
    except Exception as exc:
        # NotFound is expected; do not swallow Forbidden, timeouts or other errors.
        code = getattr(exc, "response", {}).get("Error", {}).get("Code")
        if str(code) not in ("404", "NoSuchKey", "NotFound"):
            raise
    if not exists:
        from boto3.s3.transfer import TransferConfig
        client.upload_file(
            str(filename), bucket, key,
            ExtraArgs={
                "ContentType": "application/octet-stream",
                "Metadata": metadata,
                "ContentDisposition": 'attachment; filename="' + info["file_name"] + '"',
            },
            Config=TransferConfig(
                multipart_threshold=PART_BYTES,
                multipart_chunksize=PART_BYTES,
                max_concurrency=2,
                use_threads=True,
            ),
        )
    head = client.head_object(Bucket=bucket, Key=key)
    if (head.get("ContentLength") != info["bytes"] or
            head.get("Metadata", {}).get("sha256") != info["sha256"]):
        raise ValueError("R2 không xác nhận IPA đã tải lên")
    published = {
        "ok": True,
        "source_id": info["source_id"],
        "bundle_id": info["bundle_id"],
        "version": info["version"],
        "sha256": info["sha256"],
        "bytes": info["bytes"],
        "bucket": bucket,
        "object_key": key,
        "source": "ERI R2 private storage",
        "private": True,
    }
    if output_path:
        Path(output_path).write_text(
            json.dumps(published, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return published


def notify_eri(published, job_id=None):
    """Optional server-to-server notification; only after verified R2 upload."""
    token = os.getenv("ERI_ACTIONS_CALLBACK_TOKEN", "").strip()
    endpoint = os.getenv("ERI_ACTIONS_CALLBACK_URL", "").strip()
    if not token or not endpoint:
        return False
    from urllib.parse import urlsplit
    target = urlsplit(endpoint)
    if (target.scheme != "https" or target.hostname !=
            "eri-api-dag8.onrender.com" or target.path !=
            "/api/v1/worker/ipa/complete" or target.query or target.fragment):
        raise ValueError("Webhook ERI phải dùng URL cố định và HTTPS")
    payload = dict(published)
    if job_id:
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise ValueError("job_id không hợp lệ")
        payload["job_id"] = job_id
    request = urllib.request.Request(
        endpoint, data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-ERI-Worker-Token": token,
        }, method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise ValueError("ERI chưa nhận xác nhận lưu trữ IPA")
    return True


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--validation", default="actions-ipa-output/validation.json")
    parser.add_argument("--output", default="actions-ipa-output/r2-published.json")
    parser.add_argument("--job-id", default="")
    args = parser.parse_args()
    result = publish(args.validation, args.output)
    notify_eri(result, args.job_id or None)
    print(json.dumps({
        "ok": True, "source_id": result["source_id"],
        "bytes": result["bytes"], "private": True
    }))
