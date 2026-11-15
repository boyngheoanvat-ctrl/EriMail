"""Fail-closed client for ERI's private Supabase Storage vault.

Staging only: do not enable on the existing Render API before exporting the
live SQLite DB. Public catalog links never reveal the vault secret or bucket.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CHUNK_BYTES = 4 * 1024 * 1024
MAX_PRODUCT_BYTES = 512 * 1024 * 1024
MAX_SQLITE_BYTES = 64 * 1024 * 1024
VAULT_BASE = "https://dahrbjjknmvwokruhnha.supabase.co/functions/v1/eri-private-vault"
_NAME = re.compile(r"^[a-f0-9]{32}$")
_HASH = re.compile(r"^[a-f0-9]{64}$")


class VaultError(RuntimeError):
    """A remote persistence operation failed. Never acknowledge the write."""


class ERIVault:
    def __init__(self, secret: str | None = None, base: str = VAULT_BASE):
        self.secret = secret if secret is not None else os.environ.get("ERI_VAULT_SECRET", "")
        if len(self.secret) < 48:
            raise VaultError("ERI_VAULT_SECRET chưa được cấu hình")
        parsed = urllib.parse.urlsplit(base)
        if parsed.scheme != "https" or not parsed.hostname or parsed.query or parsed.fragment:
            raise VaultError("Vault endpoint must use HTTPS")
        self.base = base.rstrip("/")

    @staticmethod
    def _sha(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _request(self, method: str, route: str, body: bytes | None = None,
                 content_type: str | None = None, checksum: str | None = None):
        if not route.startswith("/") or "//" in route or ".." in route:
            raise VaultError("Invalid vault path")
        headers = {"X-ERI-Vault-Secret": self.secret, "Cache-Control": "no-store"}
        if body is not None:
            headers["Content-Type"] = content_type or "application/octet-stream"
            if checksum:
                headers["X-ERI-SHA256"] = checksum
        req = urllib.request.Request(self.base + route, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=90) as response:
                if response.status < 200 or response.status >= 300:
                    raise VaultError("Vault refused storage operation")
                return response.read(CHUNK_BYTES + 65536)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise FileNotFoundError(route) from exc
            raise VaultError("Vault HTTP " + str(exc.code)) from exc
        except (OSError, TimeoutError) as exc:
            raise VaultError("Cannot reach private vault") from exc

    @staticmethod
    def _valid_id(obj_id: str):
        if not _NAME.fullmatch(obj_id):
            raise VaultError("Invalid private storage ID")

    def _put(self, kind: str, obj_id: str, index: int, data: bytes):
        self._valid_id(obj_id)
        if kind not in ("files", "snapshots", "sessionmeta") or not 0 <= index < 128:
            raise VaultError("Invalid vault chunk path")
        if not 0 < len(data) <= CHUNK_BYTES:
            raise VaultError("Invalid chunk size")
        output = self._request("PUT", f"/{kind}/{obj_id}/{index}",
                               data, checksum=self._sha(data))
        try:
            answer = json.loads(output)
        except (ValueError, TypeError) as exc:
            raise VaultError("Vault did not acknowledge chunk") from exc
        if (answer.get("ok") is not True or
            answer.get("size") != len(data) or
            answer.get("sha256") != self._sha(data)):
            raise VaultError("Vault chunk acknowledgement mismatch")

    def _get(self, kind: str, obj_id: str, index: int) -> bytes:
        self._valid_id(obj_id)
        if kind not in ("files", "snapshots", "sessionmeta") or not 0 <= index < 128:
            raise VaultError("Invalid vault chunk path")
        data = self._request("GET", f"/{kind}/{obj_id}/{index}")
        if not 0 < len(data) <= CHUNK_BYTES:
            raise VaultError("Vault returned an invalid chunk")
        return data

    def chunk_exists(self, obj_id: str, index: int = 0, *, kind: str = "files") -> bool:
        self._valid_id(obj_id)
        if kind not in ("files", "snapshots", "sessionmeta") or not 0 <= index < 128:
            raise VaultError("Invalid chunk locator")
        try:
            self._request("HEAD", f"/{kind}/{obj_id}/{index}")
            return True
        except FileNotFoundError:
            return False

    def put_session(self, obj_id: str, metadata: dict) -> None:
        self._valid_id(obj_id)
        data = json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(data) > 128 * 1024:
            raise VaultError("Upload metadata too large")
        self._put("sessionmeta", obj_id, 0, data)

    def get_session(self, obj_id: str) -> dict:
        self._valid_id(obj_id)
        raw = self._get("sessionmeta", obj_id, 0)
        if len(raw) > 128 * 1024:
            raise VaultError("Upload session too large")
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get("upload_id") != obj_id:
            raise VaultError("Invalid upload session in cloud")
        return result

    def put_upload_part(self, obj_id: str, index: int, data: bytes) -> None:
        self._put("files", obj_id, index, data)

    def get_upload_part(self, obj_id: str, index: int) -> bytes:
        return self._get("files", obj_id, index)

    def delete_file(self, obj_id: str, size: int, *, kind: str = "files") -> None:
        self._valid_id(obj_id)
        if kind not in ("files", "snapshots", "sessionmeta"):
            raise VaultError("Invalid delete kind")
        count = (size + CHUNK_BYTES - 1) // CHUNK_BYTES
        if size < 1 or count > 128:
            raise VaultError("Invalid delete size")
        for index in range(count):
            self._request("DELETE", f"/{kind}/{obj_id}/{index}")

    def upload_file(self, local: Path, object_id: str, *, kind="files",
                    expected_sha256: str | None = None):
        self._valid_id(object_id)
        file_size = local.stat().st_size
        max_size = MAX_PRODUCT_BYTES if kind == "files" else MAX_SQLITE_BYTES
        if file_size < 1 or file_size > max_size:
            raise VaultError("File exceeds private storage limits")
        if expected_sha256 is not None and not _HASH.fullmatch(expected_sha256):
            raise VaultError("Invalid expected SHA256")
        h = hashlib.sha256()
        chunks = 0
        with local.open("rb") as src:
            while part := src.read(CHUNK_BYTES):
                h.update(part)
                self._put(kind, object_id, chunks, part)
                chunks += 1
        if expected_sha256 is not None and h.hexdigest() != expected_sha256:
            raise VaultError("Local and expected file digests differ")
        return {"id": object_id, "chunks": chunks, "size": file_size, "sha256": h.hexdigest()}

    def restore_file(self, target: Path, obj_id: str, size: int, sha256: str,
                     *, kind="files"):
        self._valid_id(obj_id)
        limit = MAX_PRODUCT_BYTES if kind == "files" else MAX_SQLITE_BYTES
        if not isinstance(size, int) or not 0 < size <= limit or not _HASH.fullmatch(sha256):
            raise VaultError("Invalid persisted metadata")
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name("." + target.name + "." + secrets.token_hex(6) + ".partial")
        h = hashlib.sha256()
        received = 0
        try:
            with partial.open("xb") as dest:
                os.chmod(partial, 0o600)
                count = (size + CHUNK_BYTES - 1) // CHUNK_BYTES
                for index in range(count):
                    part = self._get(kind, obj_id, index)
                    expected = min(CHUNK_BYTES, size - received)
                    if len(part) != expected:
                        raise VaultError("Incomplete cloud file")
                    dest.write(part)
                    h.update(part)
                    received += len(part)
                dest.flush()
                os.fsync(dest.fileno())
            if received != size or h.hexdigest() != sha256:
                raise VaultError("Cloud file integrity mismatch")
            os.replace(partial, target)
            return target
        finally:
            partial.unlink(missing_ok=True)

    def read_snapshot_pointer(self):
        try:
            data = self._request("GET", "/pointer")
        except FileNotFoundError:
            return None
        try:
            pointer = json.loads(data)
        except (TypeError, ValueError) as exc:
            raise VaultError("Corrupt cloud database pointer") from exc
        if not isinstance(pointer, dict) or not _NAME.fullmatch(pointer.get("id", "")):
            raise VaultError("Invalid cloud database pointer")
        return pointer

    def snapshot_sqlite(self, sqlite_path: Path):
        """Backup committed SQLite pages; publish pointer only after all chunks."""
        if not sqlite_path.is_file():
            raise VaultError("Local customer database is missing")
        existing = self.read_snapshot_pointer()
        previous = existing["generation"] if existing else 0
        # Guaranteed monotonic for one-writer deployment (existing API has one instance).
        generation = max(time.time_ns() // 1000, int(previous) + 1)
        with tempfile.TemporaryDirectory(prefix="eri-sqlite-backup-") as folder:
            backup = Path(folder) / "db.sqlite3"
            with sqlite3.connect(sqlite_path) as src:
                with sqlite3.connect(backup) as dest:
                    src.backup(dest)
            result = self.upload_file(backup, secrets.token_hex(16), kind="snapshots")
        pointer = {"id": result["id"], "generation": generation,
                   "size": result["size"], "chunks": result["chunks"],
                   "sha256": result["sha256"]}
        data = json.dumps(pointer, separators=(",", ":")).encode("utf-8")
        confirmation = json.loads(self._request("PUT", "/pointer", data, "application/json"))
        if confirmation.get("ok") is not True or confirmation.get("generation") != generation:
            raise VaultError("Database snapshot not committed")
        return pointer

    def restore_sqlite(self, target: Path):
        """Never initialize an empty DB if cloud snapshot is absent or corrupt."""
        pointer = self.read_snapshot_pointer()
        if not pointer:
            raise VaultError("Cloud customer DB snapshot missing; cannot erase live accounts")
        self._valid_id(pointer["id"])
        size = pointer.get("size")
        sha256 = pointer.get("sha256")
        with tempfile.TemporaryDirectory(prefix="eri-restore-check-") as folder:
            destination = Path(folder) / "db.sqlite3"
            self.restore_file(destination, pointer["id"], size, sha256, kind="snapshots")
            with sqlite3.connect(destination) as restored:
                check = restored.execute("PRAGMA integrity_check").fetchone()[0]
                if check != "ok":
                    raise VaultError("Cloud SQLite snapshot fails integrity check")
            target.parent.mkdir(parents=True, exist_ok=True)
            staging = target.with_name("." + target.name + ".restoring")
            try:
                staging.write_bytes(destination.read_bytes())
                os.chmod(staging, 0o600)
                os.replace(staging, target)
            finally:
                staging.unlink(missing_ok=True)
        return pointer
