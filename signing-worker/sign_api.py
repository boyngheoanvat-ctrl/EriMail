"""Private macOS ERI signing API. Run behind authenticated HTTPS/Tailscale Serve.

Separate from the public catalog backend. Do not expose directly to the Internet.
"""
import asyncio
import hmac
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

app = FastAPI(title="ERI Private Signing API", docs_url=None, redoc_url=None, openapi_url=None)
MAX_IPA = 2 * 1024**3
MAX_P12 = 10 * 1024**2
MAX_PROFILE = 10 * 1024**2
_lock = asyncio.Lock()
WORKER = Path(__file__).with_name("sign_worker.py")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "ERI Private Signing"}


async def save_upload(upload: UploadFile, target: Path, limit: int):
    total = 0
    with target.open("wb") as out:
        while chunk := await upload.read(1024 * 1024):
            total += len(chunk)
            if total > limit:
                raise HTTPException(413, "Upload exceeds permitted size")
            out.write(chunk)
    if not total:
        raise HTTPException(422, "Empty upload")


@app.post("/api/v1/sign")
async def sign_ipa(
    ipa: UploadFile = File(...),
    p12: UploadFile = File(...),
    mobileprovision: UploadFile = File(...),
    password: str = Form(...),
    identifier: str = Form(""),
    name: str = Form(""),
    remove_embedded: bool = Form(False),
    x_eri_sign_token: str | None = Header(default=None),
):
    expected = os.getenv("ERI_SIGN_TOKEN", "")
    if not expected or not x_eri_sign_token or not hmac.compare_digest(expected, x_eri_sign_token):
        raise HTTPException(401, "Invalid signing token")
    if sys.platform != "darwin":
        raise HTTPException(503, "Signing requires macOS")
    if identifier.strip() or name.strip():
        raise HTTPException(422, "Changing bundle ID or app name is not supported")
    if not password or len(password) > 1024:
        raise HTTPException(422, "Invalid P12 password")
    if _lock.locked():
        raise HTTPException(429, "Signing worker is busy; retry later")

    await _lock.acquire()
    try:
        folder = tempfile.TemporaryDirectory(prefix="eri-api-")
        root = Path(folder.name)
        try:
            source = root / "input.ipa"
            cert = root / "certificate.p12"
            profile = root / "profile.mobileprovision"
            output = root / "signed.ipa"
            await save_upload(ipa, source, MAX_IPA)
            await save_upload(p12, cert, MAX_P12)
            await save_upload(mobileprovision, profile, MAX_PROFILE)
            env = dict(os.environ)
            env["ERI_P12_PASSWORD"] = password
            try:
                # Keep worker stderr and secrets out of HTTP responses and access logs.
                result = await asyncio.to_thread(
                    subprocess.run,
                    [sys.executable, str(WORKER), str(source), str(cert), str(profile), str(output)],
                    env=env, capture_output=True, timeout=600, check=False,
                )
            except subprocess.TimeoutExpired:
                raise HTTPException(504, "Signing timed out")
            finally:
                env.pop("ERI_P12_PASSWORD", None)
            if result.returncode != 0 or not output.is_file():
                raise HTTPException(422, "Signing failed; verify certificate, provisioning profile and IPA compatibility")
            # FileResponse streams the output and removes all temporary signing material afterward.
            return FileResponse(
                output, media_type="application/octet-stream",
                filename="ERI-signed.ipa",
                background=BackgroundTask(folder.cleanup),
                headers={"Cache-Control": "no-store"},
            )
        except BaseException:
            folder.cleanup()
            raise
    finally:
        _lock.release()
