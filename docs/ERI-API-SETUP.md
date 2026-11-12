# ERI API — thiết lập riêng

Backend: `backend/app.py` (FastAPI + SQLite). Đây là API do ERI sở hữu; không phải API của Sky hay MuaCert.

## Chạy thử trên máy chủ

Cài dependencies theo `backend/requirements.txt`. Tạo khóa bí mật ngẫu nhiên tối thiểu 32 ký tự, chỉ đặt trong biến môi trường trên máy chủ:

```bash
export ERI_ADMIN_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
export ERI_DB_PATH="./data/eri.sqlite3"
export ERI_PAYMENT_MODE="static_momo"
cd backend
uvicorn app:app --host 127.0.0.1 --port 8000
```

Triển khai reverse proxy HTTPS trước khi mở ra Internet. Không commit `ERI_ADMIN_KEY`, cookie Sky/MuaCert hoặc dữ liệu khách. Bảo vệ máy chủ bằng firewall, rate limiting và sao lưu database. Không sử dụng cấu hình này như triển khai production nếu chưa kiểm thử.

## API

- `GET /health` — kiểm tra máy chủ
- `GET /api/v1/products` — danh mục AI Plus, chỉ giá bán
- `GET /api/v1/certificates` — danh mục chứng chỉ, chỉ giá bán
- `GET /api/v1/mods` — danh mục IPA được phép phân phối
- `POST /api/v1/orders` — tạo đơn (AI/chứng chỉ)
- `GET /api/v1/orders/{id}` — trạng thái đơn
- `GET /api/v1/admin/orders` — danh sách đơn riêng
- `POST /api/v1/admin/orders/{id}/status` — xác nhận MoMo thủ công, yêu cầu mã giao dịch duy nhất
- `POST /api/v1/admin/products` — thêm gói AI
- `POST /api/v1/admin/certificates` — thêm chứng chỉ
- `GET /api/v1/admin/ipa` — danh mục IPA quản trị
- `POST /api/v1/admin/ipa` — đăng bản IPA được phép phân phối với URL HTTPS và SHA-256

Các endpoint admin yêu cầu header `X-ERI-Admin-Key`. Chỉ lưu giá vốn trong backend, không truyền về IPA khách. Đối với `POST /api/v1/admin/products` và `certificates`, trường `price_vnd` hiện là **giá vốn** và backend cộng 30% để tính giá bán. Nếu muốn giá bán tùy chỉnh (ví dụ 100k thành 300k), cần thay đổi mô hình giá và quy trình duyệt trước khi mở bán.

## Giới hạn hiện tại

Chưa có server được triển khai, domain HTTPS, thanh toán MoMo tự động, kết nối đặt hàng Sky/MuaCert, worker tự giao, tải file IPA trực tiếp lên máy chủ hoặc pipeline build hai IPA. ERI ADMIN mới quản lý đơn; việc đánh dấu fulfilled là thao tác thủ công, không chứng minh sản phẩm đã được giao. Không bật bán hàng thực tế trước khi hoàn thiện và kiểm thử luồng thanh toán/giao hàng.


## Cập nhật tệp trực tiếp từ ERI ADMIN

- IPA Mod Game: chọn tệp .ipa, tên game, phiên bản, Bundle ID, giá vốn và xác nhận quyền phân phối.
- Chứng chỉ: chọn tệp .p12, .mobileprovision hoặc .zip, tên, nhà phát hành và giá vốn.
- AI Plus: chọn tệp .txt, .json, .csv hoặc .zip, tên gói, mô tả và giá vốn.

Admin upload qua HTTPS đến POST /api/v1/admin/uploads/{ipa|certificate|ai} dùng X-ERI-Admin-Key. Giới hạn IPA 512 MB, chứng chỉ 15 MB, AI Plus 20 MB. Tệp gốc được lưu riêng phía server và không hiển thị trong API danh mục của Store. Chưa có chức năng tải tệp công khai hay tự giao hàng: cần triển khai quyền truy cập và giao hàng sau thanh toán riêng.

LƯU Ý: Render Free hiện đặt SQLite ở /tmp/eri.sqlite3 nên database và file upload không bền vững qua lần khởi động/triển khai lại. Trước khi bán hàng, hãy cấu hình ERI_DB_PATH và ERI_UPLOAD_DIR trên bộ lưu trữ riêng bền vững (hoặc object storage), mã hóa tệp chứa khóa riêng/credential, backup, giới hạn tốc độ và kiểm soát truy cập. Không đặt khóa quản trị hay khóa thanh toán trong app Store. URL công khai của API không phải bí mật.
