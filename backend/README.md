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

Máy chủ ERI có worker riêng, không gọi bất kỳ API giải mã bên thứ ba nào.
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
  cách nhau bằng dấu phẩy, ví dụ `files.example.org`. Chỉ cho phép tên miền nguồn IPA do bạn quản lý hoặc có quyền sử dụng.
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

## ERI IPA ADMIN trên iPhone

Sau khi triển khai bản GitHub mới lên cả `eri-store-web` và `eri-api-dag8`,
mở **https://eri-store-web.onrender.com/ipa-admin.html** bằng Safari.

1. Nhập `ERI_ADMIN_KEY` từ cấu hình bí mật trên Render (không dán vào GitHub).
2. Chọn IPA do bạn sở hữu hoặc được phép phân phối, nhập tên và xác nhận quyền.
3. Bấm **Tải lên và xác minh**. File được truyền thành các phần 4 MiB, mỗi phần
   xác minh SHA-256, hiển thị % đã được máy chủ xác nhận. Nếu ngắt kết nối,
   chọn lại cùng file và bấm tiếp tục; khóa admin không lưu trong localStorage.
4. Backend kiểm tra `Info.plist`, Bundle ID, phiên bản và Mach-O. IPA còn
   FairPlay hoặc không xác minh được sẽ bị từ chối, không được công bố là
   đã giải mã. IPA đã kiểm tra được lưu vào kho và có liên kết tải thực.
5. Phần **Yêu cầu IPA** phía dưới cho biết người dùng nào đang chờ
   nguồn và phiên bản nào đã sẵn sàng.

Nếu dùng trường **Nhập IPA từ nguồn HTTPS** thay vì upload, cấu hình
`ERI_IPA_SOURCE_HOSTS` bằng tên miền lưu trữ IPA do bạn kiểm soát và
đăng ký SHA-256 chính xác. Worker không tự giải mã FairPlay và không lấy
file từ Apple Search API.

**Quan trọng về triển khai:** Hiện cả hai dịch vụ Render được cài
`autoDeploy=yes`, nhưng kết quả kiểm tra ngày 15/11/2026 cho thấy bản
deploy live chưa đồng bộ với nhánh `main`. Không vội deploy API khi SQLite
và file IPA vẫn ở ổ tạm: redeploy có thể làm mất tài khoản, đơn hàng, phiên
tải và dữ liệu kho. Sao lưu hoặc chuyển sang lưu trữ bền vững trước khi
chuyển sang bản live. Nếu chỉ cập nhật giao diện có thể deploy dịch vụ
`eri-store-web` riêng; API cần cùng phiên bản để nhận endpoint mới.


## ERI Native IPA Queue — không phụ thuộc API giải mã bên ngoài

Luồng dịch vụ:

