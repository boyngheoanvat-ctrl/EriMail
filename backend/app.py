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
import zipfile
import plistlib
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Header, Depends, Request, Response
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, HttpUrl

DB = Path(os.getenv("ERI_DB_PATH", "./eri.sqlite3"))
ADMIN_KEY = os.getenv("ERI_ADMIN_KEY", "")
app = FastAPI(title="ERI NGUYEN API", version="0.1.0")
# No wildcard CORS: native iOS clients do not require browser CORS.
origins = [x.strip() for x in os.getenv("ERI_CORS_ORIGINS", "").split(",") if x.strip()]
if origins:
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["Authorization", "Content-Type"], max_age=600)

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
          sha256 TEXT NOT NULL, authorized INTEGER NOT NULL DEFAULT 0,
          price_vnd INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS products (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
          price_vnd INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS customers (
          id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
          display_name TEXT NOT NULL, contact TEXT NOT NULL,
          salt TEXT NOT NULL, password_hash TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS customer_sessions (
          token_hash TEXT PRIMARY KEY, customer_id INTEGER NOT NULL,
          expires_at TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_customer ON customer_sessions(customer_id);
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
        CREATE TABLE IF NOT EXISTS private_product_uploads (
          id TEXT PRIMARY KEY, kind TEXT NOT NULL, product_id INTEGER NOT NULL,
          original_name TEXT NOT NULL, storage_name TEXT NOT NULL,
          size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS merchant_payment_qr (
          id INTEGER PRIMARY KEY CHECK(id=1), image BLOB NOT NULL,
          mime TEXT NOT NULL CHECK(mime IN ('image/png','image/jpeg')),
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS order_deliveries (
          order_id TEXT PRIMARY KEY, original_name TEXT NOT NULL,
          storage_name TEXT NOT NULL UNIQUE, size_bytes INTEGER NOT NULL,
          sha256 TEXT NOT NULL, created_at TEXT NOT NULL
        );
        """)
        ipa_columns = {row["name"] for row in db.execute("PRAGMA table_info(ipa)")}
        if "description" not in ipa_columns:
            db.execute("ALTER TABLE ipa ADD COLUMN description TEXT NOT NULL DEFAULT ''")
        if "price_vnd" not in ipa_columns:
            db.execute("ALTER TABLE ipa ADD COLUMN price_vnd INTEGER NOT NULL DEFAULT 0")
        columns = {row["name"] for row in db.execute("PRAGMA table_info(orders)")}
        for column, dtype in (("payos_order_code", "INTEGER"), ("payos_checkout_url", "TEXT"),
                              ("customer_id", "INTEGER")):
            if column not in columns:
                db.execute("ALTER TABLE orders ADD COLUMN " + column + " " + dtype)
        db.execute("CREATE INDEX IF NOT EXISTS idx_orders_customer_created ON orders(customer_id, created_at)")

init()

def require_admin(x_eri_admin_key: str | None = Header(default=None)):
    if len(ADMIN_KEY) < 32 or not secrets.compare_digest(x_eri_admin_key or "", ADMIN_KEY):
        raise HTTPException(403, "Admin access denied")


# Customers have individual credentials and scoped order access.
# Passwords are never stored in plaintext; session tokens are stored only as SHA-256 hashes.
CUSTOMER_PBKDF2_ITERATIONS = 390_000
CUSTOMER_SESSION_DAYS = 30
USERNAME_RULE = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")

class CustomerRegistration(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=10, max_length=128)
    display_name: str = Field(min_length=1, max_length=80)
    contact: str = Field(min_length=6, max_length=100)

class CustomerLogin(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=128)

def _customer_password_hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, CUSTOMER_PBKDF2_ITERATIONS
    ).hex()

def _customer_response(row):
    return {
        "id": row["id"], "username": row["username"],
        "display_name": row["display_name"], "contact": row["contact"]
    }

def _customer_session(db, row):
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    db.execute(
        "INSERT INTO customer_sessions(token_hash,customer_id,expires_at,created_at)"
        " VALUES(?,?,?,?)",
        (hashlib.sha256(token.encode()).hexdigest(), row["id"],
         (now + timedelta(days=CUSTOMER_SESSION_DAYS)).isoformat(), now.isoformat())
    )
    return {"token": token, "customer": _customer_response(row)}

def require_customer(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Vui lòng đăng nhập tài khoản ERI STORE")
    token = authorization[7:]
    if len(token) < 40 or len(token) > 128:
        raise HTTPException(401, "Phiên đăng nhập không hợp lệ")
    digest = hashlib.sha256(token.encode()).hexdigest()
    with connection() as db:
        row = db.execute(
            "SELECT c.id,c.username,c.display_name,c.contact,s.expires_at "
            "FROM customer_sessions s JOIN customers c ON c.id=s.customer_id "
            "WHERE s.token_hash=?", (digest,)
        ).fetchone()
    if row is None or datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
        raise HTTPException(401, "Phiên đăng nhập đã hết hạn")
    return dict(row)

@app.post("/api/v1/auth/register", status_code=201)
def customer_register(body: CustomerRegistration):
    username = body.username.strip().lower()
    display_name = body.display_name.strip()
    contact = body.contact.strip()
    if not USERNAME_RULE.fullmatch(username) or not (1 <= len(display_name) <= 80) or not (6 <= len(contact) <= 100):
        raise HTTPException(422, "Thông tin đăng ký không hợp lệ")
    salt = secrets.token_bytes(16)
    hashed = _customer_password_hash(body.password, salt)
    now = datetime.now(timezone.utc).isoformat()
    with connection() as db:
        try:
            cur = db.execute(
                "INSERT INTO customers(username,display_name,contact,salt,password_hash,created_at)"
                " VALUES(?,?,?,?,?,?)",
                (username, display_name, contact, salt.hex(), hashed, now)
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Tên đăng nhập đã được sử dụng")
        row = db.execute("SELECT * FROM customers WHERE id=?", (cur.lastrowid,)).fetchone()
        return _customer_session(db, row)

@app.post("/api/v1/auth/login")
def customer_login(body: CustomerLogin):
    with connection() as db:
        row = db.execute("SELECT * FROM customers WHERE username=?",
                         (body.username.strip().lower(),)).fetchone()
        if row is None:
            # Consume comparable CPU time so unknown usernames are not trivially distinguishable.
            _customer_password_hash(body.password, b"\0" * 16)
            raise HTTPException(401, "Sai tên đăng nhập hoặc mật khẩu")
        calculated = _customer_password_hash(body.password, bytes.fromhex(row["salt"]))
        if not hmac.compare_digest(calculated, row["password_hash"]):
            raise HTTPException(401, "Sai tên đăng nhập hoặc mật khẩu")
        return _customer_session(db, row)

@app.get("/api/v1/auth/me")
def customer_me(customer=Depends(require_customer)):
    return _customer_response(customer)

@app.post("/api/v1/auth/logout")
def customer_logout(authorization: str | None = Header(default=None),
                    customer=Depends(require_customer)):
    with connection() as db:
        db.execute("DELETE FROM customer_sessions WHERE token_hash=?",
                   (hashlib.sha256(authorization[7:].encode()).hexdigest(),))
    return {"logged_out": True}

@app.get("/api/v1/me/orders")
def customer_order_history(customer=Depends(require_customer)):
    with connection() as db:
        rows = db.execute(
            "SELECT o.*,d.original_name AS delivery_filename,"
            "d.size_bytes AS delivery_size_bytes,"
            "u.udid AS certificate_udid,u.source AS certificate_udid_source "
            "FROM orders o "
            "LEFT JOIN order_deliveries d ON d.order_id=o.id "
            "LEFT JOIN cert_order_udids u ON u.order_id=o.id "
            "WHERE o.customer_id=? ORDER BY o.created_at DESC LIMIT 200",
            (customer["id"],)).fetchall()
    return {"items": [public_order(x) for x in rows]}

class IPA(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    version: str = Field(min_length=1, max_length=60)
    bundle_id: str = Field(min_length=3, max_length=255)
    download_url: HttpUrl
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    authorized: bool = False
    price_vnd: int = Field(ge=0, le=1000000000)

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
            "SELECT id,name,version,bundle_id,description,price_vnd FROM ipa WHERE authorized=1 AND (name LIKE ? OR bundle_id LIKE ?) ORDER BY id DESC LIMIT 100",
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
        rows = db.execute("SELECT id,name,version,bundle_id,download_url,sha256,authorized,price_vnd FROM ipa ORDER BY id DESC LIMIT 200").fetchall()
    return {"items": [dict(x) for x in rows]}

@app.get("/api/v1/mods")
def public_mod_catalog():
    with connection() as db:
        rows = db.execute("SELECT id,name,version,bundle_id,description,price_vnd FROM ipa WHERE authorized=1 ORDER BY id DESC LIMIT 100").fetchall()
    items = []
    with connection() as db:
        for row in rows:
            item = dict(row)
            if item["price_vnd"] == 0 and db.execute(
                "SELECT 1 FROM private_product_uploads WHERE kind='ipa' AND product_id=?",
                (item["id"],)
            ).fetchone():
                item["download_path"] = f'/api/v1/mods/{item["id"]}/download'
            items.append(item)
    return {"items": items}

@app.get("/api/v1/mods/{product_id}/download")
def download_free_mod(product_id: int):
    # Public download only for authorized, zero-price IPA files stored locally.
    # Never expose paid files or allow a user-controlled filesystem path.
    with connection() as db:
        row = db.execute(
            "SELECT p.storage_name,p.original_name,p.sha256 FROM ipa i "
            "JOIN private_product_uploads p ON p.kind='ipa' AND p.product_id=i.id "
            "WHERE i.id=? AND i.authorized=1 AND i.price_vnd=0",
            (product_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(404, "Free IPA not available")
    storage_name = row["storage_name"]
    if (not re.fullmatch(r"[a-f0-9]{32}\\.ipa", storage_name)
            or not (UPLOAD_DIR / storage_name).is_file()):
        raise HTTPException(404, "Free IPA file not available")
    return FileResponse(
        UPLOAD_DIR / storage_name,
        filename=Path(row["original_name"]).name,
        media_type="application/octet-stream",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
    )

@app.post("/api/v1/admin/ipa", dependencies=[Depends(require_admin)], status_code=201)
def add_ipa(item: IPA):
    if not item.authorized:
        raise HTTPException(422, "Distribution authorization required")
    with connection() as db:
        cur = db.execute("INSERT INTO ipa(name,version,bundle_id,download_url,sha256,authorized,price_vnd) VALUES(?,?,?,?,?,1,?)",
            (item.name,item.version,item.bundle_id,str(item.download_url),item.sha256.lower(),item.price_vnd))
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
    kind: str = Field(pattern="^(ai|certificate|mod)$")
    product_id: int = Field(gt=0)

def public_order(row):
    data = dict(row)
    filename = data.pop("delivery_filename", None)
    size = data.pop("delivery_size_bytes", None)
    device_udid = data.pop("certificate_udid", None)
    device_source = data.pop("certificate_udid_source", None)
    if data.get("kind") == "certificate" and device_udid:
        data["device_udid"] = device_udid
        data["device_udid_source"] = device_source
    if filename and data.get("status") == "fulfilled":
        data["delivery"] = {
            "filename": filename, "size_bytes": size,
            "download_path": "/api/v1/me/orders/" + data["id"] + "/delivery"
        }
    data.pop("customer_id", None)
    data.pop("product_id", None)
    data.pop("payos_order_code", None)
    checkout = data.pop("payos_checkout_url", None)
    if data["status"] == "pending" and PAYMENT_MODE == "payos":
        data["payment_method"] = "payos"
        if checkout: data["checkout_url"] = checkout
        return data
    data.pop("supplier_reference", None)
    if data["status"] == "pending" and PAYMENT_MODE == "static_momo":
        data["payment_method"] = "static_momo"
        data["payment_instructions"] = "Quét QR MoMo, tự nhập đúng số tiền và nội dung chuyển khoản. Chỉ Admin xác nhận khi đã kiểm tra giao dịch thực tế."
        data["transfer_note"] = "ERI " + data["id"]
        with connection() as db:
            has_qr = db.execute("SELECT 1 FROM merchant_payment_qr WHERE id=1").fetchone() is not None
        if has_qr:
            data["qr_url"] = "/api/v1/payment/momo-qr"
        else:
            data["payment_setup_required"] = True
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
def create_order(item: NewOrder, customer=Depends(require_customer)):
    if PAYMENT_MODE == "payos" and not payos_configured():
        raise HTTPException(503, "ERI payOS payment channel not configured")
    if PAYMENT_MODE not in ("static_momo", "payos") and (not BANK_BIN or not BANK_ACCOUNT or not BANK_NAME):
        raise HTTPException(503, "Bank payment is not configured")
    table = {"ai": "products", "certificate": "certificates", "mod": "ipa"}[item.kind]
    with connection() as db:
        product = db.execute("SELECT name,price_vnd FROM " + table + " WHERE id=? AND " + ("authorized=1" if item.kind == "mod" else "active=1"), (item.product_id,)).fetchone()
        if product is None:
            raise HTTPException(404, "Product not available")
        if product["price_vnd"] <= 0:
            raise HTTPException(422, "Product price must be positive")
        oid = secrets.token_hex(6).upper()
        db.execute("INSERT INTO orders(id,kind,product_id,product_name,amount_vnd,status,created_at,customer_id) VALUES(?,?,?,?,?,?,?,?)",
            (oid,item.kind,item.product_id,product["name"],product["price_vnd"] if item.kind == "mod" else sale_price(product["price_vnd"]),"pending",datetime.now(timezone.utc).isoformat(),customer["id"]))
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
def order_status(order_id: str, customer=Depends(require_customer)):
    if not re.fullmatch(r"[A-F0-9]{12}", order_id):
        raise HTTPException(404, "Order not found")
    with connection() as db:
        row = db.execute(
            "SELECT o.*,d.original_name AS delivery_filename,"
            "d.size_bytes AS delivery_size_bytes,"
            "u.udid AS certificate_udid,u.source AS certificate_udid_source "
            "FROM orders o "
            "LEFT JOIN order_deliveries d ON d.order_id=o.id "
            "LEFT JOIN cert_order_udids u ON u.order_id=o.id "
            "WHERE o.id=? AND o.customer_id=?",
            (order_id, customer["id"])).fetchone()
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
        rows = db.execute(
            "SELECT o.*, c.username AS buyer_username, c.display_name AS buyer_name, "
            "c.contact AS buyer_contact, d.original_name AS delivery_filename,"
            "u.udid AS certificate_udid,u.source AS certificate_udid_source "
            "FROM orders o LEFT JOIN customers c ON c.id=o.customer_id "
            "LEFT JOIN order_deliveries d ON d.order_id=o.id "
            "LEFT JOIN cert_order_udids u ON u.order_id=o.id "
            "ORDER BY o.created_at DESC LIMIT 300"
        ).fetchall()
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


# Files are private and only uploadable by an authenticated admin.
# Use persistent, access-controlled storage for ERI_UPLOAD_DIR and ERI_DB_PATH in production.
UPLOAD_LIMITS = {"ipa": 512 * 1024**2, "certificate": 15 * 1024**2, "ai": 20 * 1024**2}
UPLOAD_SUFFIXES = {
    "ipa": {".ipa"},
    "certificate": {".p12", ".mobileprovision", ".zip"},
    "ai": {".txt", ".json", ".csv", ".zip"},
}
UPLOAD_DIR = Path(os.getenv("ERI_UPLOAD_DIR", str(DB.parent / "eri-private-uploads")))

@app.post("/api/v1/admin/uploads/{kind}", dependencies=[Depends(require_admin)], status_code=201)
async def upload_private_product(
    kind: str,
    upload: UploadFile = File(...),
    name: str = Form(...),
    price_vnd: int = Form(...),
    version: str = Form(""),
    bundle_id: str = Form(""),
    issuer: str = Form(""),
    description: str = Form(""),
    authorized: bool = Form(False),
):
    if kind not in UPLOAD_LIMITS:
        raise HTTPException(404, "Unknown product category")
    name, version, bundle_id, issuer, description = (
        field.strip() for field in (name, version, bundle_id, issuer, description)
    )
    if not 1 <= len(name) <= 160 or not 0 <= price_vnd <= 1_000_000_000 or (kind != "ipa" and price_vnd == 0):
        raise HTTPException(422, "Invalid name or price")
    if len(description) > 2000:
        raise HTTPException(422, "Description too long")
    if kind == "ipa" and (
        not authorized or not 1 <= len(version) <= 60 or
        not 3 <= len(bundle_id) <= 255
    ):
        raise HTTPException(422, "Valid IPA version, bundle ID and distribution rights required")
    if kind == "certificate" and not 1 <= len(issuer) <= 160:
        raise HTTPException(422, "Certificate issuer required")

    original_name = Path(upload.filename or "").name[:180]
    suffix = Path(original_name).suffix.lower()
    if suffix not in UPLOAD_SUFFIXES[kind]:
        raise HTTPException(422, "Unsupported file format")
    UPLOAD_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    storage_id = secrets.token_hex(16)
    storage_name = storage_id + suffix
    final_path = UPLOAD_DIR / storage_name
    temporary_path = UPLOAD_DIR / ("." + storage_name + ".partial")
    digest = hashlib.sha256()
    size = 0
    try:
        with temporary_path.open("xb") as target:
            os.chmod(temporary_path, 0o600)
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > UPLOAD_LIMITS[kind]:
                    raise HTTPException(413, "File exceeds category size limit")
                digest.update(chunk)
                target.write(chunk)
        if size == 0:
            raise HTTPException(422, "Empty file")
        if suffix in {".ipa", ".zip"}:
            try:
                with zipfile.ZipFile(temporary_path) as archive:
                    if kind == "ipa" and not any(
                        re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", member.filename)
                        for member in archive.infolist()
                    ):
                        raise HTTPException(422, "Not a valid IPA payload")
            except zipfile.BadZipFile:
                raise HTTPException(422, "Invalid ZIP or IPA file")
        os.replace(temporary_path, final_path)
        with connection() as db:
            if kind == "ipa":
                row = db.execute(
                    "INSERT INTO ipa(name,version,bundle_id,download_url,sha256,authorized,price_vnd) "
                    "VALUES(?,?,?,?,?,1,?)",
                    (name, version, bundle_id, "private:" + storage_id, digest.hexdigest(), price_vnd),
                )
            elif kind == "certificate":
                row = db.execute(
                    "INSERT INTO certificates(name,issuer,description,price_vnd,active) VALUES(?,?,?,?,1)",
                    (name, issuer, description, price_vnd),
                )
            else:
                row = db.execute(
                    "INSERT INTO products(name,description,price_vnd,active) VALUES(?,?,?,1)",
                    (name, description, price_vnd),
                )
            product_id = row.lastrowid
            db.execute(
                "INSERT INTO private_product_uploads(id,kind,product_id,original_name,"
                "storage_name,size_bytes,sha256,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (storage_id, kind, product_id, original_name, storage_name, size,
                 digest.hexdigest(), datetime.now(timezone.utc).isoformat()),
            )
        return {"id": product_id, "kind": kind, "name": name, "uploaded": True}
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()


# Resumable, authenticated chunk uploads; avoid a 200+ MB single HTTP request.
# This protocol requires the same persistent filesystem across requests.
ERI_CHUNK_BYTES = 4 * 1024 * 1024
_eri_upload_lock = asyncio.Lock()

class EriChunkStart(BaseModel):
    kind: str = Field(pattern="^(ipa|certificate|ai)$")
    filename: str = Field(min_length=1, max_length=180)
    size_bytes: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    price_vnd: int = Field(ge=0, le=1000000000)
    issuer: str = Field(default="", max_length=160)
    authorized: bool = False
    fast: bool = False

def _eri_paths(upload_id: str):
    if not re.fullmatch(r"[a-f0-9]{32}", upload_id):
        raise HTTPException(404, "Upload not found")
    return (UPLOAD_DIR / ("." + upload_id + ".session.json"),
            UPLOAD_DIR / ("." + upload_id + ".part"))

def _eri_read_session(upload_id: str):
    metadata_file, data_file = _eri_paths(upload_id)
    try:
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        if metadata.get("upload_id") != upload_id:
            raise ValueError("Session mismatch")
    except (OSError, ValueError, TypeError):
        raise HTTPException(404, "Upload session not found; server storage may have restarted")
    actual = data_file.stat().st_size if data_file.exists() else -1
    if metadata.get("fast"):
        # Fast mode pre-sizes a sparse file; completed ranges are tracked
        # independently so a partial chunk is never considered acknowledged.
        if actual != metadata["size_bytes"]:
            raise HTTPException(409, "Fast upload data missing or inconsistent")
        return metadata
    if actual < 0 or actual > metadata["size_bytes"]:
        raise HTTPException(409, "Upload data missing or inconsistent")
    if actual != metadata["offset"]:
        metadata["offset"] = actual
        _eri_save_session(upload_id, metadata)
    return metadata

def _eri_save_session(upload_id: str, metadata: dict):
    metadata_file, _ = _eri_paths(upload_id)
    staged = metadata_file.with_suffix(".writing")
    staged.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    os.chmod(staged, 0o600)
    os.replace(staged, metadata_file)

def _eri_ipa_properties(file: Path):
    try:
        with zipfile.ZipFile(file) as package:
            candidates = [m for m in package.infolist()
                          if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", m.filename)]
            if not candidates or candidates[0].file_size > 1024 * 1024:
                raise HTTPException(422, "IPA must contain a valid, small Info.plist")
            payload = plistlib.loads(package.read(candidates[0]))
            bundle_id = str(payload.get("CFBundleIdentifier", "")).strip()
            version = str(payload.get("CFBundleShortVersionString")
                          or payload.get("CFBundleVersion") or "").strip()
            if not (3 <= len(bundle_id) <= 255) or not (1 <= len(version) <= 60):
                raise HTTPException(422, "IPA Info.plist missing bundle identifier or version")
            return version, bundle_id
    except (zipfile.BadZipFile, ValueError, TypeError, plistlib.InvalidFileException):
        raise HTTPException(422, "Invalid IPA archive or Info.plist")

@app.post("/api/v1/admin/upload-sessions", dependencies=[Depends(require_admin)], status_code=201)
async def eri_begin_chunk_upload(item: EriChunkStart):
    filename = Path(item.filename).name
    extension = Path(filename).suffix.lower()
    if extension not in UPLOAD_SUFFIXES[item.kind]:
        raise HTTPException(422, "Unsupported file extension")
    if item.size_bytes > UPLOAD_LIMITS[item.kind]:
        raise HTTPException(413, "File exceeds maximum size")
    if item.kind == "ipa" and not item.authorized:
        raise HTTPException(422, "Distribution authorization required")
    if item.kind != "ipa" and item.price_vnd == 0:
        raise HTTPException(422, "Only Mod Game supports free uploads")
    if item.kind == "certificate" and not item.issuer.strip():
        raise HTTPException(422, "Certificate issuer required")
    UPLOAD_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    async with _eri_upload_lock:
        upload_id = secrets.token_hex(16)
        metadata_file, data_file = _eri_paths(upload_id)
        metadata = item.model_dump()
        metadata.update(upload_id=upload_id, filename=filename, extension=extension, offset=0)
        with data_file.open("xb") as dest:
            if item.fast:
                # Sparse allocation; avoids allocating a second full copy of large IPAs.
                dest.truncate(item.size_bytes)
        os.chmod(data_file, 0o600)
        if item.fast:
            (UPLOAD_DIR / ("." + upload_id + ".parts")).mkdir(mode=0o700)
        _eri_save_session(upload_id, metadata)
    return {"upload_id": upload_id, "offset": 0, "fast": item.fast,
            "size_bytes": item.size_bytes, "chunk_bytes": ERI_CHUNK_BYTES}

@app.get("/api/v1/admin/upload-sessions/{upload_id}", dependencies=[Depends(require_admin)])
async def eri_chunk_upload_status(upload_id: str):
    async with _eri_upload_lock:
        meta = _eri_read_session(upload_id)
        return {"offset": meta["offset"], "size_bytes": meta["size_bytes"],
                "fast": bool(meta.get("fast"))}


def _eri_fast_parts_dir(upload_id: str) -> Path:
    # _eri_paths validates the upload_id before constructing this path.
    _eri_paths(upload_id)
    return UPLOAD_DIR / ("." + upload_id + ".parts")


def _eri_fast_part_count(meta: dict) -> int:
    return (meta["size_bytes"] + ERI_CHUNK_BYTES - 1) // ERI_CHUNK_BYTES


def _eri_fast_part_marker(upload_id: str, index: int) -> Path:
    return _eri_fast_parts_dir(upload_id) / (f"{index:06d}.sha256")


@app.get("/api/v1/admin/upload-sessions/{upload_id}/fast-parts",
         dependencies=[Depends(require_admin)])
async def eri_fast_upload_status(upload_id: str):
    async with _eri_upload_lock:
        meta = _eri_read_session(upload_id)
        if not meta.get("fast"):
            raise HTTPException(409, "Session is not in fast upload mode")
        count = _eri_fast_part_count(meta)
        received = [i for i in range(count)
                    if _eri_fast_part_marker(upload_id, i).is_file()]
    return {"received": received, "chunk_bytes": ERI_CHUNK_BYTES,
            "size_bytes": meta["size_bytes"], "total_parts": count}


@app.post("/api/v1/admin/upload-sessions/{upload_id}/fast-parts/{index}",
          dependencies=[Depends(require_admin)])
async def eri_send_fast_part(upload_id: str, index: int, request: Request,
                             x_eri_chunk_sha256: str = Header(...)):
    # Authorization is enforced before reading the body. Each range is
    # independently hash-checked, then atomically marked as acknowledged.
    if not re.fullmatch(r"[a-f0-9]{64}", x_eri_chunk_sha256):
        raise HTTPException(422, "Invalid chunk checksum")
    async with _eri_upload_lock:
        meta = _eri_read_session(upload_id)
        if not meta.get("fast"):
            raise HTTPException(409, "Session is not in fast upload mode")
        count = _eri_fast_part_count(meta)
        if not 0 <= index < count:
            raise HTTPException(422, "Invalid chunk index")
        expected = min(ERI_CHUNK_BYTES, meta["size_bytes"] - index * ERI_CHUNK_BYTES)
        marker = _eri_fast_part_marker(upload_id, index)
        if marker.is_file():
            if marker.read_text() != x_eri_chunk_sha256:
                raise HTTPException(409, "Chunk checksum differs from acknowledged part")
            return {"index": index, "received_bytes": expected, "duplicate": True}

    chunk = bytearray()
    async for piece in request.stream():
        chunk.extend(piece)
        if len(chunk) > expected:
            raise HTTPException(413, "Chunk exceeds expected size")
    if len(chunk) != expected:
        raise HTTPException(422, "Incomplete chunk")
    if hashlib.sha256(chunk).hexdigest() != x_eri_chunk_sha256:
        raise HTTPException(422, "Chunk checksum mismatch")

    async with _eri_upload_lock:
        # Revalidate after the network transfer (another call can have completed).
        meta = _eri_read_session(upload_id)
        if not meta.get("fast"):
            raise HTTPException(409, "Session is not in fast upload mode")
        marker = _eri_fast_part_marker(upload_id, index)
        if marker.is_file():
            if marker.read_text() != x_eri_chunk_sha256:
                raise HTTPException(409, "Chunk checksum differs from acknowledged part")
            return {"index": index, "received_bytes": expected, "duplicate": True}
        _, data_file = _eri_paths(upload_id)
        with data_file.open("r+b") as dest:
            dest.seek(index * ERI_CHUNK_BYTES)
            dest.write(chunk)
            dest.flush()
        staged = marker.with_name(marker.name + "." + secrets.token_hex(8) + ".writing")
        staged.write_text(x_eri_chunk_sha256)
        os.chmod(staged, 0o600)
        os.replace(staged, marker)
    return {"index": index, "received_bytes": expected, "duplicate": False}


@app.put("/api/v1/admin/upload-sessions/{upload_id}", dependencies=[Depends(require_admin)])
async def eri_chunk_upload(upload_id: str, request: Request,
                           x_eri_upload_offset: int = Header(...)):
    async with _eri_upload_lock:
        meta = _eri_read_session(upload_id)
        if meta.get("fast"):
            raise HTTPException(409, "Use fast-parts endpoint for this session")
        if x_eri_upload_offset != meta["offset"]:
            raise HTTPException(409, detail={"offset": meta["offset"]})
        written = 0
        _, data_file = _eri_paths(upload_id)
        with data_file.open("ab") as dest:
            async for piece in request.stream():
                written += len(piece)
                if written > ERI_CHUNK_BYTES or meta["offset"] + written > meta["size_bytes"]:
                    dest.truncate(meta["offset"])
                    raise HTTPException(413, "Chunk too large")
                dest.write(piece)
            dest.flush()
        if written == 0:
            raise HTTPException(422, "Empty chunk")
        meta["offset"] += written
        _eri_save_session(upload_id, meta)
        return {"offset": meta["offset"]}

@app.post("/api/v1/admin/upload-sessions/{upload_id}/complete",
          dependencies=[Depends(require_admin)], status_code=201)
async def eri_finish_chunk_upload(upload_id: str):
    async with _eri_upload_lock:
        meta = _eri_read_session(upload_id)
        if meta.get("fast"):
            # Do not finalize until every validated byte range is acknowledged.
            if any(not _eri_fast_part_marker(upload_id, i).is_file()
                   for i in range(_eri_fast_part_count(meta))):
                raise HTTPException(409, "Not all fast upload parts have arrived")
            meta["offset"] = meta["size_bytes"]
        if meta["offset"] != meta["size_bytes"]:
            raise HTTPException(409, "File not fully transferred")
        metadata_file, part = _eri_paths(upload_id)
        digest = hashlib.sha256()
        with part.open("rb") as src:
            while data := src.read(1024 * 1024):
                digest.update(data)
        if meta["extension"] == ".ipa":
            version, bundle_id = _eri_ipa_properties(part)
        elif meta["extension"] == ".zip":
            try:
                with zipfile.ZipFile(part) as archive:
                    _ = archive.infolist()
            except zipfile.BadZipFile:
                raise HTTPException(422, "Invalid ZIP file")
            version, bundle_id = "", ""
        else:
            version, bundle_id = "", ""
        final = UPLOAD_DIR / (upload_id + meta["extension"])
        os.replace(part, final)
        try:
            with connection() as db:
                if meta["kind"] == "ipa":
                    row = db.execute(
                        "INSERT INTO ipa(name,version,bundle_id,download_url,sha256,"
                        "authorized,price_vnd,description) VALUES(?,?,?,?,?,1,?,?)",
                        (meta["name"], version, bundle_id, "private:" + upload_id,
                         digest.hexdigest(), meta["price_vnd"], meta["description"]))
                elif meta["kind"] == "certificate":
                    row = db.execute(
                        "INSERT INTO certificates(name,issuer,description,price_vnd,active)"
                        " VALUES(?,?,?,?,1)",
                        (meta["name"], meta["issuer"], meta["description"], meta["price_vnd"]))
                else:
                    row = db.execute(
                        "INSERT INTO products(name,description,price_vnd,active) VALUES(?,?,?,1)",
                        (meta["name"], meta["description"], meta["price_vnd"]))
                db.execute(
                    "INSERT INTO private_product_uploads(id,kind,product_id,original_name,"
                    "storage_name,size_bytes,sha256,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (upload_id, meta["kind"], row.lastrowid, meta["filename"],
                     final.name, meta["size_bytes"], digest.hexdigest(),
                     datetime.now(timezone.utc).isoformat()))
                product_id = row.lastrowid
        except Exception:
            os.replace(final, part)
            raise
        metadata_file.unlink(missing_ok=True)
        if meta.get("fast"):
            # Cleanup is best effort after the DB transaction succeeds.
            directory = _eri_fast_parts_dir(upload_id)
            try:
                for marker in directory.iterdir():
                    marker.unlink()
                directory.rmdir()
            except OSError:
                pass
    return {"id": product_id, "name": meta["name"], "uploaded": True}


# Merchant-configured static MoMo QR. Never put admin tokens or buyer secrets here.
# This image is the account's own QR, NOT a MoMo OAuth/API connection.
MOMO_QR_LIMIT = 2 * 1024 * 1024

def _validate_momo_qr(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        if 64 <= width <= 4096 and 64 <= height <= 4096:
            return "image/png"
    if data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"):
        return "image/jpeg"
    raise HTTPException(422, "Chỉ chấp nhận ảnh QR PNG/JPG hợp lệ")

@app.get("/api/v1/payment/momo-qr")
def public_momo_qr():
    with connection() as db:
        row = db.execute("SELECT image,mime FROM merchant_payment_qr WHERE id=1").fetchone()
    if row is None:
        raise HTTPException(404, "Chưa liên kết QR MoMo")
    return Response(content=row["image"], media_type=row["mime"],
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

@app.get("/api/v1/admin/payment/momo", dependencies=[Depends(require_admin)])
def admin_momo_qr_status():
    with connection() as db:
        row = db.execute("SELECT updated_at FROM merchant_payment_qr WHERE id=1").fetchone()
    return {"linked": row is not None, "updated_at": row["updated_at"] if row else None}

@app.post("/api/v1/admin/payment/momo", dependencies=[Depends(require_admin)])
async def admin_link_momo_qr(qr: UploadFile = File(...)):
    length = 0
    chunks = []
    try:
        while chunk := await qr.read(256 * 1024):
            length += len(chunk)
            if length > MOMO_QR_LIMIT:
                raise HTTPException(413, "Ảnh QR quá 2MB")
            chunks.append(chunk)
    finally:
        await qr.close()
    data = b"".join(chunks)
    mime = _validate_momo_qr(data)
    now = datetime.now(timezone.utc).isoformat()
    with connection() as db:
        db.execute(
            "INSERT INTO merchant_payment_qr(id,image,mime,updated_at) VALUES(1,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET image=excluded.image,mime=excluded.mime,"
            "updated_at=excluded.updated_at", (data,mime,now))
    return {"linked": True, "updated_at": now}

@app.delete("/api/v1/admin/payment/momo", dependencies=[Depends(require_admin)])
def admin_unlink_momo_qr():
    with connection() as db:
        db.execute("DELETE FROM merchant_payment_qr WHERE id=1")
    return {"linked": False}

@app.get("/api/v1/admin/catalog/{kind}", dependencies=[Depends(require_admin)])
def admin_catalog_products(kind: str):
    lookup = {"ipa": "ipa", "certificate": "certificates", "ai": "products"}
    if kind not in lookup:
        raise HTTPException(404, "Danh mục không tồn tại")
    with connection() as db:
        rows = db.execute("SELECT id,name,description,price_vnd FROM " + lookup[kind] +
                          " ORDER BY id DESC LIMIT 300").fetchall()
    return {"items": [dict(row) for row in rows]}

@app.delete("/api/v1/admin/catalog/{kind}/{product_id}",
            dependencies=[Depends(require_admin)])
def admin_delete_catalog_product(kind: str, product_id: int):
    lookup = {"ipa": "ipa", "certificate": "certificates", "ai": "products"}
    if kind not in lookup or product_id <= 0:
        raise HTTPException(404, "Sản phẩm không tồn tại")
    storage_names = []
    with connection() as db:
        row = db.execute("SELECT id FROM " + lookup[kind] + " WHERE id=?", (product_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Sản phẩm không tồn tại")
        matches = db.execute(
            "SELECT storage_name FROM private_product_uploads WHERE kind=? AND product_id=?",
            (kind, product_id)).fetchall()
        storage_names = [x["storage_name"] for x in matches]
        db.execute("DELETE FROM private_product_uploads WHERE kind=? AND product_id=?",
                   (kind, product_id))
        db.execute("DELETE FROM " + lookup[kind] + " WHERE id=?", (product_id,))
        # Keep existing order records and payment history; they store a name/price snapshot.
    # Remove only private files referenced by metadata. Never construct paths from user input.
    for name in storage_names:
        if Path(name).name == name and not name.startswith("."):
            (UPLOAD_DIR / name).unlink(missing_ok=True)
    return {"deleted": True, "id": product_id, "kind": kind}


# Authenticated delivery files, visible only to the buyer whose order is fulfilled.
# WARNING: /tmp is temporary on Render Free; use durable storage in production.
DELIVERY_MAX_BYTES = 300 * 1024 * 1024
DELIVERY_SUFFIXES = {".ipa",".zip",".pdf",".txt",".mobileprovision",".p12",".cer",
                     ".png",".jpg",".jpeg",".json",".plist",".md",".csv"}

class DeliveryBegin(BaseModel):
    filename: str = Field(min_length=1, max_length=180)
    size_bytes: int = Field(gt=0, le=DELIVERY_MAX_BYTES)

def delivery_paths(upload_id: str):
    if not re.fullmatch(r"[a-f0-9]{32}", upload_id):
        raise HTTPException(404, "Lượt upload không tồn tại")
    return UPLOAD_DIR / ("." + upload_id + ".delivery.json"), UPLOAD_DIR / ("." + upload_id + ".delivery.part")

def delivery_metadata(order_id: str, upload_id: str):
    metadata_path, part_path = delivery_paths(upload_id)
    try:
        metadata = json.loads(metadata_path.read_text("utf-8"))
    except (OSError, ValueError):
        raise HTTPException(404, "Không tìm thấy lượt giao hàng")
    if metadata.get("order_id") != order_id or not part_path.is_file():
        raise HTTPException(404, "Lượt giao không thuộc đơn này")
    if part_path.stat().st_size != metadata["offset"]:
        raise HTTPException(409, "Trạng thái tệp chưa đồng bộ")
    return metadata, metadata_path, part_path

def save_delivery_metadata(meta, path):
    tmp = path.with_suffix(".writing")
    tmp.write_text(json.dumps(meta,ensure_ascii=False),encoding="utf-8")
    os.chmod(tmp,0o600)
    os.replace(tmp,path)

@app.post("/api/v1/admin/orders/{order_id}/delivery-sessions",
          dependencies=[Depends(require_admin)], status_code=201)
async def delivery_begin(order_id: str, item: DeliveryBegin):
    filename = item.filename.replace("\\","/").split("/")[-1].strip()
    suffix = Path(filename).suffix.lower()
    if not filename or filename in (".","..") or any(ord(c)<32 or ord(c)==127 for c in filename) or suffix not in DELIVERY_SUFFIXES:
        raise HTTPException(422,"Tên hoặc định dạng tệp chưa được hỗ trợ")
    if not re.fullmatch(r"[A-F0-9]{12}", order_id):
        raise HTTPException(404,"Không tìm thấy đơn")
    with connection() as db:
        order=db.execute("SELECT customer_id,status FROM orders WHERE id=?",(order_id,)).fetchone()
        prior=db.execute("SELECT 1 FROM order_deliveries WHERE order_id=?",(order_id,)).fetchone()
    if order is None or order["customer_id"] is None:
        raise HTTPException(404,"Đơn chưa gắn với tài khoản khách")
    if order["status"]!="paid" or prior:
        raise HTTPException(409,"Chỉ giao file cho đơn đã thanh toán và chưa được giao")
    UPLOAD_DIR.mkdir(parents=True,exist_ok=True,mode=0o700)
    async with _eri_upload_lock:
        upload_id=secrets.token_hex(16)
        path,part=delivery_paths(upload_id)
        with part.open("xb"):
            pass
        os.chmod(part,0o600)
        try:
            save_delivery_metadata({"order_id":order_id,"filename":filename,"suffix":suffix,
                                    "offset":0,"size_bytes":item.size_bytes},path)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
    return {"upload_id":upload_id,"offset":0,"size_bytes":item.size_bytes,"chunk_bytes":ERI_CHUNK_BYTES}

@app.get("/api/v1/admin/orders/{order_id}/delivery-sessions/{upload_id}",
         dependencies=[Depends(require_admin)])
async def delivery_progress(order_id: str, upload_id: str):
    async with _eri_upload_lock:
        meta,_,_=delivery_metadata(order_id,upload_id)
        return {"offset":meta["offset"],"size_bytes":meta["size_bytes"]}

@app.put("/api/v1/admin/orders/{order_id}/delivery-sessions/{upload_id}",
         dependencies=[Depends(require_admin)])
async def delivery_chunk(order_id: str, upload_id: str, request: Request,
                         x_eri_upload_offset: int=Header(...)):
    async with _eri_upload_lock:
        meta,path,part=delivery_metadata(order_id,upload_id)
        if x_eri_upload_offset!=meta["offset"]:
            raise HTTPException(409,detail={"offset":meta["offset"]})
        length=0
        with part.open("ab") as out:
            async for piece in request.stream():
                length+=len(piece)
                if length>ERI_CHUNK_BYTES or meta["offset"]+length>meta["size_bytes"]:
                    out.truncate(meta["offset"])
                    raise HTTPException(413,"Chunk quá lớn")
                out.write(piece)
            out.flush()
        if not length:
            raise HTTPException(422,"Tệp rỗng")
        meta["offset"]+=length
        save_delivery_metadata(meta,path)
        return {"offset":meta["offset"]}

@app.post("/api/v1/admin/orders/{order_id}/delivery-sessions/{upload_id}/complete",
          dependencies=[Depends(require_admin)],status_code=201)
async def delivery_complete(order_id: str,upload_id: str):
    async with _eri_upload_lock:
        meta,path,part=delivery_metadata(order_id,upload_id)
        if meta["offset"]!=meta["size_bytes"]:
            raise HTTPException(409,"Chưa nhận đủ tệp")
        digest=hashlib.sha256()
        with part.open("rb") as src:
            while block:=src.read(1048576):
                digest.update(block)
        final=UPLOAD_DIR / (upload_id+meta["suffix"])
        os.replace(part,final)
        try:
            with connection() as db:
                order=db.execute("SELECT status,customer_id FROM orders WHERE id=?",(order_id,)).fetchone()
                if order is None or order["status"]!="paid" or order["customer_id"] is None:
                    raise HTTPException(409,"Đơn chưa sẵn sàng để giao file")
                if db.execute("SELECT 1 FROM order_deliveries WHERE order_id=?",(order_id,)).fetchone():
                    raise HTTPException(409,"Đơn đã được giao file")
                db.execute(
                    "INSERT INTO order_deliveries(order_id,original_name,storage_name,size_bytes,sha256,created_at)"
                    " VALUES(?,?,?,?,?,?)",
                    (order_id,meta["filename"],final.name,meta["size_bytes"],digest.hexdigest(),
                     datetime.now(timezone.utc).isoformat()))
                cur=db.execute("UPDATE orders SET status='fulfilled' WHERE id=? AND status='paid'",(order_id,))
                if cur.rowcount!=1:
                    raise HTTPException(409,"Trạng thái đơn thay đổi")
        except BaseException:
            os.replace(final,part)
            raise
        path.unlink(missing_ok=True)
    return {"id":order_id,"status":"fulfilled","delivery":{
        "filename":meta["filename"],"size_bytes":meta["size_bytes"],
        "download_path":"/api/v1/me/orders/"+order_id+"/delivery"}}

@app.get("/api/v1/me/orders/{order_id}/delivery")
def delivery_download(order_id: str, customer=Depends(require_customer)):
    if not re.fullmatch(r"[A-F0-9]{12}",order_id):
        raise HTTPException(404,"Không tìm thấy tệp")
    with connection() as db:
        file=db.execute(
            "SELECT d.original_name,d.storage_name FROM order_deliveries d "
            "JOIN orders o ON o.id=d.order_id "
            "WHERE o.id=? AND o.customer_id=? AND o.status='fulfilled'",
            (order_id,customer["id"])).fetchone()
    if file is None:
        raise HTTPException(404,"Đơn này chưa có tệp được giao")
    path=UPLOAD_DIR / file["storage_name"]
    if path.name!=file["storage_name"] or not path.is_file():
        raise HTTPException(410,"Tệp đã mất khỏi ổ tạm. Liên hệ Admin để nhận lại.")
    return FileResponse(path,media_type="application/octet-stream",
        filename=file["original_name"],content_disposition_type="attachment",
        headers={"Cache-Control":"private, no-store","X-Content-Type-Options":"nosniff",
                 "Content-Security-Policy":"default-src 'none'"})


# Customer management. Deleting a selected customer permanently removes their
# sessions, orders, delivery records and private uploaded delivery files.
# Backups, provider logs and external payment records are not affected.
@app.get("/api/v1/admin/customers", dependencies=[Depends(require_admin)])
def admin_customers():
    with connection() as db:
        rows = db.execute(
            "SELECT c.id,c.username,c.display_name,c.contact,c.created_at,"
            "COUNT(o.id) AS order_count,"
            "COALESCE(SUM(CASE WHEN o.status='pending' THEN 1 ELSE 0 END),0) AS pending_count,"
            "COALESCE(SUM(CASE WHEN o.status='paid' THEN 1 ELSE 0 END),0) AS paid_count,"
            "COALESCE(SUM(CASE WHEN o.status='fulfilled' THEN 1 ELSE 0 END),0) AS fulfilled_count "
            "FROM customers c LEFT JOIN orders o ON o.customer_id=c.id "
            "GROUP BY c.id ORDER BY c.id DESC LIMIT 500"
        ).fetchall()
    return {"items": [dict(row) for row in rows]}

class ConfirmCustomerDeletion(BaseModel):
    confirm_username: str = Field(min_length=3, max_length=32)

@app.delete("/api/v1/admin/customers/{customer_id}",
            dependencies=[Depends(require_admin)])
async def admin_delete_customer(customer_id: int, confirmation: ConfirmCustomerDeletion):
    if customer_id < 1:
        raise HTTPException(404, "Không tìm thấy tài khoản")
    # Prevent delivery upload and order completion from racing with account deletion.
    async with _eri_upload_lock:
        with connection() as db:
            db.execute("PRAGMA secure_delete=ON")
            customer = db.execute(
                "SELECT username FROM customers WHERE id=?", (customer_id,)
            ).fetchone()
            if customer is None:
                raise HTTPException(404, "Không tìm thấy tài khoản")
            if not hmac.compare_digest(
                confirmation.confirm_username.lower().encode("utf-8"),
                customer["username"].lower().encode("utf-8")
            ):
                raise HTTPException(422, "Tên tài khoản xác nhận không khớp")
            order_ids = [r["id"] for r in db.execute(
                "SELECT id FROM orders WHERE customer_id=?", (customer_id,)
            ).fetchall()]
            deliveries = [r["storage_name"] for r in db.execute(
                "SELECT d.storage_name FROM order_deliveries d "
                "JOIN orders o ON o.id=d.order_id WHERE o.customer_id=?",
                (customer_id,)
            ).fetchall()]
            # Delete UDID submissions and unexpired scan links for this customer.
            db.execute("DELETE FROM cert_order_udids WHERE customer_id=?", (customer_id,))
            db.execute("DELETE FROM cert_udid_sessions WHERE customer_id=?", (customer_id,))
            # Rows are removed, not anonymized or retained. This includes
            # pending, paid, fulfilled and cancelled orders.
            db.execute(
                "DELETE FROM order_deliveries WHERE order_id IN "
                "(SELECT id FROM orders WHERE customer_id=?)", (customer_id,)
            )
            db.execute("DELETE FROM orders WHERE customer_id=?", (customer_id,))
            db.execute("DELETE FROM customer_sessions WHERE customer_id=?", (customer_id,))
            db.execute("DELETE FROM customers WHERE id=?", (customer_id,))
        files_removed = 0
        for filename in deliveries:
            if filename and Path(filename).name == filename:
                target = UPLOAD_DIR / filename
                if target.is_file():
                    target.unlink(missing_ok=True)
                    files_removed += 1
        # Also remove incomplete delivery uploads for the deleted orders.
        order_set = set(order_ids)
        if order_set and UPLOAD_DIR.is_dir():
            for meta in UPLOAD_DIR.glob(".*.delivery.json"):
                try:
                    data = json.loads(meta.read_text("utf-8"))
                except (OSError, ValueError):
                    continue
                if data.get("order_id") in order_set:
                    part = meta.with_name(meta.name[:-len(".delivery.json")] + ".delivery.part")
                    part.unlink(missing_ok=True)
                    meta.unlink(missing_ok=True)
    return {
        "deleted": True, "deleted_orders": len(order_ids),
        "deleted_delivery_files": files_removed
    }

# Consent-based Apple profile scan for certificate orders only.
# The module performs a migration that only creates new tables.
from udid_routes import install_udid_routes
install_udid_routes(app, connection, require_customer, require_admin)
