# ERI IPA Studio · ipatool + Ký IPA bằng GitHub Actions

## Phần đã viết

- App Store URL → ipatool đăng nhập Apple Account có quyền tải ứng dụng, tải IPA.
- Kiểm tra ZIP, Info.plist, Bundle ID và mã hóa Mach-O.
- IPA sạch, không mã hóa → ký bằng P12 + mobileprovision trên macOS codesign → artifact ERI-signed-IPA.
- KHÔNG dùng mã nguồn, API hoặc server F4.

**Giới hạn:** ipatool không giải mã FairPlay. IPA tải từ App Store thường vẫn được mã hóa, nên bước kiểm tra sẽ từ chối ký và không xuất file giả. Đổi cryptid trong Mach-O header không giải mã được nội dung.

## Cấu hình GitHub Secrets

Vào repository EriMail → Settings → Secrets and variables → Actions → New repository secret.

Bắt buộc cho cả hai chế độ:
- ERI_P12_BASE64: Nội dung base64 của chứng chỉ P12 do bạn sở hữu.
- ERI_MOBILEPROVISION_BASE64: Nội dung base64 của provisioning profile tương ứng.
- ERI_P12_PASSWORD: Mật khẩu chứng chỉ P12.

Thêm cho chế độ App Store:
- ERI_APPLE_ID: Apple Account có quyền tải app.
- ERI_APPLE_PASSWORD: Mật khẩu Apple Account.
- ERI_IPATOOL_KEYCHAIN_PASSPHRASE: Mật khẩu ngẫu nhiên bảo vệ local keychain của ipatool.
- ERI_APPLE_2FA_CODE: Mã xác thực ngắn hạn (nếu Apple yêu cầu).

Lưu ý: Đôi khi Apple bắt buộc xác thực tương tác, đồng ý điều khoản hoặc xử lý thông báo đăng nhập. Khi đó, lệnh đăng nhập không tương tác trên GitHub Actions có thể thất bại. Không tắt 2FA. Không đưa mật khẩu Apple ID/P12 và file chứng chỉ lên repository, issue, chat, website.

## Chạy workflow

Workflow: .github/workflows/eri-ipatool-sign.yml
Tên trong tab Actions: ERI IPA Studio - ipatool and Signing.

Workflow mới chỉ có nút Run workflow sau khi đã có trong nhánh mặc định (main).
Kiểm tra PR + CI trước khi merge.

### A. Tải ứng dụng bằng ipatool

Chọn source_mode = appstore.

app_store_url = https://apps.apple.com/vn/app/garena-li%C3%AAn-qu%C3%A2n-mobile/id1150288115?l=vi

external_version_id = để trống nếu lấy mới nhất.

Xác nhận bạn có quyền tải/ký/phân phối ứng dụng. Workflow không dùng tùy chọn --purchase nên không tự mua hoặc lấy license khi chưa có.

Nếu IPA còn FairPlay/metadata tài khoản, workflow báo lỗi và dừng; sẽ không có file ký.

### B. Ký trực tiếp IPA sạch do bạn cung cấp

Chọn source_mode = release.

release_tag = tên GitHub Release chứa IPA (ví dụ eri-owned-v1).
release_asset = tên file trong Release (ví dụ OwnedApp.ipa).

Ứng dụng phải có quyền phân phối, không có FairPlay, chứng chỉ và provisioning profile phải hợp lệ và khớp Bundle ID/thiết bị. Workflow ký bằng signing-worker/sign_worker.py rồi kiểm tra IPA đầu ra.

**Cảnh báo:** Nếu repo public thì GitHub Release asset cũng public. Không tải IPA riêng tư, thông tin tài khoản hay ứng dụng chưa có quyền phát hành vào public Release. Với tài liệu nhạy cảm nên dùng repo private hoặc máy Mac ký riêng.

### File tải về

Sau khi workflow thành công, mở run → Artifacts → ERI-signed-IPA.
File artifact là ZIP, bên trong có ERI-signed.ipa. Artifact được giữ 1 ngày.
Không có file nào khi kiểm tra, tải IPA hoặc ký thất bại.

## Website và ERI ADMIN

ERI STORE = website hiện có; tab Ký IPA trực tiếp đã dùng API ERI riêng.
ERI ADMIN = ứng dụng IPA quản trị; không phải website.
GitHub Actions này chạy theo thao tác thủ công, chưa có backend webhook an toàn để khách bấm nút trên shop tự kích hoạt. Không đặt GitHub PAT trong JavaScript của website.

## Kiểm thử không cần Apple ID

Chạy:
python3 -m unittest discover -s signing-worker -p test_eri_ipatool_pipeline.py -v

Unit tests dùng Mach-O giả, không lấy ứng dụng App Store thực, không chứng minh đã giải mã hoặc đã ký IPA thực trên iPhone.
