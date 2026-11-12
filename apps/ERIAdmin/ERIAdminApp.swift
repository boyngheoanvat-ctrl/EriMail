import SwiftUI

@main
struct ERIAdminApp: App {
    var body: some Scene { WindowGroup { AdminHome() } }
}

private struct AdminHome: View {
    @AppStorage("eri_admin_api") private var api = ""
    @AppStorage("eri_admin_key") private var key = ""
    @State private var status = "Chưa kết nối máy chủ"
    var body: some View {
        NavigationView {
            Form {
                Section("Kết nối máy chủ riêng") {
                    TextField("https://api.example.com", text: $api)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                    SecureField("Khóa quản trị", text: $key)
                    Button("Kiểm tra kết nối") {
                        Task {
                            guard let url = URL(string: api.trimmingCharacters(in: .whitespacesAndNewlines) + "/health"),
                                  url.scheme == "https" else { status = "Cần URL HTTPS hợp lệ"; return }
                            do {
                                let (_, response) = try await URLSession.shared.data(from: url)
                                status = (response as? HTTPURLResponse)?.statusCode == 200 ? "Máy chủ phản hồi" : "Máy chủ chưa sẵn sàng"
                            } catch { status = "Không kết nối được: \(error.localizedDescription)" }
                        }
                    }
                    Text(status).font(.footnote)
                }
                Section("Quản lý") {
                    Label("Đơn hàng và xác nhận MoMo", systemImage: "checkmark.shield")
                    Label("Danh mục AI Plus", systemImage: "sparkles")
                    Label("Chứng chỉ iOS", systemImage: "checkmark.seal")
                    Label("Kho IPA và cập nhật phiên bản", systemImage: "square.and.arrow.up")
                    Text("Các chức năng quản trị cần kết nối backend và xác thực trước khi sử dụng.")
                        .font(.footnote).foregroundColor(.secondary)
                }
            }.navigationTitle("ERI ADMIN")
        }
    }
}
