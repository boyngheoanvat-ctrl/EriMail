"""ERI NGUYEN backend — independently hosted API. No third-party scraping or fake signing."""
import os
import sqlite3
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
        CREATE TABLE IF NOT EXISTS certificates (\n          id INTEGER PRIMARY KEY, name TEXT NOT NULL, issuer TEXT NOT NULL,\n          description TEXT NOT NULL, price_vnd INTEGER NOT NULL,\n          active INTEGER NOT NULL DEFAULT 1\n        );\n        CREATE TABLE IF NOT EXISTS offers (
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
    return {"items": [dict(x) for x in rows]}

@app.get("/api/v1/offers")
def offers():
    with connection() as db:
        rows = db.execute("SELECT id,title,description,source_url FROM offers WHERE active=1 ORDER BY id DESC LIMIT 100").fetchall()
    return {"items": [dict(x) for x in rows]}

@app.get("/api/v1/capabilities")
def capabilities():
    return {"ipa_catalog": True, "ai_products": True, "offers": True,
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
    return {"items": [dict(x) for x in rows]}

@app.post("/api/v1/admin/certificates", dependencies=[Depends(require_admin)], status_code=201)
def add_certificate(item: CertificateProduct):
    with connection() as db:
        cur = db.execute("INSERT INTO certificates(name,issuer,description,price_vnd,active) VALUES(?,?,?,?,?)",
            (item.name,item.issuer,item.description,item.price_vnd,int(item.active)))
    return {"id": cur.lastrowid}
