# ERI NGUYỄN Backend (MVP)

Independent FastAPI + SQLite service. Public endpoints:
- GET /health
- GET /api/v1/capabilities
- GET /api/v1/ipa?q=...
- GET /api/v1/products
- GET /api/v1/offers

Admin-only POST endpoints: /api/v1/admin/ipa, /api/v1/admin/products, /api/v1/admin/offers.
Set a long random ERI_ADMIN_KEY environment variable. Send it in X-ERI-ADMIN-KEY only from your trusted admin client; **never ship this key inside an IPA**.

Local start:
```sh
cd backend
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
ERI_ADMIN_KEY='replace-with-long-random-secret' uvicorn app:app --reload
```

Deploy behind HTTPS reverse proxy, configure backups, access logs and rate limits before production. For Docker persist /data volume and pass ERI_ADMIN_KEY as a secret. Native iOS app should use its own public HTTPS ERI_API_BASE_URL configured at build time.

Important limitations:
- This is an independently managed catalog, not a mirror of F4TEAM, Sky World or CodeVN. Their data and accounts require an authorized integration.
- IPA signing is NOT implemented. A separate isolated macOS signing worker, secure temporary file handling, certificate/provision validation and download expiry are required.
- Payments and AI Plus fulfillment are NOT implemented. Do not accept money until merchant integration, entitlements and delivery are completed.
- Only add IPA URLs when distribution is authorized. Do not upload certificate private keys to this API.

## Static MoMo QR checkout
Set `ERI_PAYMENT_MODE=static_momo` (default). The iOS app generates the static MoMo QR from the exact payload provided by the merchant. This is not a dynamic QR: buyers must enter the order total manually and optionally the transfer note `ERI <order-id>`. Orders remain `pending` until the merchant verifies the incoming transfer and marks them `paid` using `POST /api/v1/admin/orders/{order_id}/status` with `{"status":"paid"}`. Only then may the merchant fulfill the product. Never mark paid on the basis of a customer tapping a button or presenting a screenshot.

For a later bank-account QR integration, set `ERI_PAYMENT_MODE=bank` plus `ERI_BANK_BIN`, `ERI_BANK_ACCOUNT`, and `ERI_BANK_NAME`. Avoid placing admin keys in the iOS binary.

API order endpoints: `POST /api/v1/orders` (kind `ai` or `certificate`, product_id), `GET /api/v1/orders/{id}`, `GET /api/v1/admin/orders`. Product fulfillment and payment reconciliation are not automated.
