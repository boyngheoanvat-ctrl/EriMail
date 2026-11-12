"""Private ERI signing API. Run only on an isolated macOS host behind HTTPS.
Set ERI_SIGNING_KEY, ERI_PUBLIC_BASE_URL and optionally ERI_SIGNING_MAX_BYTES.
The signing worker must be present at ../signing-worker/sign_worker.py.
"""
import os
import sys
import time
import secrets
import subprocess
import tempfile
from pathlib import Path
from fastapi import APIRouter, Header, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

router = APIRouter()
SIGNING_KEY = os.getenv("ERI_SIGNING_KEY", "")
PUBLIC_BASE = os.getenv("ERI_PUBLIC_BASE_URL", "").rstrip("/")
MAX_BYTES = int(os.getenv("ERI_SIGNING_MAX_BYTES", str(512 * 1024 * 1024)))
WORKER = Path(__file__).resolve().parent.parent / "signing-worker" / "sign_worker.py"
OUTPUT_DIR = Path(tempfile.gettempdir()) / "eri-signed-results"
EXPIRY_SECONDS = 1800
# In-memory one-time download tickets. A restart invalidates tickets.
tickets = {}

def authorize(value):
    if not SIGNING_KEY or not value or not value.startswith("Bearer "):
        raise HTTPException(403, "Signing is not configured or unauthorized")
    if not secrets.compare_digest(value[7:], SIGNING_KEY):
        raise HTTPException(403, "Invalid signing credential")

def purge():
    now = time.time()
    for token, (path, expiry) in list(tickets.items()):
        if expiry < now:
            tickets.pop(token, None)
            Path(path).unlink(missing_ok=True)

def write_upload(upload, path, budget):
    total = 0
    with path.open("wb") as out:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > budget:
                raise HTTPException(413, "File too large")
            out.write(chunk)

@router.post("/api/v1/sign")
def sign_ipa(
    ipa: UploadFile = File(...),
    p12: UploadFile = File(...),
    mobileprovision: UploadFile = File(...),
    password: str = Form(...),
    identifier: str = Form(""),
    name: str = Form(""),
    remove_embedded: bool = Form(False),
    authorization: str | None = Header(default=None),
):
    authorize(authorization)
    if sys.platform != "darwin" or not WORKER.is_file() or not PUBLIC_BASE.startswith("https://"):
        raise HTTPException(503, "macOS signing worker or HTTPS public URL not configured")
    if not password or len(password) > 1024:
        raise HTTPException(400, "Invalid P12 password")
    if identifier or name or remove_embedded:
        raise HTTPException(422, "Bundle edits and remove_embedded are not yet supported by the worker")
    purge()
    OUTPUT_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="eri-job-") as directory:
        root = Path(directory)
        input_ipa = root / "input.ipa"
        input_p12 = root / "cert.p12"
        input_profile = root / "profile.mobileprovision"
        write_upload(ipa, input_ipa, MAX_BYTES)
        write_upload(p12, input_p12, 10 * 1024 * 1024)
        write_upload(mobileprovision, input_profile, 10 * 1024 * 1024)
        token = secrets.token_urlsafe(32)
        output = OUTPUT_DIR / (token + ".ipa")
        env = dict(os.environ, ERI_P12_PASSWORD=password)
        try:
            subprocess.run(
                [sys.executable, str(WORKER), str(input_ipa), str(input_p12), str(input_profile), str(output)],
                env=env, capture_output=True, timeout=600, check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            output.unlink(missing_ok=True)
            raise HTTPException(422, "Signing failed; check certificate, provisioning profile and IPA compatibility")
        tickets[token] = (str(output), time.time() + EXPIRY_SECONDS)
        return {"download_url": PUBLIC_BASE + "/api/v1/sign/download/" + token,
                "message": "IPA đã ký; liên kết tải một lần, hết hạn sau 30 phút."}

@router.get("/api/v1/sign/download/{token}")
def download_signed(token: str):
    purge()
    item = tickets.pop(token, None)
    if not item:
        raise HTTPException(404, "Download expired or already used")
    path, expiry = item
    if expiry < time.time() or not Path(path).is_file():
        Path(path).unlink(missing_ok=True)
        raise HTTPException(404, "Download expired")
    return FileResponse(path, media_type="application/octet-stream",
                        filename="ERI-signed.ipa",
                        background=BackgroundTask(lambda: Path(path).unlink(missing_ok=True)))
