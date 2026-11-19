# ERI ADMIN — tải nguyên IPA/APK qua Cloudflare R2 (không chia phần)

## Công dụng

Bản này có chế độ **Gửi nguyên file – 1 lần** mặc định trong ERI ADMIN:
iPhone gửi đúng một yêu cầu HTTPS `PUT` trực tiếp đến R2. Không tạo 173
phần IPA 4 MiB trên iPhone hay ghép toàn bộ tệp trên ổ tạm Render.

- Tối đa **5 GiB mỗi tệp** khi dùng một `PutObject` Cloudflare R2.
- Tệp lớn hơn 5 GiB có thể chuyển sang chế độ **tải nhiều phần** R2,
  tối đa theo giới hạn của R2, hiện khoảng 4.995 TiB.
- Chế độ một yêu cầu PUT **không thể resume giữa chừng**. Khi mất mạng,
  tải lại toàn bộ file, không nên hứa là nhanh hơn trong mọi điều kiện.
- `URLSession.upload(fromFile:)` đọc tệp từ đĩa, không nạp nguyên IPA vào RAM.
- Chỉ khi R2 xác nhận đủ dung lượng, backend mới tải qua R2 để kiểm tra
  SHA-256 toàn tệp, ZIP và Info.plist / AndroidManifest.xml rồi mới
  thêm bản ghi sản phẩm vào SQLite.

## Cấu hình phải có trên Render

Tại Render `eri-api-dag8` > Environment (không ghi giá trị vào GitHub hoặc chat):

```text
ERI_R2_MOD_UPLOADS=enabled
ERI_R2_ACCOUNT_ID=<32-digit Cloudflare account ID>
ERI_R2_BUCKET=<private existing R2 bucket>
ERI_R2_ACCESS_KEY_ID=<scoped object access key>
ERI_R2_SECRET_ACCESS_KEY=<scoped secret key>
```

R2 bucket phải có dung lượng còn trống đủ chứa tệp, tài khoản sở hữu
phải cho phép thao tác và đồng ý với bất cứ chi phí nào phát sinh.
Không xóa, chuyển hoặc sửa các tệp IPA cũ trong Supabase khi bật R2.
Điều này **không biến Render Free thành server không ngủ**.

## API

- `GET /api/v1/admin/r2-mods/config` báo `single_put_max_bytes` nếu sẵn sàng.
- `POST /api/v1/admin/r2-mods/start-direct` tạo phiên được lưu trong
  SQLite bền vững, có quyền Admin; trả `upload_id`.
- `GET /api/v1/admin/r2-mods/{id}/direct-ticket` cấp presigned PUT ngắn hạn.
  Quyền R2 không bao giờ được gửi xuống iPhone.
- iPhone PUT trực tiếp vào kho R2 và theo dõi số byte thực gửi.
- `GET /api/v1/admin/r2-mods/{id}/status` kiểm tra toàn bộ object.
- `POST /api/v1/admin/r2-mods/{id}/complete` xác thực file và đăng bán,
  có thể gọi lại an toàn nếu xác thực mạng bị ngắt.

Các endpoint và flow multipart cũ được giữ lại nguyên vẹn để không phá phiên
đang có. Nếu R2 chưa cấu hình, ERI ADMIN **không tự âm thầm chia 173 phần**
khi người dùng chọn chế độ gửi nguyên file; app báo rõ thiếu R2 và cho
chủ shop tắt tùy chọn đó nếu muốn dùng cơ chế cũ.

## Triển khai an toàn

Không gộp và redeploy khi tệp đang được tải lên. Với Render Free, SQLite và
các phần tải chưa hoàn tất trên `/tmp` có thể mất khi restart. Trước khi
triển khai phải đối chiếu bản sao lưu SQLite, danh mục sản phẩm hiện có
và đối tượng tệp; sau khi triển khai xác minh khôi phục dữ liệu và thử
một tệp IPA thực tế 724,1 MB rồi tải ngược lại kiểm tra SHA-256.

Không thể đảm bảo hoàn tất tải file trong 5 giây: thời gian tối thiểu
phụ thuộc kích thước file, băng thông tải lên và xác minh máy chủ.
