# ERI STORE — tài khoản khách và giao hàng thủ công

## Tài khoản riêng cho từng khách
- Khách đăng ký tên đăng nhập, mật khẩu (tối thiểu 10 ký tự), tên người nhận và cách liên hệ (Zalo hoặc số điện thoại).
- Khách đăng nhập mới được tạo đơn từ ERI STORE iOS và Android.
- Mỗi đơn gắn vào `customer_id` trên backend, không nhận `customer_id` từ ứng dụng.
- Ứng dụng lưu phiên đăng nhập an toàn: iOS Keychain; Android Keystore với AES-GCM. Backend chỉ lưu hash SHA-256 của token và hash PBKDF2-HMAC-SHA256 của mật khẩu.
- `GET /api/v1/me/orders` chỉ trả đơn của người đang đăng nhập; `GET /api/v1/orders/{id}` cũng kiểm tra chủ sở hữu. Đăng xuất sẽ thu hồi phiên.

## ERI ADMIN
- Mục **Lịch sử khách mua** liệt kê đơn của cả Mod Game, Chứng chỉ và AI Plus: tên người nhận, username, liên hệ nhận hàng, mã đơn, thời điểm, số tiền, loại hàng và trạng thái.
- Admin đối chiếu giao dịch MoMo thật trước khi bấm **Đã nhận tiền**. Sau khi giao thủ công thì đánh dấu **Đã giao**.
- Đơn cũ chưa có tài khoản sẽ hiện “Đơn cũ chưa có tài khoản”; không tự gán đơn cũ cho khách mới.
- AI Plus và Chứng chỉ chỉ đăng tên, mô tả, giá; **không yêu cầu upload tệp**. Mod Game vẫn dùng tệp IPA do Admin nhập.

## API
- `POST /api/v1/auth/register`, `POST /api/v1/auth/login`, `POST /api/v1/auth/logout`, `GET /api/v1/auth/me`.
- `POST /api/v1/orders`, `GET /api/v1/orders/{id}`, `GET /api/v1/me/orders` yêu cầu `Authorization: Bearer <customer-token>`.
- `GET /api/v1/admin/orders` yêu cầu khóa Admin, trả thông tin liên hệ của khách. Không đưa tên, số liên hệ hoặc token vào API danh mục công khai.
- Không tự động xác nhận MoMo. Tài khoản mới đăng ký **không phải tài khoản đã xác thực danh tính**.

## Giới hạn trước khi triển khai thực tế
**Quan trọng:** Render Free hiện dùng SQLite và file upload trong bộ nhớ đĩa tạm. Khi server triển khai/khởi động lại, tài khoản, lịch sử mua hàng, hình QR và IPA có thể mất. Không chuyển bản này lên môi trường bán hàng thật cho đến khi có **database và storage bền vững**, sao lưu và quy trình khôi phục.

Trước khi phát hành công khai nên bổ sung giới hạn đăng nhập/rate-limit, khôi phục mật khẩu, cơ chế xác thực liên hệ, chính sách riêng tư và chính sách lưu trữ/xóa dữ liệu khách. Hiện đăng ký chỉ xác minh mật khẩu, không xác minh số điện thoại hoặc chủ tài khoản MoMo.
