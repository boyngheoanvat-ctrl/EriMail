"""Opt-in Apple profile-based UDID collection for purchased certificate orders.

Only UDID is requested. A unique one-time challenge binds the device callback
to the authenticated buyer's certificate order. CMS signatures must validate
against Apple's pinned iPhone Device CA; unverified data is never marked scanned.
Manual entry is available but is explicitly labeled self-reported.
"""
import hashlib
import hmac
import io
import os
import plistlib
import re
import secrets
import subprocess
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response, RedirectResponse
from pydantic import BaseModel, Field

API_ORIGIN = os.getenv("ERI_PUBLIC_API_ORIGIN", "https://eri-api-dag8.onrender.com").rstrip("/")
WEB_ORIGIN = os.getenv("ERI_STORE_ORIGIN", "https://eri-store-web.onrender.com").rstrip("/")
APPLE_DEVICE_CA = Path(__file__).with_name("apple_iphone_device_ca.pem")
UDID_PATTERN = re.compile(r"^(?:[0-9a-fA-F]{24,40}|[0-9a-fA-F]{8}-[0-9a-fA-F]{16,32})$")
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{24,64}$")
ORDER_PATTERN = re.compile(r"^[A-F0-9]{12}$")
SCAN_TTL_MINUTES = 15
MAX_CMS_LENGTH = 128 * 1024


class ManualDeviceUDID(BaseModel):
    udid: str = Field(min_length=24, max_length=45)
    consent: bool


