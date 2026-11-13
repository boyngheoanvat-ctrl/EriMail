"""Resumable browser-to-Render IPA signing jobs.

Small verified chunk requests avoid gateway timeouts on 200+ MiB IPA files.
Signing is queued after upload and runs without an open browser connection.
The default filesystem is EPHEMERAL: production requires persistent storage
to survive Render instance replacement. Never retain the P12 password on disk.
"""
import hashlib
import importlib.util
import json
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

CHUNK_BYTES = 2 * 1024 * 1024
MAX_IPA = 512 * 1024 * 1024
MAX_CERT = 10 * 1024 * 1024
JOB_TTL = 6 * 3600
BOOT = secrets.token_hex(12)
ROOT = Path(os.getenv("ERI_SIGN_JOBS_DIR", str(Path(tempfile.gettempdir()) / "eri-sign-jobs")))
LOCK = threading.RLock()
ACTIVE_LIMIT = 2
SIGN_SLOTS = threading.BoundedSemaphore(value=1)


class JobCreate(BaseModel):
    filename: str = Field(min_length=5, max_length=240)
    size_bytes: int = Field(gt=0, le=MAX_IPA)


def _paths(job_id: str):
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise HTTPException(404, "Không tìm thấy lượt tải")
    folder = ROOT / job_id
    return folder, folder / "state.json", folder / "input.ipa"


def _save(folder: Path, state: dict):
    temp = folder / ("." + secrets.token_hex(8) + ".writing")
    temp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, folder / "state.json")


