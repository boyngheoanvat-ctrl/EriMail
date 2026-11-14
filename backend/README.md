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


## Xử lý IPA độc lập (ERI IPA Worker)

Máy chủ ERI có worker riêng, không gọi API của F4 hay dịch vụ giải mã bên thứ ba.
Worker chỉ tải IPA từ nguồn HTTPS do chủ máy chủ **có quyền phân phối** đăng ký,
kiểm tra SHA-256, cấu trúc `Payload/*.app`, Bundle ID, phiên bản và Mach-O
`cryptid=0` trước khi công bố file tải thật trong kho ERI.

**Giới hạn quan trọng:** Worker này không gỡ mã hóa FairPlay của ứng dụng App Store.
Apple Search API trả metadata, không trả IPA đã giải mã. Với ứng dụng không có nguồn
IPA đã được cấp quyền, trạng thái là `waiting_for_source` và KHÔNG hiển thị %
xử lý giả. File nguồn còn FairPlay bị từ chối; không ký số được bằng cách
đổi tên hay đóng gói lại. Các file IPA hợp lệ có thể ký qua mục Ký IPA trên web.

Cấu hình máy chủ (không đưa khóa admin hoặc mật khẩu Apple vào trình duyệt):
- `ERI_IPA_SOURCE_HOSTS`: danh sách tên miền HTTPS tin cậy được phép tải IPA,
  cách nhau bằng dấu phẩy, ví dụ `files.example.org`. Không đặt tên miền F4.
- `ERI_PIPELINE_MAX_IPA_BYTES`: kích thước tải tối đa (mặc định 536870912 byte).
  Nâng giá trị chỉ khi có đủ bộ nhớ, ổ đĩa và giới hạn giải nén phù hợp.
- `ERI_DB_PATH`, `ERI_UPLOAD_DIR`: phải trỏ tới **ổ lưu trữ bền vững** trên
  môi trường sản xuất. Render Free dùng ổ tạm, không đảm bảo IPA qua restart.

Đăng ký nguồn hợp lệ qua API Admin HTTPS:
```sh
curl -X POST https://eri-api-dag8.onrender.com/api/v1/admin/decrypt/sources \
  -H "X-ERI-Admin-Key: $ERI_ADMIN_KEY" -H "Content-Type: application/json" \
  -d '{"name":"Ứng dụng ERI","bundle_id":"com.eri.demo","version":"1.0","url":"https://files.example.org/ERI-Demo.ipa","sha256":"<64 ký tự SHA-256>","distribution_authorized":true}'
```

Sau khi đăng ký, mọi yêu cầu trùng Bundle ID và phiên bản đang chờ sẽ
được worker tự xử lý. Khách hàng không cần nhập chứng chỉ hoặc upload file tại
màn hình tra cứu. Tiến trình lấy trực tiếp từ backend và tải IPA qua
`/api/v1/mods/{id}/download` chỉ khi tệp đã được xác minh và thực sự tồn tại.