def _digest(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _utc_now():
    return datetime.now(timezone.utc)


def _order_for_customer(connection, order_id, customer_id):
    if not ORDER_PATTERN.fullmatch(order_id):
        raise HTTPException(404, "Không tìm thấy đơn Chứng chỉ")
    with connection() as db:
        row = db.execute(
            "SELECT id,kind,status FROM orders WHERE id=? AND customer_id=?",
            (order_id, customer_id)
        ).fetchone()
    if not row or row["kind"] != "certificate" or row["status"] == "cancelled":
        raise HTTPException(404, "Không tìm thấy đơn Chứng chỉ thuộc tài khoản")
    return row


def _verify_apple_cms(payload: bytes) -> dict:
    """Verify the CMS integrity AND signer chain to Apple's pinned device CA.

    Fails closed. Never parse unauthenticated XML or trust a client-supplied UDID.
    The archival Apple Device CA is expired; Apple says validity dates of device
    certificates under it must be ignored. Certificate trust and signature still
    need to validate. Newer device issuers may need separately vetted anchors.
    """
    if not payload or len(payload) > MAX_CMS_LENGTH:
        raise HTTPException(413, "Phản hồi hồ sơ vượt giới hạn")
    if not APPLE_DEVICE_CA.is_file():
        raise HTTPException(503, "Chưa cấu hình xác thực thiết bị Apple")
    try:
        with tempfile.TemporaryDirectory(prefix="eri-udid-") as d:
            root = Path(d)
            cms = root / "device.cms"
            decoded = root / "verified.plist"
            signer = root / "signer.pem"
            cms.write_bytes(payload)
            result = subprocess.run(
                ["openssl", "cms", "-verify", "-inform", "DER", "-binary",
                 "-noverify", "-in", str(cms), "-out", str(decoded),
                 "-signer", str(signer)],
                capture_output=True, timeout=10, check=False
            )
            if result.returncode or not signer.is_file():
                raise ValueError("CMS signature verification failed")
            trust = subprocess.run(
                ["openssl", "verify", "-partial_chain", "-no_check_time",
                 "-purpose", "any", "-CAfile", str(APPLE_DEVICE_CA),
                 str(signer)],
                capture_output=True, timeout=10, check=False
            )
            if trust.returncode:
                raise ValueError("Signer is not trusted under Apple iPhone Device CA")
            raw = decoded.read_bytes()
            if len(raw) > 32 * 1024:
                raise ValueError("Device property list too large")
            attributes = plistlib.loads(raw)
            if not isinstance(attributes, dict):
                raise ValueError("Device response was not a dictionary")
            return attributes
    except (OSError, subprocess.TimeoutExpired, ValueError, plistlib.InvalidFileException):
        raise HTTPException(422, "Không xác minh được phản hồi từ thiết bị Apple")


def install_udid_routes(app, connection, require_customer, require_admin):
    # Lightweight schema migration; preserves all existing customers and orders.
    with connection() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS cert_order_udids (
          order_id TEXT PRIMARY KEY,
          customer_id INTEGER NOT NULL,
          udid TEXT NOT NULL,
          source TEXT NOT NULL CHECK(source IN ('apple_profile','self_reported')),
          submitted_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS cert_udid_sessions (
          token_hash TEXT PRIMARY KEY,
          order_id TEXT NOT NULL,
          customer_id INTEGER NOT NULL,
          created_at TEXT NOT NULL,
          expires_at TEXT NOT NULL,
          completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_cert_udid_session_order
          ON cert_udid_sessions(order_id, customer_id);
        """)

    @app.post("/api/v1/me/certificate-orders/{order_id}/udid-sessions", status_code=201)
    def start_scan(order_id: str, customer=Depends(require_customer)):
        _order_for_customer(connection, order_id, customer["id"])
        token = secrets.token_urlsafe(32)
        now = _utc_now()
        expires = now + timedelta(minutes=SCAN_TTL_MINUTES)
        with connection() as db:
            db.execute(
                "DELETE FROM cert_udid_sessions WHERE order_id=? AND customer_id=?",
                (order_id, customer["id"])
            )
            db.execute(
                "INSERT INTO cert_udid_sessions(token_hash,order_id,customer_id,"
                "created_at,expires_at) VALUES(?,?,?,?,?)",
                (_digest(token), order_id, customer["id"], now.isoformat(),
                 expires.isoformat())
            )
        return {
            "scan_token": token, "expires_in_seconds": SCAN_TTL_MINUTES * 60,
            "profile_url": API_ORIGIN + "/api/v1/cert-udid/profile/" + token,
            "qr_url": API_ORIGIN + "/api/v1/cert-udid/qr/" + token,
            "order_id": order_id
        }

    def _lookup_session(token):
        if not TOKEN_PATTERN.fullmatch(token):
            raise HTTPException(404, "Liên kết quét không hợp lệ")
        with connection() as db:
            row = db.execute(
                "SELECT token_hash,order_id,customer_id,expires_at,completed_at "
                "FROM cert_udid_sessions WHERE token_hash=?",
                (_digest(token),)
            ).fetchone()
        if not row:
            raise HTTPException(404, "Lượt lấy UDID không còn tồn tại")
        if datetime.fromisoformat(row["expires_at"]) <= _utc_now():
            raise HTTPException(410, "Liên kết lấy UDID đã hết hạn")
        return dict(row)

    @app.get("/api/v1/me/certificate-orders/{order_id}/udid-sessions/{token}")
    def scan_status(order_id: str, token: str, customer=Depends(require_customer)):
        _order_for_customer(connection, order_id, customer["id"])
        row = _lookup_session(token)
        if row["order_id"] != order_id or row["customer_id"] != customer["id"]:
            raise HTTPException(404, "Không tìm thấy lượt lấy UDID")
        with connection() as db:
            device = db.execute(
                "SELECT udid,source,submitted_at FROM cert_order_udids WHERE order_id=?",
                (order_id,)
            ).fetchone()
        if row["completed_at"] is not None and device is not None:
            return {"complete": True, "udid": device["udid"],
                    "source": device["source"], "submitted_at": device["submitted_at"]}
        return {"complete": False}

    @app.get("/api/v1/cert-udid/profile/{token}")
    def download_profile(token: str):
        row = _lookup_session(token)
        if row["completed_at"]:
            raise HTTPException(409, "Lượt lấy UDID này đã hoàn thành")
        profile = {
            "PayloadContent": {
                "URL": API_ORIGIN + "/api/v1/cert-udid/callback/" + token,
                "DeviceAttributes": ["UDID"],
                "Challenge": token,
            },
            "PayloadOrganization": "ERI STORE",
            "PayloadDisplayName": "ERI STORE - Xác nhận UDID",
            "PayloadVersion": 1,
            "PayloadUUID": str(uuid.uuid4()).upper(),
            "PayloadIdentifier": "com.eringuyen.store.certificate.udid",
            "PayloadDescription":
                "Chỉ yêu cầu UDID để đăng ký chứng chỉ cho đơn hàng bạn đã chọn. "
                "Thông tin sẽ được gửi cho ERI STORE. "
                "Không yêu cầu IMEI, serial, mật khẩu hay quyền quản lý thiết bị.",
            "PayloadType": "Profile Service",
        }
        return Response(
            plistlib.dumps(profile, fmt=plistlib.FMT_XML),
            media_type="application/x-apple-aspen-config",
            headers={
                "Content-Disposition": 'attachment; filename="ERI-STORE-UDID.mobileconfig"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer"
            }
        )

    @app.get("/api/v1/cert-udid/qr/{token}")
    def scan_qr(token: str):
        _lookup_session(token)
        try:
            import segno
            qr = segno.make(API_ORIGIN + "/api/v1/cert-udid/profile/" + token)
            data = io.BytesIO()
            qr.save(data, kind="svg", scale=5, border=2)
            return Response(data.getvalue(), media_type="image/svg+xml", headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; style-src 'none'"
            })
        except ImportError:
            raise HTTPException(503, "Chưa cấu hình tạo mã QR")

    @app.post("/api/v1/cert-udid/callback/{token}")
    async def device_callback(token: str, request: Request):
        row = _lookup_session(token)
        if row["completed_at"]:
            raise HTTPException(409, "Lượt này đã được xử lý")
        content_type = request.headers.get("content-type", "").lower()
        if not any(x in content_type for x in
                   ("application/pkcs7-signature", "application/x-apple-aspen-config",
                    "application/pkcs7-mime", "application/octet-stream")):
            raise HTTPException(415, "Yêu cầu không phải phản hồi hồ sơ thiết bị")
        pieces = []
        received = 0
        async for part in request.stream():
            received += len(part)
            if received > MAX_CMS_LENGTH:
                raise HTTPException(413, "Phản hồi từ thiết bị quá lớn")
            pieces.append(part)
        attributes = _verify_apple_cms(b"".join(pieces))
        value = attributes.get("UDID")
        if not isinstance(value, str) or not UDID_PATTERN.fullmatch(value.strip()):
            raise HTTPException(422, "Thiết bị không cung cấp UDID hợp lệ")
        challenge = attributes.get("CHALLENGE")
        if isinstance(challenge, bytes):
            try: challenge = challenge.decode("utf-8")
            except UnicodeError: challenge = ""
        if not isinstance(challenge, str) or not hmac.compare_digest(challenge, token):
            raise HTTPException(403, "Mã xác nhận UDID không khớp")
        udid = value.strip().upper()
        now = _utc_now().isoformat()
        with connection() as db:
            current = db.execute(
                "SELECT expires_at,completed_at FROM cert_udid_sessions WHERE token_hash=?",
                (row["token_hash"],)
            ).fetchone()
            order = db.execute(
                "SELECT id FROM orders WHERE id=? AND customer_id=? "
                "AND kind='certificate' AND status!='cancelled'",
                (row["order_id"], row["customer_id"])
            ).fetchone()
            if (not current or current["completed_at"] or not order or
                datetime.fromisoformat(current["expires_at"]) <= _utc_now()):
                raise HTTPException(409, "Lượt quét đã hết hạn hoặc đơn hàng không còn")
            db.execute(
                "INSERT INTO cert_order_udids(order_id,customer_id,udid,source,submitted_at)"
                " VALUES(?,?,?,?,?) ON CONFLICT(order_id) DO UPDATE SET "
                "udid=excluded.udid,source=excluded.source,"
                "submitted_at=excluded.submitted_at",
                (row["order_id"], row["customer_id"], udid, "apple_profile", now)
            )
            db.execute(
                "UPDATE cert_udid_sessions SET completed_at=? WHERE token_hash=?",
                (now, row["token_hash"])
            )
        # Do not include UDID or identifying data in redirected URLs.
        return RedirectResponse(WEB_ORIGIN + "/?udid_scan=complete", status_code=303)

    @app.put("/api/v1/me/certificate-orders/{order_id}/udid")
    def submit_manual_udid(order_id: str, data: ManualDeviceUDID,
                           customer=Depends(require_customer)):
        _order_for_customer(connection, order_id, customer["id"])
        if not data.consent:
            raise HTTPException(422, "Cần đồng ý gửi UDID cho cửa hàng")
        udid = data.udid.strip().upper()
        if not UDID_PATTERN.fullmatch(udid):
            raise HTTPException(422, "UDID không đúng định dạng")
        with connection() as db:
            db.execute(
                "INSERT INTO cert_order_udids(order_id,customer_id,udid,source,submitted_at)"
                " VALUES(?,?,?,?,?) ON CONFLICT(order_id) DO UPDATE SET "
                "udid=excluded.udid,source=excluded.source,"
                "submitted_at=excluded.submitted_at",
                (order_id,customer["id"],udid,"self_reported",_utc_now().isoformat())
            )
        return {"saved": True, "udid": udid, "source": "self_reported"}


    @app.get("/api/v1/admin/certificate-udids", dependencies=[Depends(require_admin)])
    def admin_certificate_udids():
        with connection() as db:
            rows = db.execute(
                "SELECT d.order_id,d.udid,d.source,d.submitted_at,"
                "o.product_name,o.amount_vnd,o.status AS order_status,"
                "c.username AS buyer_username,c.display_name AS buyer_name,"
                "c.contact AS buyer_contact "
                "FROM cert_order_udids d JOIN orders o ON o.id=d.order_id "
                "LEFT JOIN customers c ON c.id=o.customer_id "
                "ORDER BY d.submitted_at DESC LIMIT 300"
            ).fetchall()
        return {"items": [dict(r) for r in rows]}

    @app.delete("/api/v1/me/certificate-orders/{order_id}/udid")
    def remove_own_udid(order_id: str, customer=Depends(require_customer)):
        _order_for_customer(connection, order_id, customer["id"])
        with connection() as db:
            db.execute("DELETE FROM cert_order_udids WHERE order_id=? AND customer_id=?",
                       (order_id, customer["id"]))
            db.execute("DELETE FROM cert_udid_sessions WHERE order_id=? AND customer_id=?",
                       (order_id, customer["id"]))
        return {"deleted": True}
