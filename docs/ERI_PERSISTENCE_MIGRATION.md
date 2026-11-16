# ERI Store — chuyển dữ liệu sang Supabase mà không mất tài khoản

## Hiện trạng

- Production `eri-api-dag8` (Render Free/Oregon) đang dùng SQLite và thư mục upload trên ổ tạm.
- Kho riêng tư Supabase `eri-private-uploads` đã tạo, hạn mức mỗi object 16 MiB; API vault chia file thành 4 MiB.
- Phiên bản ổn định hiện tại trên `main` **chưa được thay thế**. Nhánh `fix/eri-persistent-storage` đã kiểm thử giả lập.
- Production chưa có snapshot SQLite trên Supabase: **không được merge và deploy nhánh này vào Render API lúc này**.

## Điều kiện bắt buộc trước cutover

1. Dừng đăng ký, mua hàng, upload, xóa khách hàng, giao file và các thao tác ghi trên máy chủ cũ.
2. Xuất bản sao nhất quán của `ERI_DB_PATH` trên **chính máy chủ đang chạy**, cùng toàn bộ `ERI_UPLOAD_DIR`, trước khi restart/redeploy.
3. Nếu không có bản sao cũ: **không tự động khởi tạo SQLite rỗng**. Việc thay thế cơ sở dữ liệu sẽ làm khách hàng phải đăng ký lại. Cần chấp thuận rõ ràng cho bất kỳ chuyển đổi trắng dữ liệu nào.
4. Render Free **không hỗ trợ Shell/SSH**; ở thời điểm viết tài liệu không có API xuất file SQLite sẵn trên phiên bản production, nên kết nối hiện tại không thể tự xuất dữ liệu cũ. Không tuyên bố đã khôi phục tài khoản cũ.

## Kiểm tra bản sao và upload kho

Trên môi trường được phép truy cập bản sao SQLite và thư mục upload:

```bash
cd backend
python migrate_live_storage.py --db /path/to/eri.sqlite3 --uploads /path/to/eri-private-uploads
# Lệnh trên chỉ kiểm tra, không ghi Supabase.
```

Nếu danh sách đầy đủ, không có file thiếu, không có tiến trình ghi đồng thời, đặt `ERI_VAULT_SECRET` riêng tư (không commit GitHub), rồi chạy:

```bash
python migrate_live_storage.py --db /path/to/eri.sqlite3 --uploads /path/to/eri-private-uploads --commit
```

Lệnh này kiểm tra toàn bộ tệp trước, sao lưu SQLite **sau cùng**, rồi xác nhận pointer. Nó dừng và báo lỗi nếu thiếu một file, chưa có khóa riêng hoặc kho đã có DB mới; không âm thầm ghi đè người dùng.

## Cắt chuyển production

- Chỉ sau khi snapshot trên Supabase đã được xác minh, đặt biến môi trường Render `ERI_VAULT_SECRET` và `ERI_VAULT_MODE=required` cùng với code mới trong một cửa sổ bảo trì.
- Chuyển đổi an toàn cần tránh tự động triển khai nhánh `main` khi biến môi trường chưa được đặt, vì dịch vụ Render hiện có `autoDeploy=yes`.
- **Tuyệt đối không dùng `ERI_VAULT_MODE=preview` trên production.** Preview chỉ dùng ở môi trường `ERI_STORE_ENV=isolated-preview` cùng cờ khởi tạo riêng.
- Sau khi API online, kiểm tra bằng khóa Admin qua `GET /api/v1/admin/vault/status`: `enabled=true`, `ready=true`, số lượng `customers` và `uploads` khớp trước cutover.
- Kiểm tra đăng nhập bằng tài khoản sẵn có mà **không tạo lại tài khoản**; kiểm tra tải IPA/APK sau khi xóa tệp cache cục bộ; kiểm tra đơn hàng đã giao.
- Duy trì dự phòng mã nguồn và bản sao SQLite đã xuất cho đến khi kiểm tra tất cả.

## Khôi phục sự cố và bảo mật

- Nếu snapshot không tồn tại hoặc lỗi SHA-256, API `required` **không được khởi tạo một cơ sở dữ liệu trắng**.
- Mọi thay đổi SQLite được snapshot sau commit; các bản sao lưu lưu hai thế hệ để có thể kiểm tra hồi phục.
- Chỉ chấp nhận upload hoàn thành sau khi các phần trên kho ngoài tồn tại; các upload nhanh có thể khôi phục phiên và byte đã xác nhận.
- Kho Supabase là `private`; khóa `ERI_VAULT_SECRET` chỉ nằm trong cấu hình máy chủ. Tài khoản người dùng lưu hash + salt, không lưu mật khẩu thuần.
- Lỗi kho ngoài trả 503, không giả vờ thành công.
- Supabase Free: 1 GB lưu trữ và 50 MB/object. Cấu hình 4 MiB/chunk chỉ vượt giới hạn *một tệp*, không vượt hạn mức **tổng 1 GB**.

## Kiểm thử

```bash
python -m pip install -r backend/requirements.txt
python -m pip install httpx2==2.13.0
cd backend
python -m unittest -v test_eri_vault.py test_eri_persistence.py test_live_storage_migration.py
```

Các bài thử mô phỏng mất SQLite, mất phiên chunk upload, phục hồi khách hàng, hoàn tất và tải IPA, dọn tệp từ xa và kiểm tra checksum.

> Mã đã kiểm thử giả lập không tương đương với dữ liệu đã được chuyển trên Render. Không được xác nhận triển khai production trước khi xuất và xác minh bản sao cũ.
