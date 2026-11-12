"""ERI NGUYEN backend — independently hosted API. No third-party scraping or fake signing."""
import os
import sqlite3
import secrets
import re
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Header, Depends
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

init()

def require_admin(x_eri_admin_key: str | None = Header(default=None)):
    if not ADMIN_KEY or x_eri_admin_key != ADMIN_KEY:
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
    return {"ipa_catalog": True, "ai_products": True, "offers": True, "markup_percent": 30,
            "ipa_signing": False, "payments": False, "third_party_catalog_sync": False,
            "install_manifest_catalog": False}

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

PAYMENT_MODE = os.getenv("ERI_PAYMENT_MODE", "static_momo").strip()
BANK_BIN = os.getenv("ERI_BANK_BIN", "").strip()
BANK_ACCOUNT = os.getenv("ERI_BANK_ACCOUNT", "").strip()
BANK_NAME = os.getenv("ERI_BANK_NAME", "").strip()

class NewOrder(BaseModel):
    kind: str = Field(pattern="^(ai|certificate)$")
    product_id: int = Field(gt=0)

def public_order(row):
    data = dict(row)
    data.pop("product_id", None)
    data.pop("created_at", None)
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
    if PAYMENT_MODE != "static_momo" and (not BANK_BIN or not BANK_ACCOUNT or not BANK_NAME):
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

class OrderStatusUpdate(BaseModel):
    status: str = Field(pattern="^(paid|cancelled|fulfilled)$")

@app.post("/api/v1/admin/orders/{order_id}/status", dependencies=[Depends(require_admin)])
def update_order_status(order_id: str, item: OrderStatusUpdate):
    with connection() as db:
        old = db.execute("SELECT status FROM orders WHERE id=?", (order_id,)).fetchone()
        if old is None:
            raise HTTPException(404, "Order not found")
        allowed = {"pending": {"paid", "cancelled"}, "paid": {"fulfilled"}, "cancelled": set(), "fulfilled": set()}
        if item.status not in allowed[old["status"]]:
            raise HTTPException(409, "Invalid order status transition")
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
