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

## ERI Decrypt IPA: API và dữ liệu thực tế

- GET /api/v1/tools/ipa/decrypt/search: tìm theo tên, Bundle ID hoặc App Store URL; chọn một trong 10 vùng, Auto tìm song song VN/US/JP.
- GET /api/v1/tools/ipa/decrypt/vault: chỉ hiển thị IPA miễn phí được phép phân phối và còn file, sử dụng endpoint tải /api/v1/mods/{id}/download.
- GET /api/v1/tools/ipa/decrypt/versions: phiên bản hiện tại của Apple và các phiên bản thực sự có trong kho ERI.
- POST /api/v1/tools/ipa/decrypt/requests: tạo yêu cầu theo tài khoản (Bearer token), lưu vào SQLite.
- GET /api/v1/tools/ipa/decrypt/requests/{job_id}: 0% nếu chưa có IPA, 100% chỉ khi có file thật và quyền tải.

Apple Search API chỉ cung cấp metadata, không tải hay giải mã FairPlay. Không sao chép IPA và API của website tham khảo.

QUAN TRỌNG: Render Free đang sử dụng SQLite và file upload trên ổ tạm. Tác vụ có thể khôi phục sau khi đóng trình duyệt nhưng KHÔNG đảm bảo giữ dữ liệu qua deploy/restart. Trước khi deploy backend production, phải sao lưu hoặc chuyển toàn bộ SQLite và IPA sang dịch vụ lưu trữ bền vững. IPA chưa ký vẫn cần P12/mobileprovision hợp lệ trước khi cài iPhone.

## ERI authorized IPA worker — independent backend

Implemented in ipa_verified_worker.py and registered from app.py. It consumes an **admin-approved licensed IPA HTTPS source**, downloads the real IPA, checks SHA-256, Bundle ID, exact version, and Mach-O encryption commands, then publishes a verified file to the existing ERI catalog.

**This is not a FairPlay decrypter.** An App Store URL or public Apple Search API metadata cannot produce the binary. A protected Mach-O (cryptid != 0) is rejected, not modified. iTunesMetadata.plist and SC_Info account metadata are rejected. An authorized source/device exporter must supply the IPA. Do not use F4 or an untrusted external IPA source.

### Environment settings (server-side only)

- ERI_DECRYPT_WORKER_ENABLED=1: runs the worker within the current ERI FastAPI web service. Default 0 (disabled).
- ERI_DECRYPT_SOURCE_HOSTS=ipa.example.org: comma-separated own HTTPS hosts. Without a configured allowlist the API refuses new sources.
- ERI_UPLOAD_DIR=/data/eri-private-uploads and ERI_DB_PATH=/data/eri.sqlite3: example paths when a persistent disk is actually attached. Merely setting the paths without mounting a disk is not sufficient.
- ERI_ADMIN_KEY: existing long server-side secret, never embed into website/IPA.

### Admin workflow

1. Customers find apps using GET /api/v1/tools/ipa/decrypt/search and submit POST /api/v1/tools/ipa/decrypt/requests after ERI STORE login.
2. Using ERI ADMIN IPA or an authenticated admin HTTP client, POST /api/v1/admin/decrypt/sources with JSON fields: name, bundle_id, version, download_url, sha256 (64 hexadecimal chars), authorized:true.
3. Worker accepts allowlisted HTTPS hosts only, refuses redirects, downloads at most 512 MiB, checks file hash and Mach-O, then saves a real file into the ERI vault. It does not bypass DRM.
4. Clients poll GET /api/v1/tools/ipa/decrypt/requests/{job_id}: waiting_for_authorized_file, queued, processing, failed or completed. Only completed jobs with a physically present authorized file return a downloadable path.

Admin diagnostics: GET /api/v1/admin/decrypt/worker and GET /api/v1/admin/decrypt/sources, both requiring X-ERI-Admin-Key. Signed source URLs are never returned in these listings.

CI/tests from backend directory: python test_decrypt_catalog.py && python test_ipa_verified_worker.py

**Important production constraint:** The existing Render Free backend has ephemeral SQLite and upload storage. Deploy/restart can lose customers, orders and IPA files. Free web services also may suspend processing when idle. Do not merge/deploy production until a verified backup and durable storage solution are in place. A persistent disk or managed database plus object storage and an always-on worker may require a paid plan. Check available plans and obtain approval before provisioning billed resources.
