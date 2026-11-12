"""ERI NGUYEN backend — independently hosted API. No third-party scraping or fake signing."""
import os
import sqlite3
import secrets
import hmac
import hashlib
import json
import urllib.request
import urllib.error
import re
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Header, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, HttpUrl

DB = Path(os.getenv("ERI_DB_PATH", "./eri.sqlite3"))
ADMIN_KEY = os.getenv("ERI_ADMIN_KEY", "")
app = FastAPI(title="ERI NGUYEN API", version="0.1.0")
# No wildcard CORS: native iOS clients do not require browser CORS.
origins = [x.strip() for x in os.getenv("ERI_CORS_ORIGINS", "").split(",") if x.strip()]
if origins:
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET"], allow_headers=["*"])

@contextmanager
def connection():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    finally:
        db.close()

def sale_price(base_price_vnd: int) -> int:
    """30% markup, rounded up to the next VND; never trust client pricing."""
    return (base_price_vnd * 130 + 99) // 100

def init():
    with connection() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS ipa (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, version TEXT NOT NULL,
          bundle_id TEXT NOT NULL, download_url TEXT NOT NULL,
          sha256 TEXT NOT NULL, authorized INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS products (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
          price_vnd INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS orders (
          id TEXT PRIMARY KEY, kind TEXT NOT NULL, product_id INTEGER NOT NULL,
          product_name TEXT NOT NULL, amount_vnd INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS certificates (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, issuer TEXT NOT NULL,
          description TEXT NOT NULL, price_vnd INTEGER NOT NULL,
          active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS offers (
          id INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
          source_url TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
        );
        """)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(orders)")}
        for column, dtype in (("payos_order_code", "INTEGER"), ("payos_checkout_url", "TEXT")):
            if column not in columns:
                db.execute("ALTER TABLE orders ADD COLUMN " + column + " " + dtype)

init()

def require_admin(x_eri_admin_key: str | None = Header(default=None)):
    if len(ADMIN_KEY) < 32 or not secrets.compare_digest(x_eri_admin_key or "", ADMIN_KEY):
        raise HTTPException(403, "Admin access denied")

class IPA(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    version: str = Field(min_length=1, max_length=60)
    bundle_id: str = Field(min_length=3, max_length=255)
    download_url: HttpUrl
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    authorized: bool = False

class Product(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(max_length=2000)
    price_vnd: int = Field(ge=0)
    active: bool = True

class Offer(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(max_length=2000)
    source_url: HttpUrl
    active: bool = True

@app.get("/health")
def health():
    return {"status": "ok", "service": "ERI NGUYEN"}

@app.get("/api/v1/ipa")
def ipa_catalog(q: str = ""):
    with connection() as db:
        rows = db.execute(
            "SELECT id,name,version,bundle_id,download_url,sha256 FROM ipa WHERE authorized=1 AND (name LIKE ? OR bundle_id LIKE ?) ORDER BY id DESC LIMIT 100",
            (f"%{q[:100]}%", f"%{q[:100]}%"),
        ).fetchall()
    return {"items": [dict(x) for x in rows]}

@app.get("/api/v1/products")
def products():
    with connection() as db:
        rows = db.execute("SELECT id,name,description,price_vnd FROM products WHERE active=1 ORDER BY id DESC LIMIT 100").fetchall()
    return {"items": [{**dict(x), "price_vnd": sale_price(x["price_vnd"])} for x in rows]}

@app.get("/api/v1/offers")
def offers():
    with connection() as db:
        rows = db.execute("SELECT id,title,description,source_url FROM offers WHERE active=1 ORDER BY id DESC LIMIT 100").fetchall()
    return {"items": [dict(x) for x in rows]}

@app.get("/api/v1/capabilities")
def capabilities():
    return {"ipa_catalog": True, "ai_products": True, "offers": False,
            "ipa_signing": False, "payments": False, "third_party_catalog_sync": False,
            "automatic_fulfillment": False, "install_manifest_catalog": False}

@app.get("/api/v1/admin/ipa", dependencies=[Depends(require_admin)])
def admin_ipa_catalog():
    with connection() as db:
        rows = db.execute("SELECT id,name,version,bundle_id,download_url,sha256,authorized FROM ipa ORDER BY id DESC LIMIT 200").fetchall()
    return {"items": [dict(x) for x in rows]}

@app.get("/api/v1/mods")
def public_mod_catalog():
    with connection() as db:
        rows = db.execute("SELECT id,name,version,bundle_id,download_url,sha256 FROM ipa WHERE authorized=1 ORDER BY id DESC LIMIT 100").fetchall()
    return {"items": [dict(x) for x in rows]}

@app.post("/api/v1/admin/ipa", dependencies=[Depends(require_admin)], status_code=201)
def add_ipa(item: IPA):
    if not item.authorized:
        raise HTTPException(422, "Distribution authorization required")
    with connection() as db:
        cur = db.execute("INSERT INTO ipa(name,version,bundle_id,download_url,sha256,authorized) VALUES(?,?,?,?,?,1)",
            (item.name,item.version,item.bundle_id,str(item.download_url),item.sha256.lower()))
    return {"id": cur.lastrowid}

@app.post("/api/v1/admin/products", dependencies=[Depends(require_admin)], status_code=201)
def add_product(item: Product):
    with connection() as db:
        cur = db.execute("INSERT INTO products(name,description,price_vnd,active) VALUES(?,?,?,?)",
            (item.name,item.description,item.price_vnd,int(item.active)))
    return {"id": cur.lastrowid}

@app.post("/api/v1/admin/offers", dependencies=[Depends(require_admin)], status_code=201)
def add_offer(item: Offer):
    with connection() as db:
        cur = db.execute("INSERT INTO offers(title,description,source_url,active) VALUES(?,?,?,?)",
            (item.title,item.description,str(item.source_url),int(item.active)))
    return {"id": cur.lastrowid}

class CertificateProduct(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    issuer: str = Field(min_length=1, max_length=160)
    description: str = Field(max_length=2000)
    price_vnd: int = Field(ge=0)
    active: bool = True

@app.get("/api/v1/certificates")
def certificates():
    with connection() as db:
        rows = db.execute("SELECT id,name,issuer,description,price_vnd FROM certificates WHERE active=1 ORDER BY id DESC LIMIT 100").fetchall()
    return {"items": [{**dict(x), "price_vnd": sale_price(x["price_vnd"])} for x in rows]}

@app.post("/api/v1/admin/certificates", dependencies=[Depends(require_admin)], status_code=201)
def add_certificate(item: CertificateProduct):
    with connection() as db:
        cur = db.execute("INSERT INTO certificates(name,issuer,description,price_vnd,active) VALUES(?,?,?,?,?)",
            (item.name,item.issuer,item.description,item.price_vnd,int(item.active)))
    return {"id": cur.lastrowid}

PAYMENT_MODE = os.getenv("ERI_PAYMENT_MODE", "payos").strip()
PAYOS_CLIENT_ID = os.getenv("PAYOS_CLIENT_ID", "").strip()
PAYOS_API_KEY = os.getenv("PAYOS_API_KEY", "").strip()
PAYOS_CHECKSUM_KEY = os.getenv("PAYOS_CHECKSUM_KEY", "").strip()
PAYOS_RETURN_URL = os.getenv("PAYOS_RETURN_URL", "").strip()
PAYOS_CANCEL_URL = os.getenv("PAYOS_CANCEL_URL", "").strip()
BANK_BIN = os.getenv("ERI_BANK_BIN", "").strip()
BANK_ACCOUNT = os.getenv("ERI_BANK_ACCOUNT", "").strip()
BANK_NAME = os.getenv("ERI_BANK_NAME", "").strip()

def payos_configured():
    return all((PAYOS_CLIENT_ID, PAYOS_API_KEY, PAYOS_CHECKSUM_KEY,
                PAYOS_RETURN_URL.startswith("https://"), PAYOS_CANCEL_URL.startswith("https://")))

def payos_call(path, payload=None):
    if not payos_configured():
        raise HTTPException(503, "ERI payOS channel not configured")
    headers = {"x-client-id": PAYOS_CLIENT_ID, "x-api-key": PAYOS_API_KEY}
    if payload is not None: headers["Content-Type"] = "application/json"
    request = urllib.request.Request("https://api-merchant.payos.vn" + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers=headers, method="POST" if payload is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            result = json.load(response)
    except (urllib.error.URLError, ValueError) as exc:
        raise HTTPException(502, "Payment provider unavailable") from exc
    if result.get("code") != "00" or not isinstance(result.get("data"), dict):
        raise HTTPException(502, "Payment provider rejected request")
    return result["data"]

def payos_create(order_code, amount):
    params = {"amount": amount, "cancelUrl": PAYOS_CANCEL_URL,
              "description": "ERI" + str(order_code), "orderCode": order_code,
              "returnUrl": PAYOS_RETURN_URL}
    signed = "&".join(k + "=" + str(params[k]) for k in sorted(params))
    params["signature"] = hmac.new(PAYOS_CHECKSUM_KEY.encode(), signed.encode(), hashlib.sha256).hexdigest()
    return payos_call("/v2/payment-requests", params)

class NewOrder(BaseModel):
    kind: str = Field(pattern="^(ai|certificate)$")
    product_id: int = Field(gt=0)

def public_order(row):
    data = dict(row)
    data.pop("product_id", None)
    data.pop("created_at", None)
    data.pop("payos_order_code", None)
    checkout = data.pop("payos_checkout_url", None)
    if data["status"] == "pending" and PAYMENT_MODE == "payos":
        data["payment_method"] = "payos"
        if checkout: data["checkout_url"] = checkout
        return data
    data.pop("supplier_reference", None)
    if data["status"] == "pending" and PAYMENT_MODE == "static_momo":
        data["payment_method"] = "static_momo"
        data["payment_instructions"] = "Quét QR MoMo cố định trong ứng dụng. Nhập đúng số tiền và ghi mã đơn nếu ứng dụng ngân hàng hỗ trợ. Người bán xác nhận thủ công."
        data["transfer_note"] = "ERI " + data["id"]
    elif data["status"] == "pending" and BANK_BIN and BANK_ACCOUNT:
        from urllib.parse import quote
        note = "ERI " + data["id"]
        data["transfer_note"] = note
        data["bank_name"] = BANK_NAME
        data["bank_account"] = BANK_ACCOUNT
        data["qr_url"] = "https://img.vietqr.io/image/" + quote(BANK_BIN, safe="") + "-" + quote(BANK_ACCOUNT, safe="") + "-compact2.png?amount=" + str(data["amount_vnd"]) + "&addInfo=" + quote(note, safe="") + "&accountName=" + quote(BANK_NAME, safe="")
    else:
        data["payment_setup_required"] = not (BANK_BIN and BANK_ACCOUNT)
    return data

@app.post("/api/v1/orders", status_code=201)
def create_order(item: NewOrder):
    if PAYMENT_MODE == "payos" and not payos_configured():
        raise HTTPException(503, "ERI payOS payment channel not configured")
    if PAYMENT_MODE not in ("static_momo", "payos") and (not BANK_BIN or not BANK_ACCOUNT or not BANK_NAME):
        raise HTTPException(503, "Bank payment is not configured")
    table = "products" if item.kind == "ai" else "certificates"
    with connection() as db:
        product = db.execute("SELECT name,price_vnd FROM " + table + " WHERE id=? AND active=1", (item.product_id,)).fetchone()
        if product is None:
            raise HTTPException(404, "Product not available")
        if product["price_vnd"] <= 0:
            raise HTTPException(422, "Product price must be positive")
        oid = secrets.token_hex(6).upper()
        db.execute("INSERT INTO orders(id,kind,product_id,product_name,amount_vnd,status,created_at) VALUES(?,?,?,?,?,?,?)",
            (oid,item.kind,item.product_id,product["name"],sale_price(product["price_vnd"]),"pending",datetime.now(timezone.utc).isoformat()))
        row = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if PAYMENT_MODE == "payos":
        order_code = secrets.randbelow(1_900_000_000) + 100_000_000
        checkout = payos_create(order_code, row["amount_vnd"])
        checkout_url = checkout.get("checkoutUrl", "")
        if not isinstance(checkout_url, str) or not checkout_url.startswith("https://pay.payos.vn/"):
            raise HTTPException(502, "Invalid payOS checkout link")
        with connection() as db:
            db.execute("UPDATE orders SET payos_order_code=?,payos_checkout_url=? WHERE id=?", (order_code,checkout_url,oid))
            row = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    return public_order(row)

@app.get("/api/v1/orders/{order_id}")
def order_status(order_id: str):
    if not re.fullmatch(r"[A-F0-9]{12}", order_id):
        raise HTTPException(404, "Order not found")
    with connection() as db:
        row = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Order not found")
    return public_order(row)

# Only a trusted payment verifier can confirm a transfer. A QR scan is not proof of payment.
# This webhook is a private integration point, not a public customer confirmation endpoint.
# Connect a payment provider that independently verifies incoming MoMo transactions.
PAYMENT_WEBHOOK_SECRET = os.getenv("ERI_PAYMENT_WEBHOOK_SECRET", "")

@app.post("/api/v1/private/payment-confirmation")
async def trusted_payment_confirmation(request: Request, x_eri_signature: str | None = Header(default=None)):
    if len(PAYMENT_WEBHOOK_SECRET) < 32:
        raise HTTPException(503, "Payment verifier not configured")
    body = await request.body()
    if len(body) > 4096:
        raise HTTPException(413, "Payload too large")
    expected = hmac.new(PAYMENT_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(x_eri_signature or "", expected):
        raise HTTPException(401, "Invalid signature")
    try:
        data = json.loads(body)
        order_id = data["order_id"]
        amount = data["amount_vnd"]
        transaction_id = data["transaction_id"]
        if not isinstance(order_id, str) or not re.fullmatch(r"[A-F0-9]{12}", order_id):
            raise ValueError("Invalid order")
        if type(amount) is not int or amount <= 0:
            raise ValueError("Invalid amount")
        if not isinstance(transaction_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", transaction_id):
            raise ValueError("Invalid transaction")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise HTTPException(422, "Invalid confirmation")
    with connection() as db:
        db.execute("CREATE TABLE IF NOT EXISTS payment_receipts (transaction_id TEXT PRIMARY KEY, order_id TEXT NOT NULL, amount_vnd INTEGER NOT NULL)")
        row = db.execute("SELECT id,amount_vnd,status FROM orders WHERE id=?", (order_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Order not found")
        if row["amount_vnd"] != amount:
            raise HTTPException(409, "Payment amount mismatch")
        existing = db.execute("SELECT order_id,amount_vnd FROM payment_receipts WHERE transaction_id=?", (transaction_id,)).fetchone()
        if existing:
            if existing["order_id"] != order_id or existing["amount_vnd"] != amount:
                raise HTTPException(409, "Transaction already assigned")
            return {"status": row["status"]}
        if row["status"] != "pending":
            raise HTTPException(409, "Order not pending")
        db.execute("INSERT INTO payment_receipts(transaction_id,order_id,amount_vnd) VALUES(?,?,?)", (transaction_id,order_id,amount))
        db.execute("UPDATE orders SET status='paid' WHERE id=? AND status='pending'", (order_id,))
    # Supplier ordering is deliberately NOT triggered until an authorized supplier
    # integration and an idempotent fulfillment worker have been configured.
    return {"status": "paid"}

@app.post("/api/v1/webhooks/payos")
async def payos_webhook(request: Request):
    if not payos_configured():
        raise HTTPException(503, "payOS not configured")
    raw = await request.body()
    if len(raw) > 8192: raise HTTPException(413, "Webhook too large")
    try:
        data = json.loads(raw)["data"]
        code = data["orderCode"]
        if type(code) is not int or code <= 0: raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise HTTPException(422, "Invalid webhook")
    with connection() as db:
        row = db.execute("SELECT * FROM orders WHERE payos_order_code=?", (code,)).fetchone()
    if row is None:
        return {"success": True}
    verified = payos_call("/v2/payment-requests/" + str(code))
    if verified.get("status") == "PAID" and verified.get("amount") == row["amount_vnd"] and verified.get("amountPaid", 0) >= row["amount_vnd"]:
        with connection() as db:
            db.execute("UPDATE orders SET status='paid' WHERE id=? AND status='pending'", (row["id"],))
    return {"success": True}

class OrderStatusUpdate(BaseModel):
    status: str = Field(pattern="^(paid|cancelled|fulfilled)$")
    transaction_reference: str | None = Field(default=None, min_length=6, max_length=128)

@app.post("/api/v1/admin/orders/{order_id}/status", dependencies=[Depends(require_admin)])
def update_order_status(order_id: str, item: OrderStatusUpdate):
    with connection() as db:
        old = db.execute("SELECT status FROM orders WHERE id=?", (order_id,)).fetchone()
        if old is None:
            raise HTTPException(404, "Order not found")
        allowed = {"pending": {"paid", "cancelled"}, "paid": {"fulfilled"}, "cancelled": set(), "fulfilled": set()}
        if item.status not in allowed[old["status"]]:
            raise HTTPException(409, "Invalid order status transition")
        if item.status == "paid":
            if not item.transaction_reference or not re.fullmatch(r"[A-Za-z0-9_\\-]{6,128}", item.transaction_reference):
                raise HTTPException(422, "MoMo transaction reference required to confirm payment")
            db.execute("CREATE TABLE IF NOT EXISTS manual_payment_receipts (transaction_reference TEXT PRIMARY KEY, order_id TEXT NOT NULL)")
            try:
                db.execute("INSERT INTO manual_payment_receipts(transaction_reference,order_id) VALUES(?,?)", (item.transaction_reference,order_id))
            except sqlite3.IntegrityError:
                raise HTTPException(409, "MoMo transaction reference already used")
        db.execute("UPDATE orders SET status=? WHERE id=?", (item.status,order_id))
    return {"id": order_id, "status": item.status}

@app.get("/api/v1/admin/orders", dependencies=[Depends(require_admin)])
def admin_orders():
    with connection() as db:
        rows = db.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT 200").fetchall()
    return {"items": [dict(x) for x in rows]}

# Private signing API (requires a macOS worker and ERI_SIGNING_KEY).
from signing_api import router as signing_router
app.include_router(signing_router)


# Private macOS signing service. Deploy this API on a macOS host with Apple codesign.
# Never expose this route without a strong per-client token and HTTPS termination.
import asyncio
import tempfile
import shutil
import sys
from fastapi import File, Form, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

SIGN_TOKEN = os.getenv("ERI_SIGN_TOKEN", "")
SIGN_WORKER = Path(os.getenv("ERI_SIGN_WORKER", str(Path(__file__).resolve().parent.parent / "signing-worker" / "sign_worker.py")))
SIGN_MAX_IPA = int(os.getenv("ERI_SIGN_MAX_IPA_BYTES", str(2 * 1024**3)))
SIGN_MAX_CERT = 5 * 1024**2
_sign_lock = asyncio.Lock()

async def _save_limited(upload: UploadFile, target: Path, limit: int):
    size = 0
    with target.open("wb") as dest:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, "Uploaded file exceeds signing size limit")
            dest.write(chunk)

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
    if not SIGN_TOKEN or not secrets.compare_digest(x_eri_sign_token or "", SIGN_TOKEN):
        raise HTTPException(403, "Signing access denied")
    if sys.platform != "darwin" or not SIGN_WORKER.is_file():
        raise HTTPException(503, "ERI macOS signing worker is not available")
    if len(password) > 1024 or len(identifier) > 255 or len(name) > 160:
        raise HTTPException(422, "Invalid signing fields")
    # Bundle rewriting requires separate binary-safe implementation; never silently ignore it.
    if identifier.strip() or name.strip():
        raise HTTPException(422, "Bundle ID and app name changes are not yet supported")
    if not (ipa.filename or "").lower().endswith(".ipa") or not (p12.filename or "").lower().endswith(".p12") or not (mobileprovision.filename or "").lower().endswith(".mobileprovision"):
        raise HTTPException(422, "Expected .ipa, .p12 and .mobileprovision")
    if _sign_lock.locked():
        raise HTTPException(429, "Signing worker is busy; retry later")
    async with _sign_lock:
        tmp = Path(tempfile.mkdtemp(prefix="eri-api-sign-"))
        try:
            ipa_path, p12_path, profile_path = (tmp / "input.ipa", tmp / "cert.p12", tmp / "profile.mobileprovision")
            await _save_limited(ipa, ipa_path, SIGN_MAX_IPA)
            await _save_limited(p12, p12_path, SIGN_MAX_CERT)
            await _save_limited(mobileprovision, profile_path, SIGN_MAX_CERT)
            output = tmp / "signed.ipa"
            env = os.environ.copy()
            env["ERI_P12_PASSWORD"] = password
            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(SIGN_WORKER), str(ipa_path), str(p12_path),
                str(profile_path), str(output),
                env=env, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                raise HTTPException(504, "Signing timed out")
            finally:
                env.pop("ERI_P12_PASSWORD", None)
            if proc.returncode != 0 or not output.is_file():
                # Do not echo stderr: Apple tools can include sensitive file paths.
                raise HTTPException(422, "Signing failed; check certificate, profile and IPA compatibility")
            return FileResponse(
                str(output), media_type="application/octet-stream",
                filename="ERI-signed.ipa",
                background=BackgroundTask(shutil.rmtree, tmp, ignore_errors=True),
                headers={"Cache-Control": "no-store"})
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
