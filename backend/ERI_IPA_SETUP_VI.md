# ERI STORE — Cấu hình nhận yêu cầu IPA tự động

## Những gì hệ thống đã làm được

1. Người dùng nhập tên, Bundle ID, App Store ID hoặc link `https://apps.apple.com/...`.
2. ERI tra cứu thông tin qua Apple Search API và lưu yêu cầu theo tài khoản, phiên bản.
3. Yêu cầu trùng được dùng lại, không tạo hàng đợi ảo; người dùng xem được trạng thái.
4. Khi quản trị viên đã đăng ký **nguồn IPA có quyền phân phối**, worker tải IPA qua HTTPS, xác minh SHA-256, cấu trúc IPA và trạng thái mã hóa Mach-O trước khi xuất file.
5. Nếu chưa có nguồn, hiển thị `waiting_for_source`, **không** thông báo giải mã thành công.

## Cấu hình nguồn IPA có quyền phân phối

File mẫu: `eri_ipa_source_manifest.example.json` (chỉ chứa dữ liệu giả, **không phải nguồn tải thật**).

- Lưu manifest JSON thật trên host HTTPS do ERI quản lý, với từng mục có `name`, `bundle_id`, `version`, `url`, `sha256`, `distribution_authorized: true`.
- Chỉ đăng ký file IPA được phép phân phối; thay đường dẫn và SHA-256 mẫu bằng dữ liệu thực tế.
- Cấu hình `ERI_IPA_SOURCE_MANIFEST_URL` thành URL manifest. Worker sẽ đồng bộ và tìm yêu cầu phù hợp.
- Hoặc quản trị viên có thể dùng `POST /api/v1/admin/decrypt/sources` với quyền admin.
- Endpoint trạng thái: `GET /api/v1/tools/ipa/decrypt/engine`.

## Giới hạn thực tế

- `ipatool` chỉ tải ứng dụng từ Apple với tài khoản và quyền phù hợp; **không tự giải mã FairPlay**.
- Worker hiện tại **không** có bộ giải mã FairPlay; không thể biến mọi link App Store thành IPA đã giải mã.
- `ERI_IPATOOL_AUTO_DOWNLOAD=1` chỉ dùng cho các bundle được phép trong `ERI_AUTO_IPA_BUNDLES`, trên worker riêng có môi trường đăng nhập phù hợp. Không bật khi chưa cấu hình.
- Không dùng API của decrypt.day để làm nguồn mặc định hoặc tái phân phối IPA bên thứ ba khi chưa có quyền.

## Cảnh báo Render production

API production dùng lưu trữ tạm có thể mất dữ liệu sau restart/redeploy. **Không triển khai lên production hoặc merge PR khi chưa chuyển database và kho IPA sang lưu trữ bền vững, sao lưu dữ liệu khách hàng/đơn hàng, và kiểm tra migration.** Hãy kiểm tra ở preview trước.

## API dành cho website

- `GET /api/v1/tools/ipa/decrypt/search?q=<link-or-name>`
- `POST /api/v1/tools/ipa/decrypt/requests` — JSON: `{"target":"https://apps.apple.com/vn/app/id123456789","country":"auto"}`; yêu cầu đăng nhập khách hàng.
- `GET /api/v1/tools/ipa/decrypt/requests` — lịch sử yêu cầu của tài khoản.
- `GET /api/v1/tools/ipa/decrypt/requests/{job_id}` — trạng thái và kết quả thực, nếu có.

**Tiêu chí hoàn thành:** chỉ hiển thị nút tải khi đã có file IPA hợp lệ và có quyền phân phối; không dùng trạng thái thành công giả.
