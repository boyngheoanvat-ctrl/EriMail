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
