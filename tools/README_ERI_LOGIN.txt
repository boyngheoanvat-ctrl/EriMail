ERI LOGIN — BỘ TÍCH HỢP IPA / APK
====================================
Đây KHÔNG phải ứng dụng demo. Bộ này gồm mã native ERI LOGIN và công cụ tích hợp.

TỆP
  LOGIN.py             Công cụ Python tự dò IPA / APK
  ERILogin.dylib       Thư viện ERI LOGIN iOS ARM64 build bằng iPhoneOS SDK
  ERIAndroidGate.dex   Activity ERI LOGIN Android build bằng Android SDK

CHẠY TRÊN iPHONE (IPA)
  1. Giải nén cùng một thư mục Documents.
  2. Đặt IPA ĐƯỢC PHÉP SỬA vào cùng thư mục với 3 tệp trên.
  3. Mở LOGIN.py trong ứng dụng Python và chạy.
  4. Nếu IPA tương thích, script chèn LC_LOAD_DYLIB vào Mach-O và thêm
     ERILogin.dylib trong Frameworks, tạo *_ERI_LOGIN_unsigned.ipa.
  5. PHẢI ký lại toàn bộ IPA, bao gồm app + dylib, với chứng chỉ và
     provisioning profile hợp lệ. Sau đó cài và kiểm thử trên iPhone.

ANDROID (APK)
  - Cần Java, apktool, Android SDK Build Tools (zipalign/apksigner), keytool.
  - Chạy LOGIN.py trong môi trường có các lệnh trên.
  - Script thay MAIN/LAUNCHER Activity thành ERILoginActivity và thêm
    lớp Android vào classesN.dex. Nếu có đủ công cụ, tự tạo khóa ký và ký APK.
  - Không thể chạy quy trình apktool đầy đủ bằng Python thuần trên iPhone.
  - Nếu chạy trên iPhone không có apktool, script báo thiếu công cụ và dừng,
    không tạo APK giả.

KEY
  - Khi mở app: ERI LOGIN yêu cầu key; key được gửi qua HTTPS đến Render.
  - Server tự liên kết key với mã thiết bị của lần kích hoạt đầu.
  - Key bị khóa, hết hạn hoặc gắn máy khác sẽ bị từ chối.
  - Tạo key trong ERI KEY ADMIN với App ID chính xác (Bundle ID/Package name).
  - API: https://eri-login-api.onrender.com

GIỚI HẠN QUAN TRỌNG
  - iOS: chỉ hỗ trợ app có main executable ARM64 thin, không mã hóa FairPlay,
    đủ vùng đệm Mach-O cho LC_LOAD_DYLIB. FAT / mã hóa / không padding sẽ dừng.
  - Dylib chỉ trình bày lớp Login trên cửa sổ ứng dụng; game engine có thể
    chạy bên dưới. Không thể đảm bảo khóa game tuyệt đối khi không có mã nguồn.
  - Android: chỉ hỗ trợ APK có đúng một MAIN/LAUNCHER, apktool decode/build được.
    App có kiểm tra chữ ký, anti-tamper, split APK hoặc kiểm tra bảo vệ riêng
    có thể không cài/chạy sau khi sửa.
  - Android ký bằng khóa mới có thể không nâng cấp đè lên APK cùng package ID
    đã cài bằng khóa khác; gỡ app cũ có thể làm mất dữ liệu.
  - Không có IPA game Garena nào được chứng nhận đã chạy Login cho tới khi
    thử cài, chạy và kiểm tra trên iPhone thật.
  - Chỉ sử dụng trên ứng dụng được phép sửa và phân phối.

MÃ NGUỒN
https://github.com/boyngheoanvat-ctrl/EriMail/tree/eri-login-dylib-injector