def _read(job_id: str, owner: int):
    folder, statefile, source = _paths(job_id)
    try:
        state = json.loads(statefile.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(410, "Phiên xử lý không còn trên máy chủ. Cần tải lại.")
    if state.get("owner") != owner:
        raise HTTPException(404, "Không tìm thấy lượt tải")
    if state["state"] == "processing" and state.get("boot") != BOOT:
        state["state"] = "failed"
        state["error"] = "Máy chủ đã khởi động lại trong khi ký. Hãy thử ký lại."
        _save(folder, state)
        for name in ("cert.p12", "profile.mobileprovision"):
            (folder / name).unlink(missing_ok=True)
    return folder, source, state


def _purge_old():
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    now = time.time()
    for folder in ROOT.iterdir():
        if not folder.is_dir() or not re.fullmatch(r"[a-f0-9]{32}", folder.name):
            continue
        try:
            state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
            when = state.get("created", 0)
            if when and now - when > JOB_TTL:
                shutil.rmtree(folder)
        except (OSError, ValueError):
            pass


def _received(folder: Path, count: int):
    markers = folder / "received"
    return [i for i in range(count) if (markers / f"{i:04d}.sha256").is_file()]


def _public_state(state: dict, folder: Path):
    pieces = (state["size_bytes"] + CHUNK_BYTES - 1) // CHUNK_BYTES
    received = _received(folder, pieces)
    bytes_done = sum(min(CHUNK_BYTES, state["size_bytes"] - i * CHUNK_BYTES) for i in received)
    info = {
        "job_id": state["job_id"], "state": state["state"],
        "size_bytes": state["size_bytes"], "chunk_bytes": CHUNK_BYTES,
        "total_chunks": pieces, "received": received,
        "uploaded_bytes": bytes_done,
        "error": state.get("error"),
    }
    if state["state"] == "completed":
        info["result"] = state.get("result")
    return info


def _write_small(upload: UploadFile, target: Path, suffix: str):
    if not (upload.filename or "").lower().endswith(suffix):
        raise HTTPException(422, "Chọn file " + suffix + " hợp lệ")
    size = 0
    with target.open("xb") as output:
        os.chmod(target, 0o600)
        while chunk := upload.file.read(256 * 1024):
            size += len(chunk)
            if size > MAX_CERT:
                raise HTTPException(413, "Tệp chứng chỉ vượt 10 MB")
            output.write(chunk)
    if size == 0:
        raise HTTPException(422, "Tệp chứng chỉ rỗng")


def register_signing_jobs(router: APIRouter, require_customer, inspect, validate,
                          details, register_output):
    @router.post("/api/v1/tools/ipa/sign-jobs", status_code=201)
    def create_sign_job(item: JobCreate, customer=Depends(require_customer)):
        if not item.filename.lower().endswith(".ipa"):
            raise HTTPException(422, "Chọn file IPA hợp lệ")
        with LOCK:
            _purge_old()
            existing = 0
            for job_folder in ROOT.iterdir():
                if not job_folder.is_dir(): continue
                try:
                    state = json.loads((job_folder / "state.json").read_text())
                except (OSError, ValueError):
                    continue
                if state.get("state") in ("uploading", "processing"):
                    existing += 1
            if existing >= ACTIVE_LIMIT:
                raise HTTPException(503, "Máy chủ đang xử lý nhiều IPA. Vui lòng thử lại.")
            job_id = secrets.token_hex(16)
            folder, _, source = _paths(job_id)
            folder.mkdir(mode=0o700)
            (folder / "received").mkdir(mode=0o700)
            with source.open("xb") as output:
                output.truncate(item.size_bytes)
            os.chmod(source, 0o600)
            state = {
                "job_id": job_id, "owner": customer["id"],
                "filename": Path(item.filename).name, "size_bytes": item.size_bytes,
                "state": "uploading", "created": time.time(),
            }
            _save(folder, state)
        return _public_state(state, folder)

    @router.get("/api/v1/tools/ipa/sign-jobs/{job_id}")
    def get_sign_job(job_id: str, customer=Depends(require_customer)):
        with LOCK:
            folder, source, state = _read(job_id, customer["id"])
            if not source.is_file() and state["state"] == "uploading":
                raise HTTPException(410, "Tệp upload đã bị máy chủ xóa, cần tải lại")
            return _public_state(state, folder)

    @router.put("/api/v1/tools/ipa/sign-jobs/{job_id}/chunks/{index}")
    async def upload_chunk(job_id: str, index: int, request: Request,
                           x_eri_chunk_sha256: str = Header(...),
                           customer=Depends(require_customer)):
        if not re.fullmatch(r"[a-f0-9]{64}", x_eri_chunk_sha256):
            raise HTTPException(422, "Checksum không hợp lệ")
        with LOCK:
            folder, source, state = _read(job_id, customer["id"])
            if state["state"] != "uploading":
                raise HTTPException(409, "Tác vụ đã đóng phần upload")
            count = (state["size_bytes"] + CHUNK_BYTES - 1) // CHUNK_BYTES
            if not 0 <= index < count:
                raise HTTPException(422, "Số thứ tự phần không hợp lệ")
            size = min(CHUNK_BYTES, state["size_bytes"] - index * CHUNK_BYTES)
            marker = folder / "received" / f"{index:04d}.sha256"
            if marker.is_file():
                if marker.read_text() != x_eri_chunk_sha256:
                    raise HTTPException(409, "Phần tệp có checksum khác dữ liệu đã nhận")
                return {"index": index, "received_bytes": size, "duplicate": True}
        # Read only one bounded chunk; reject oversized bodies.
        data = bytearray()
        async for piece in request.stream():
            data.extend(piece)
            if len(data) > size:
                raise HTTPException(413, "Chunk quá lớn")
        if len(data) != size or hashlib.sha256(data).hexdigest() != x_eri_chunk_sha256:
            raise HTTPException(422, "Phần tệp thiếu hoặc bị thay đổi")
        with LOCK:
            folder, source, state = _read(job_id, customer["id"])
            if state["state"] != "uploading":
                raise HTTPException(409, "Tác vụ đã kết thúc upload")
            marker = folder / "received" / f"{index:04d}.sha256"
            if marker.is_file():
                if marker.read_text() != x_eri_chunk_sha256:
                    raise HTTPException(409, "Checksum không khớp")
                return {"index": index, "received_bytes": size, "duplicate": True}
            with source.open("r+b") as dst:
                dst.seek(index * CHUNK_BYTES)
                dst.write(data)
                dst.flush()
            tmp = marker.with_suffix(".tmp")
            tmp.write_text(x_eri_chunk_sha256)
            os.replace(tmp, marker)
        return {"index": index, "received_bytes": size, "duplicate": False}

    def _finish_job(job_id: str, password: str):
        try:
            folder, meta_file, source = _paths(job_id)
            with LOCK:
                state = json.loads(meta_file.read_text(encoding="utf-8"))
                if state["state"] != "processing": return
            if not SIGN_SLOTS.acquire(blocking=False):
                raise RuntimeError("Máy chủ đang ký IPA khác, hãy thử lại")
            try:
                import ipasign
                inspection = inspect(source)
                if inspection["zip_password_protected"] or inspection["fairplay_encrypted"] is True:
                    raise ValueError("IPA còn mã hóa không thể ký lại trực tiếp")
                output = folder / "signed.ipa"
                signed = ipasign.Key(str(folder / "cert.p12"),
                                     str(folder / "profile.mobileprovision"),
                                     password).sign(str(source), str(output))
                if not output.is_file() or output.stat().st_size == 0:
                    raise ValueError("Bộ ký IPA không tạo ra kết quả")
                with zipfile.ZipFile(output) as archive:
                    validate(archive)
                metadata = details(output)
                if metadata["bundle_id"] != str(signed.bundle_id):
                    raise ValueError("Bundle ID không khớp với kết quả ký")
                result = register_output(output, metadata)
                with LOCK:
                    state = json.loads(meta_file.read_text(encoding="utf-8"))
                    state.update(state="completed", result={**result,
                                 "app_name": metadata["title"],
                                 "bundle_id": metadata["bundle_id"],
                                 "message": "IPA đã ký xong. Mở Safari trên iPhone để cài."})
                    _save(folder, state)
            finally:
                SIGN_SLOTS.release()
        except Exception:
            with LOCK:
                try:
                    folder, meta_file, _ = _paths(job_id)
                    state = json.loads(meta_file.read_text(encoding="utf-8"))
                    state["state"] = "failed"
                    state["error"] = "Không ký được IPA. Kiểm tra P12, mật khẩu, provisioning profile, Bundle ID và dung lượng bộ nhớ máy chủ."
                    _save(folder, state)
                except (OSError, ValueError): pass
        finally:
            # Never retain P12, profile, or plain password after processing.
            try:
                folder, _, _ = _paths(job_id)
                for name in ("cert.p12", "profile.mobileprovision"):
                    (folder / name).unlink(missing_ok=True)
                (folder / "input.ipa").unlink(missing_ok=True)
            except OSError: pass

    @router.post("/api/v1/tools/ipa/sign-jobs/{job_id}/start", status_code=202)
    def start_sign_job(job_id: str, background: BackgroundTasks,
                       p12: UploadFile = File(...),
                       mobileprovision: UploadFile = File(...),
                       password: str = Form(...),
                       customer=Depends(require_customer)):
        if not password or len(password) > 512:
            raise HTTPException(422, "Mật khẩu P12 không hợp lệ")
        if importlib.util.find_spec("ipasign") is None:
            raise HTTPException(503, "Máy chủ chưa có bộ ký IPA")
        with LOCK:
            folder, source, state = _read(job_id, customer["id"])
            if state["state"] != "uploading":
                raise HTTPException(409, "Tác vụ này đã bắt đầu xử lý")
            if not source.is_file() or source.stat().st_size != state["size_bytes"]:
                raise HTTPException(410, "Tệp tải lên đã bị mất")
            count = (state["size_bytes"] + CHUNK_BYTES - 1) // CHUNK_BYTES
            if len(_received(folder, count)) != count:
                raise HTTPException(409, "IPA chưa tải đủ")
            # Small private inputs only. No P12 password is written to disk.
            try:
                _write_small(p12, folder / "cert.p12", ".p12")
                _write_small(mobileprovision, folder / "profile.mobileprovision", ".mobileprovision")
            except Exception:
                (folder / "cert.p12").unlink(missing_ok=True)
                (folder / "profile.mobileprovision").unlink(missing_ok=True)
                raise
            state["state"] = "processing"
            state["boot"] = BOOT
            _save(folder, state)
            background.add_task(_finish_job, job_id, password)
        return {"job_id": job_id, "state": "processing",
                "message": "Máy chủ đang ký IPA; bạn có thể đóng trình duyệt và mở lại để xem kết quả."}

    return router
