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

### Tình trạng website thật và IPA dung lượng lớn (15/11/2026)

- Website ERI dùng trực tiếp POST /api/v1/tools/ipa/decrypt/requests và GET /api/v1/tools/ipa/decrypt/requests/{job_id}, tương thích API sản xuất cũ lẫn mã mới; không tạo 404 vô ích ở /start hay /job.
- Các API /start và /job có trong mã nguồn backend nhưng sẽ chỉ hoạt động khi backend Render được triển khai an toàn. Không triển khai Render Free khi chưa sao lưu SQLite và các tệp upload từ phiên đang chạy.
- GitHub Actions Worker có giới hạn mặc định **5 GiB cho file IPA tải xuống** và **10 GiB tổng kích thước ZIP chưa nén**, tách riêng giới hạn HTTP API/upload thấp hơn để tránh tốn tài nguyên Render. Các giá trị worker có thể thay bằng ERI_ACTIONS_MAX_IPA_BYTES và ERI_ACTIONS_MAX_UNPACKED_BYTES sau khi kiểm tra dung lượng runner và phí lưu trữ.
- File đầu ra được di chuyển trong cùng filesystem (không tạo bản sao IPA thứ hai), phù hợp hơn cho file vài GB. GitHub hosted ubuntu-latest thường có khoảng **14 GB SSD**, nên việc chạy với file lớn vẫn phụ thuộc dung lượng trống, tốc độ tải, giới hạn GitHub Artifact và thời gian tác vụ.
- Nghịch Thủy Hàn App Store báo khoảng 3,85 GB **không đồng nghĩa ERI đã có IPA đầu vào**. Danh sách backend/eri_actions_sources.json phải có source hợp lệ cùng Bundle ID, phiên bản, SHA-256 và quyền phân phối trước khi workflow có thể tải và kiểm tra file. Không nhận URL App Store làm file IPA.
- 200 từ tìm kiếm, 201 khi yêu cầu và 200 khi xem trạng thái là thành công; "waiting_for_source" là trạng thái chưa có nguồn, **không phải đang giải mã hoặc lỗi API**.

Về vận hành: GitHub Actions có hạn mức, thời gian chạy và retention; không mặc định không tốn phí ở mọi cấu hình. Không cho người dùng nhập URL tùy ý hoặc thực thi lệnh shell của họ.

## Tự động hóa IPA nhiều GB: GitHub Actions → Cloudflare R2 (15/11/2026)

Workflow `.github/workflows/process-ipa.yml` tự chạy khi manifest nguồn được duyệt ở `backend/eri_actions_sources.json` thay đổi trên nhánh main. Không phải admin upload IPA từng file. Với backend mới đã cấu hình đầy đủ, thao tác **Yêu cầu IPA** của khách cũng tự phát GitHub `workflow_dispatch` khi đúng Bundle ID + version tồn tại trong manifest nguồn đã cấp quyền. Các yêu cầu trùng phiên bản được gộp lại, tránh nhân đôi tác vụ.

Worker: `backend/actions_ipa_worker.py` tải file bằng luồng và kiểm tra SHA-256, ZIP CRC, Bundle ID, phiên bản, `cryptid`; `backend/actions_r2_publish.py` tự chuyển tệp đã xác minh sang bucket **R2 private** bằng multipart upload, không cần admin tải về rồi upload lại. GitHub Artifact chỉ lưu báo cáo JSON, không copy IPA nhiều GB.

Backend: `backend/eri_auto_ipa.py` nhận callback nội bộ có token, xác nhận HEAD metadata/size từ R2, đánh dấu các yêu cầu của đúng ứng dụng/phiên bản hoàn tất. Người dùng đăng nhập ERI sẽ lấy link tải có chữ ký R2 tồn tại 15 phút qua `GET /api/v1/tools/ipa/decrypt/remote-url/{job_id}`, không thấy GitHub/R2 credentials. Bất kỳ callback nào không có token, sai manifest/SHA/bucket/object key đều bị từ chối. `GET /api/v1/tools/ipa/decrypt/auto-status` trả trạng thái cấu hình công khai, không lộ khóa.

### Cấu hình cần có — không commit token vào GitHub

GitHub repo **Secrets**: `ERI_R2_ACCOUNT_ID`, `ERI_R2_BUCKET`, `ERI_R2_ACCESS_KEY_ID`, `ERI_R2_SECRET_ACCESS_KEY`, `ERI_ACTIONS_CALLBACK_TOKEN` (token mạnh, tạo độc lập).

GitHub repo **Variables**: `ERI_IPA_SOURCE_HOSTS` (domain nguồn IPA được cấp quyền), `ERI_ACTIONS_CALLBACK_URL=https://eri-api-dag8.onrender.com/api/v1/worker/ipa/complete`. Tuỳ chọn `ERI_ACTIONS_MAX_UNPACKED_BYTES` để đặt giới hạn an toàn dữ liệu ZIP chưa nén.

Render API **Environment secrets**: `ERI_GH_ACTIONS_REPO=boyngheoanvat-ctrl/EriMail`, `ERI_GH_ACTIONS_TOKEN`, `ERI_AUTO_ACTIONS_ENABLED=true`, bốn giá trị R2 tương ứng và cùng giá trị `ERI_ACTIONS_CALLBACK_TOKEN` từ GitHub. Cần quyền GitHub Actions write để dispatch và quyền R2 đọc object + HEAD để cấp link tải.

R2 bucket mặc định private. Không cần mở public access hoặc để file trong GitHub Releases. Đặt quyền bucket hẹp nhất có thể, duy trì quota và tránh log/commit secrets.

### Giới hạn thực tế

**Không có dung lượng vô hạn vật lý.** Worker không đặt giới hạn cứng 5 GiB cho tải IPA khi `ERI_ACTIONS_MAX_IPA_BYTES=0`, nhưng tự kiểm tra ổ trống của runner và luôn giữ tối thiểu 1 GiB dự phòng. Runner Ubuntu tiêu chuẩn có khoảng 14 GB SSD; file lớn hơn cần runner ổ lớn/self-hosted. R2 multipart tối đa khoảng 5 TiB/object. Vẫn phải kiểm tra ZIP bomb, thời gian thực thi, chi phí storage/bandwidth và quyền phân phối trước khi xử lý. `ERI_ACTIONS_MAX_UNPACKED_BYTES` vẫn mặc định giới hạn dữ liệu ZIP chưa nén 10 GiB để tránh tài nguyên quá lớn.

**Chưa có nguồn**: `backend/eri_actions_sources.json` hiện trống; ngay cả khi workflow hoàn tất và secrets cấu hình, không thể tạo IPA cho Nghịch Thủy Hàn chỉ từ App Store URL. Apple Search API cho metadata chứ không cho IPA đã giải mã. Nhà phát hành/các nguồn được ủy quyền cần cung cấp file IPA đầu vào thực tế. Không tự động lấy cookie Apple hoặc bypass FairPlay.

**Render Free**: backend sản xuất hiện sử dụng SQLite và file trên ổ tạm. Không deploy backend mới trước khi sao lưu thật dữ liệu tài khoản/đơn hàng và chuyển lưu trữ dài hạn. PR này là mã nguồn mới, chưa đồng nghĩa hệ thống sản xuất có token hoặc dữ liệu đã bền vững.