1. GET /api/v1/tools/ipa/decrypt/search?q=... tìm metadata từ Apple và kiểm tra kho ERI.
2. POST /api/v1/tools/ipa/decrypt/start (ERI Bearer token), body JSON gồm target,country,version. Tạo hoặc tái sử dụng ID tác vụ bền vững trong SQLite.
3. Nếu IPA đã được kiểm định trong kho, trả state=completed cùng result.download_path. Nếu chưa có nguồn, trả state=waiting_for_source và progress=null; không bịa tiến độ.
4. Khi admin đăng ký nguồn HTTPS kèm SHA-256 và xác nhận quyền phân phối qua /api/v1/admin/decrypt/sources, hoặc đồng bộ manifest có cấp quyền qua /api/v1/admin/decrypt/sync-manifest, worker ERI tự xử lý các yêu cầu phù hợp.
5. Worker kiểm tra host nguồn cho phép, kích thước tải, SHA-256, ZIP CRC, Payload/*.app, Bundle ID, phiên bản và trạng thái mã hóa Mach-O. File lỗi, còn FairPlay hoặc không rõ trạng thái bị từ chối.
6. GET /api/v1/tools/ipa/decrypt/job/{id} (ERI Bearer token) trả trạng thái thật và đường dẫn tải khi hoàn tất; GET /api/v1/tools/ipa/decrypt/requests trả lịch sử theo khách hàng.
7. GET /api/v1/tools/ipa/decrypt/capabilities công bố giới hạn kỹ thuật và số nguồn IPA đã đăng ký.

Đây là bộ điều phối IPA do ERI sở hữu, không sử dụng F4TEAM, cookie, CSRF hay khóa giải mã bên thứ ba. App Store URL chỉ cho phép lấy metadata; không tự tạo IPA đã giải mã từ Apple. Quy trình gỡ DRM FairPlay không được triển khai. Nếu xử lý ứng dụng thuộc quyền sở hữu của mình, hãy lấy IPA qua phương thức phân phối hợp lệ và đưa tệp hoặc nguồn có cấp quyền vào ERI.

Triển khai: ERI_DB_PATH và ERI_UPLOAD_DIR cần trỏ tới lưu trữ bền vững. Render Free dùng ổ tạm; hãy sao lưu tài khoản, đơn hàng, kho IPA trước khi triển khai lại backend.

## ERI Store – GitHub Actions IPA Automation (nguồn được duyệt)

Workflow: `.github/workflows/process-ipa.yml`; worker: `backend/actions_ipa_worker.py`; manifest: `backend/eri_actions_sources.json`.
Đây là worker thay thế tài nguyên tính toán Render cho các IPA đã có nguồn hợp lệ, không lấy/gỡ FairPlay trực tiếp từ Apple App Store.

### Chuẩn bị nguồn

Trong manifest (duyệt thay đổi bằng pull request), thêm một mục ví dụ:

```json
{
  "items": [
    {
      "id": "eri-demo-1",
      "name": "ERI Demo",
      "bundle_id": "com.eri.demo",
      "version": "1.0.0",
      "url": "https://files.your-domain.example/eri-demo.ipa",
      "sha256": "thay-bằng-64-ký-tự-hex-của-tệp",
      "distribution_authorized": true
    }
  ]
}
```

Không dùng URL App Store làm URL IPA đầu vào. Chỉ đăng ký file do bạn sở hữu/được phép phân phối và hash SHA-256 của file thật.
Trong GitHub Settings → Secrets and variables → Actions → **Variables**, tạo `ERI_IPA_SOURCE_HOSTS` với danh sách hostname HTTPS được cho phép (ví dụ `files.your-domain.example`). Nếu chưa cấu hình host hoặc manifest còn trống, workflow sẽ từ chối chạy; đó là hành vi an toàn.

### Kích hoạt

- Thủ công: GitHub → Actions → **ERI Store approved IPA processor** → Run workflow, nhập `source_id` đã tồn tại trong manifest.
- Qua API riêng của ERI: quản trị viên gọi `POST /api/v1/admin/decrypt/actions/dispatch` với `X-ERI-Admin-Key` và JSON `{"source_id":"eri-demo-1"}`. Backend gửi workflow_dispatch tới GitHub. Không cung cấp GitHub token cho trình duyệt/khách hàng.
- Cấu hình **Render backend secrets**: `ERI_GH_ACTIONS_REPO=boyngheoanvat-ctrl/EriMail` và `ERI_GH_ACTIONS_TOKEN` (GitHub fine-grained token có quyền Actions: Read and write đúng repository này). Không commit token.
- Xem nguồn đã duyệt: `GET /api/v1/admin/decrypt/actions/sources`. Xem trạng thái workflow: `GET /api/v1/admin/decrypt/actions/runs`. Các route đều chỉ dành cho quản trị viên.

### Output & giới hạn

Worker chỉ xử lý ID nguồn trong manifest, tải HTTPS qua allowlist, kiểm tra dung lượng, SHA-256, ZIP CRC, nguy cơ zip bomb, Info.plist, Bundle ID, version và Mach-O `cryptid`. IPA còn FairPlay hoặc không xác minh được bị từ chối. **Không xóa** `SC_Info` và `_CodeSignature`, không tự động ký; tệp gốc được giữ nguyên để không làm hỏng chữ ký.

Khi chạy thành công, GitHub Actions lưu `eri-<source_id>.ipa` cùng `validation.json` vào Artifact (mặc định 1 ngày). Artifact là tệp riêng của run, **không phải liên kết IPA công khai vĩnh viễn**. Cần thêm Cloudflare R2/S3 và cơ chế liên kết tải có xác thực nếu muốn khách trên ERI Store tải trực tiếp từ giao diện sau khi GitHub xử lý. Hiện endpoint trigger chỉ dành cho admin và chưa tự động nhập artifact vào kho ERI.

Chạy kiểm thử: `cd backend && python3 test_actions_ipa_worker.py && python3 test_actions_bridge.py` sau khi cài `requirements.txt`.

Về vận hành: GitHub Actions có hạn mức, thời gian chạy và retention; không mặc định không tốn phí ở mọi cấu hình. Không cho người dùng nhập URL tùy ý hoặc thực thi lệnh shell của họ.
