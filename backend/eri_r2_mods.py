"""Optional direct-to-R2 multipart IPA/APK uploads, without a local Render copy.

Disabled unless ERI_R2_MOD_UPLOADS=enabled and the existing private R2
credentials are configured. No live customer DB or data is migrated by this
module. Multipart object size has a physical, provider-defined limit.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import plistlib
import re
import secrets
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from actions_r2_publish import _client, _r2_settings

MIB = 1024 * 1024
GIB = 1024**3
R2_MAX_OBJECT = 5 * 1024**4 - 5 * GIB  # 4.995 TiB provider limit
R2_MAX_PARTS = 10_000
R2_MIN_PART = 5 * MIB
R2_DEFAULT_PART = 64 * MIB
R2_MAX_PART = 5 * GIB
_R2_NAME = re.compile(r"r2\.([a-f0-9]{32})\.(ipa|apk)\Z")
_ID = re.compile(r"[a-f0-9]{32}\Z")


def enabled() -> bool:
    if os.getenv("ERI_R2_MOD_UPLOADS") != "enabled":
        return False
    try:
        _r2_settings()
        return True
    except ValueError:
        return False


def require_ready():
    if not enabled():
        raise HTTPException(503, "Kho R2 chưa được cấu hình để tải IPA lớn")
    return _client(), _r2_settings()[1]


def choose_part_size(size: int) -> int:
    if not isinstance(size, int) or not 0 < size <= R2_MAX_OBJECT:
        raise ValueError("R2 object is outside provider limits")
    # 64 MiB by default, scale to keep the multipart count <= 10,000.
    needed = math.ceil(size / R2_MAX_PARTS)
    part = max(R2_DEFAULT_PART, ((needed + 8 * MIB - 1) // (8 * MIB)) * (8 * MIB))
    if not R2_MIN_PART <= part <= R2_MAX_PART or math.ceil(size / part) > R2_MAX_PARTS:
        raise ValueError("Multipart size cannot fit within R2 limits")
    return part


def parse_storage_name(value: str):
    match = _R2_NAME.fullmatch(value) if isinstance(value, str) else None
    return (match.group(1), match.group(2)) if match else None


def storage_name(upload_id: str, kind: str) -> str:
    if not _ID.fullmatch(upload_id) or kind not in ("ipa", "apk"):
        raise ValueError("Invalid storage name")
    return "r2." + upload_id + "." + kind


def object_key(upload_id: str, kind: str) -> str:
    if not _ID.fullmatch(upload_id) or kind not in ("ipa", "apk"):
        raise ValueError("Invalid R2 object key")
    return "eri-mods/" + upload_id + "." + kind


def object_exists(value: str, size: int) -> bool:
    parsed = parse_storage_name(value)
    if not parsed or not enabled():
        return False
    ident, kind = parsed
    try:
        client, bucket = require_ready()
        info = client.head_object(Bucket=bucket, Key=object_key(ident, kind))
        return info.get("ContentLength") == size
    except Exception:
        return False


def signed_download(value: str, filename: str) -> str:
    parsed = parse_storage_name(value)
    if not parsed:
        raise HTTPException(404, "R2 file not found")
    client, bucket = require_ready()
    ident, kind = parsed
    # Never place user-controlled content in the object locator.
    name = Path(filename).name.replace('"', "").replace("\r", "").replace("\n", "")
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": object_key(ident, kind),
                "ResponseContentDisposition": 'attachment; filename="' + name[:160] + '"'},
        ExpiresIn=600,
    )


def delete_object(value: str) -> bool:
    parsed = parse_storage_name(value)
    if not parsed or not enabled():
        return False
    ident, kind = parsed
    client, bucket = require_ready()
    client.delete_object(Bucket=bucket, Key=object_key(ident, kind))
    return True


class R2Seekable:
    """Small ranged GETs for reading IPA ZIP central directory / Info.plist."""

    def __init__(self, client, bucket: str, key: str, length: int):
        self.client, self.bucket, self.key = client, bucket, key
        self.length, self.pos = length, 0

    def tell(self):
        return self.pos

    def seek(self, offset, whence=io.SEEK_SET):
        if whence == io.SEEK_END:
            offset += self.length
        elif whence == io.SEEK_CUR:
            offset += self.pos
        elif whence != io.SEEK_SET:
            raise ValueError("Invalid seek mode")
        if offset < 0 or offset > self.length:
            raise ValueError("Invalid ZIP seek")
        self.pos = offset
        return offset

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.length - self.pos
        n = min(n, self.length - self.pos)
        if not n:
            return b""
        first, last = self.pos, self.pos + n - 1
        response = self.client.get_object(
            Bucket=self.bucket, Key=self.key,
            Range=f"bytes={first}-{last}",
        )
        body = response["Body"]
        try:
            data = body.read(n)
        finally:
            body.close()
        if len(data) != n:
            raise IOError("Incomplete R2 range")
        self.pos += n
        return data

    def seekable(self):
        return True


def inspect_remote(client, bucket: str, key: str, length: int, kind: str):
    with zipfile.ZipFile(R2Seekable(client, bucket, key, length)) as package:
        if kind == "apk":
            if "AndroidManifest.xml" not in package.namelist():
                raise ValueError("Invalid APK manifest")
            return "", ""
        candidates = [entry for entry in package.infolist()
                      if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", entry.filename)]
        if not candidates:
            raise ValueError("IPA missing Info.plist")
        entry = candidates[0]
        if entry.file_size > MIB or entry.compress_size > MIB:
            raise ValueError("IPA Info.plist too large")
        metadata = plistlib.loads(package.read(entry))
        bundle = str(metadata.get("CFBundleIdentifier", "")).strip()
        version = str(metadata.get("CFBundleShortVersionString")
                      or metadata.get("CFBundleVersion") or "").strip()
        if not (3 <= len(bundle) <= 255 and 1 <= len(version) <= 60):
            raise ValueError("Invalid IPA bundle ID/version")
        return version, bundle


def sha256_remote(client, bucket: str, key: str, size: int) -> str:
    body = client.get_object(Bucket=bucket, Key=key)["Body"]
    digest, total = hashlib.sha256(), 0
    try:
        for chunk in body.iter_chunks(chunk_size=4 * MIB):
            if chunk:
                total += len(chunk)
                if total > size:
                    raise IOError("R2 object is larger than expected")
                digest.update(chunk)
    finally:
        body.close()
    if total != size:
        raise IOError("R2 object is incomplete")
    return digest.hexdigest()


def list_uploaded_parts(client, bucket: str, key: str, upload_token: str):
    items = []
    marker = 0
    while True:
        response = client.list_parts(Bucket=bucket, Key=key, UploadId=upload_token,
                                     PartNumberMarker=marker, MaxParts=1000)
        parts = response.get("Parts", [])
        items.extend(parts)
        if len(items) > R2_MAX_PARTS:
            raise ValueError("Too many R2 upload parts")
        if not response.get("IsTruncated"):
            break
        next_marker = response.get("NextPartNumberMarker")
        if not isinstance(next_marker, int) or next_marker <= marker:
            raise ValueError("Invalid R2 pagination")
        marker = next_marker
    return items


class ModStart(BaseModel):
    kind: str = Field(pattern="^(ipa|apk)$")
    filename: str = Field(min_length=1, max_length=180)
    size_bytes: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    price_vnd: int = Field(ge=0, le=1_000_000_000)
    authorized: bool = False


def install_r2_mod_routes(app, connection, require_admin):
    from fastapi import status

    def ensure_table(db):
        db.execute("""
            CREATE TABLE IF NOT EXISTS eri_r2_mod_sessions (
                id TEXT PRIMARY KEY, r2_upload_id TEXT NOT NULL,
                kind TEXT NOT NULL, original_name TEXT NOT NULL,
                size_bytes INTEGER NOT NULL, part_bytes INTEGER NOT NULL,
                name TEXT NOT NULL, description TEXT NOT NULL,
                price_vnd INTEGER NOT NULL, state TEXT NOT NULL,
                product_id INTEGER, created_at TEXT NOT NULL
            )
        """)

    def lookup(sid: str):
        if not _ID.fullmatch(sid):
            raise HTTPException(404, "R2 upload session not found")
        with connection() as db:
            ensure_table(db)
            row = db.execute("SELECT * FROM eri_r2_mod_sessions WHERE id=?", (sid,)).fetchone()
        if row is None:
            raise HTTPException(404, "R2 upload session not found")
        return dict(row)

    @app.get("/api/v1/admin/r2-mods/config", dependencies=[Depends(require_admin)])
    def config():
        return {"enabled": enabled(), "max_object_bytes": R2_MAX_OBJECT if enabled() else 0,
                "part_bytes": R2_DEFAULT_PART, "provider": "Cloudflare R2"}

    @app.post("/api/v1/admin/r2-mods/start", dependencies=[Depends(require_admin)],
              status_code=status.HTTP_201_CREATED)
    def begin(item: ModStart):
        client, bucket = require_ready()
        filename = Path(item.filename).name
        if Path(filename).suffix.lower() != "." + item.kind or not item.authorized:
            raise HTTPException(422, "Valid extension and distribution authorization required")
        if item.size_bytes > R2_MAX_OBJECT:
            raise HTTPException(413, "R2 provider maximum object size exceeded")
        part_size = choose_part_size(item.size_bytes)
        ident = secrets.token_hex(16)
        key = object_key(ident, item.kind)
        try:
            result = client.create_multipart_upload(
                Bucket=bucket, Key=key, ContentType="application/octet-stream",
                Metadata={"eri-kind": item.kind, "eri-size": str(item.size_bytes)},
            )
            upload_token = result["UploadId"]
        except Exception:
            raise HTTPException(503, "R2 could not create an upload session")
        try:
            with connection() as db:
                ensure_table(db)
                db.execute(
                    "INSERT INTO eri_r2_mod_sessions "
                    "(id,r2_upload_id,kind,original_name,size_bytes,part_bytes,"
                    "name,description,price_vnd,state,created_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (ident,upload_token,item.kind,filename,item.size_bytes,part_size,
                     item.name.strip(),item.description,item.price_vnd,"pending",
                     datetime.now(timezone.utc).isoformat()),
                )
        except Exception:
            client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_token)
            raise
        return {"upload_id": ident, "size_bytes": item.size_bytes,
                "part_bytes": part_size,
                "total_parts": math.ceil(item.size_bytes / part_size)}

    @app.get("/api/v1/admin/r2-mods/{sid}/status", dependencies=[Depends(require_admin)])
    def upload_status(sid: str):
        row = lookup(sid)
        if row["state"] == "aborted":
            raise HTTPException(410, "R2 upload was cancelled")
        if row["state"] == "published":
            return {"state": "published", "product_id": row["product_id"],
                    "received": [], "total_parts": math.ceil(row["size_bytes"] / row["part_bytes"])}
        client, bucket = require_ready()
        key = object_key(sid, row["kind"])
        try:
            existing = client.head_object(Bucket=bucket, Key=key)
            if existing.get("ContentLength") == row["size_bytes"]:
                return {"state": "verifying", "received": [],
                        "size_bytes":row["size_bytes"], "part_bytes":row["part_bytes"],
                        "total_parts":math.ceil(row["size_bytes"]/row["part_bytes"])}
        except Exception:
            pass  # A not-yet-completed multipart object is not visible to HEAD.
        try:
            parts = list_uploaded_parts(client,bucket,key,row["r2_upload_id"])
        except Exception:
            raise HTTPException(503, "R2 cannot confirm uploaded chunks")
        received = [x["PartNumber"]-1 for x in parts]
        return {"state": row["state"], "received": received,
                "size_bytes": row["size_bytes"],"part_bytes":row["part_bytes"],
                "total_parts": math.ceil(row["size_bytes"] / row["part_bytes"])}

    @app.get("/api/v1/admin/r2-mods/unfinished",
             dependencies=[Depends(require_admin)])
    def unfinished(limit: int = 100):
        """Read-only inventory; do not discard existing multipart bytes."""
        if not 1 <= limit <= 500:
            raise HTTPException(422, "Invalid unfinished upload limit")
        with connection() as db:
            ensure_table(db)
            rows = db.execute(
                "SELECT id,kind,original_name,size_bytes,created_at,state "
                "FROM eri_r2_mod_sessions WHERE state IN ('pending','verifying') "
                "ORDER BY created_at DESC LIMIT ?", (limit,),
            ).fetchall()
        return {"items": [dict(row) for row in rows]}

    @app.post("/api/v1/admin/r2-mods/{sid}/abort",
              dependencies=[Depends(require_admin)])
    def abort_pending(sid: str):
        """Explicit, admin-only cancellation; never delete published files."""
        row = lookup(sid)
        if row["state"] == "published":
            raise HTTPException(409, "A published IPA cannot be aborted")
        if row["state"] == "aborted":
            return {"aborted": True, "upload_id": sid}
        if row["state"] != "pending":
            raise HTTPException(409, "Upload is not pending")
        client, bucket = require_ready()
        key = object_key(sid, row["kind"])
        try:
            existing = client.head_object(Bucket=bucket, Key=key)
            if existing.get("ContentLength") == row["size_bytes"]:
                raise HTTPException(409, "R2 object is already finalized; publish it instead")
        except HTTPException:
            raise
        except Exception:
            # A missing multipart object is not visible through HEAD.
            pass
        try:
            client.abort_multipart_upload(
                Bucket=bucket, Key=key, UploadId=row["r2_upload_id"],
            )
        except Exception as exc:
            # Do not pretend a cancelled upload is cleaned up on R2.
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            if code != "NoSuchUpload":
                raise HTTPException(503, "R2 could not cancel this upload") from exc
        with connection() as db:
            ensure_table(db)
            current = db.execute(
                "SELECT state FROM eri_r2_mod_sessions WHERE id=?", (sid,),
            ).fetchone()
            if current is None or current["state"] == "published":
                raise HTTPException(409, "Upload was published concurrently")
            db.execute(
                "UPDATE eri_r2_mod_sessions SET state='aborted' "
                "WHERE id=? AND state='pending'", (sid,),
            )
        return {"aborted": True, "upload_id": sid}

    @app.get("/api/v1/admin/r2-mods/{sid}/part/{index}",
             dependencies=[Depends(require_admin)])
    def presign_chunk(sid: str, index: int):
        row = lookup(sid)
        if row["state"] != "pending":
            raise HTTPException(409, "R2 upload is not accepting parts")
        count = math.ceil(row["size_bytes"] / row["part_bytes"])
        if not 0 <= index < count:
            raise HTTPException(422, "Part out of range")
        client, bucket = require_ready()
        signed_url = client.generate_presigned_url(
            "upload_part",
            Params={"Bucket":bucket,"Key":object_key(sid,row["kind"]),
                    "UploadId":row["r2_upload_id"],"PartNumber":index+1},
            ExpiresIn=900,
        )
        if not signed_url.startswith("https://"):
            raise HTTPException(503, "Invalid R2 presigned URL")
        return {"url":signed_url, "expires_in_seconds":900,
                "index":index,"bytes":min(row["part_bytes"],
                                         row["size_bytes"] - index*row["part_bytes"])}

    @app.post("/api/v1/admin/r2-mods/{sid}/complete",
              dependencies=[Depends(require_admin)],
              status_code=status.HTTP_201_CREATED)
    def finish(sid: str):
        row = lookup(sid)
        if row["state"] == "aborted":
            raise HTTPException(410, "R2 upload was cancelled")
        if row["state"] == "published":
            return {"id":row["product_id"],"kind":row["kind"],"uploaded":True}
        client,bucket = require_ready()
        key = object_key(sid,row["kind"])
        expected_count = math.ceil(row["size_bytes"]/row["part_bytes"])
        if row["state"] == "pending":
            # If the API was interrupted after R2 finalized the object but
            # before SQLite committed, retry verification instead of replaying
            # an already-consumed multipart UploadId.
            completed = False
            try:
                head = client.head_object(Bucket=bucket, Key=key)
                completed = head.get("ContentLength") == row["size_bytes"]
            except Exception:
                pass
            if not completed:
                try:
                    parts=list_uploaded_parts(client,bucket,key,row["r2_upload_id"])
                    if len(parts) != expected_count:
                        raise HTTPException(409, "Some R2 parts are not uploaded")
                    for n,part in enumerate(parts):
                        expected=min(row["part_bytes"],row["size_bytes"]-n*row["part_bytes"])
                        if part.get("PartNumber") != n+1 or part.get("Size") != expected:
                            raise HTTPException(409, "R2 part order or size mismatch")
                    client.complete_multipart_upload(
                        Bucket=bucket,Key=key,UploadId=row["r2_upload_id"],
                        MultipartUpload={"Parts":[{"ETag":p["ETag"],"PartNumber":p["PartNumber"]}
                                                  for p in parts]},
                    )
                except HTTPException:
                    raise
                except Exception:
                    raise HTTPException(503, "R2 did not complete the multipart upload")
        try:
            info = client.head_object(Bucket=bucket,Key=key)
            if info.get("ContentLength") != row["size_bytes"]:
                raise ValueError("R2 file size mismatch")
            checksum=sha256_remote(client,bucket,key,row["size_bytes"])
            version,bundle=inspect_remote(client,bucket,key,row["size_bytes"],row["kind"])
        except (ValueError, OSError, KeyError, zipfile.BadZipFile, plistlib.InvalidFileException):
            raise HTTPException(422, "R2 archive verification failed")
        except Exception:
            raise HTTPException(503, "R2 archive validation service unavailable")
        # DB snapshot errors must not report publication as complete.
        with connection() as db:
            ensure_table(db)
            present = db.execute(
                "SELECT product_id FROM eri_r2_mod_sessions WHERE id=? AND state='published'",
                (sid,),
            ).fetchone()
            if present:
                return {"id":present["product_id"],"kind":row["kind"],"uploaded":True}
            product = db.execute(
                "INSERT INTO ipa(name,version,bundle_id,download_url,sha256,"
                "authorized,price_vnd,description,platform) VALUES(?,?,?,?,?,1,?,?,?)",
                (row["name"],version,bundle,"private:"+sid,checksum,
                 row["price_vnd"],row["description"],row["kind"]),
            )
            db.execute(
                "INSERT INTO private_product_uploads"
                "(id,kind,product_id,original_name,storage_name,size_bytes,sha256,created_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (sid,row["kind"],product.lastrowid,row["original_name"],
                 storage_name(sid,row["kind"]),row["size_bytes"],checksum,
                 datetime.now(timezone.utc).isoformat()),
            )
            db.execute(
                "UPDATE eri_r2_mod_sessions SET state='published',product_id=? WHERE id=?",
                (product.lastrowid,sid),
            )
            product_id = product.lastrowid
        return {"id":product_id,"kind":row["kind"],"uploaded":True,"sha256":checksum}
