import SwiftUI

@main
struct ERIAdminApp: App {
    var body: some Scene { WindowGroup { AdminHome() } }
}
private struct OrdersEnvelope: Decodable { let items: [AdminOrder] }
private struct AdminOrder: Decodable, Identifiable {
    let id: String
    let product_name: String
    let amount_vnd: Int
    let status: String
}
private struct AdminHome: View {
    @AppStorage("eri_admin_api") private var api = "https://eri-api-dag8.onrender.com"
    @State private var key = ""
    @State private var orders: [AdminOrder] = []
    @State private var status = "Chưa kết nối"
    @State private var transaction: [String: String] = [:]
    var body: some View {
        NavigationView {
            Form {
                Section("Kết nối máy chủ riêng") {
                    TextField("https://api.example.com", text: $api)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                    SecureField("Khóa quản trị (không lưu trên máy)", text: $key)
                    Button("Tải đơn hàng") { Task { await refresh() } }
                    Text(status).font(.footnote).foregroundColor(.secondary)
                }
                Section("Đơn hàng") {
                    if orders.isEmpty { Text("Chưa có đơn hoặc chưa kết nối").foregroundColor(.secondary) }
                    ForEach(orders) { order in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(order.product_name).font(.headline)
                            Text("Mã: " + order.id).font(.caption)
                            Text(order.amount_vnd.formatted(.currency(code: "VND")))
                            Text("Trạng thái: " + order.status).font(.subheadline)
                            if order.status == "pending" {
                                TextField("Mã giao dịch MoMo đã kiểm tra", text: Binding(
                                    get: { transaction[order.id] ?? "" },
                                    set: { transaction[order.id] = $0 }
                                )).textInputAutocapitalization(.never).autocorrectionDisabled()
                                Button("Xác nhận đã nhận tiền") {
                                    Task { await change(order, to: "paid") }
                                }.disabled((transaction[order.id] ?? "").count < 6)
                                Button("Hủy đơn", role: .destructive) {
                                    Task { await change(order, to: "cancelled") }
                                }
                            }
                            if order.status == "paid" {
                                Text("Chỉ xác nhận giao xong khi đã giao sản phẩm thực tế.")
                                    .font(.caption).foregroundColor(.secondary)
                                Button("Đánh dấu đã giao") { Task { await change(order, to: "fulfilled") } }
                            }
                        }.padding(.vertical, 4)
                    }
                }
                Section("Quản lý kho IPA") {
                    Text("Danh mục IPA công khai được cập nhật bằng API quản trị /api/v1/admin/ipa.")
                    Text("Chỉ thêm IPA bạn có quyền phân phối. Không đưa khóa quản trị vào ứng dụng khách.")
                        .font(.footnote).foregroundColor(.secondary)
                }
            }.navigationTitle("ERI ADMIN")
        }
    }
    private func request(_ path: String, method: String = "GET", body: Data? = nil) throws -> URLRequest {
        guard let url = URL(string: api.trimmingCharacters(in: .whitespacesAndNewlines) + path),
              url.scheme == "https", url.host != nil else { throw URLError(.badURL) }
        guard key.count >= 32 else { throw URLError(.userAuthenticationRequired) }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.setValue(key, forHTTPHeaderField: "X-ERI-Admin-Key")
        if body != nil { req.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        req.httpBody = body
        return req
    }
    @MainActor private func refresh() async {
        do {
            let (data, response) = try await URLSession.shared.data(for: request("/api/v1/admin/orders"))
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.userAuthenticationRequired) }
            orders = try JSONDecoder().decode(OrdersEnvelope.self, from: data).items
            status = "Đã kết nối · " + String(orders.count) + " đơn"
        } catch { status = "Lỗi kết nối/xác thực: " + error.localizedDescription }
    }
    @MainActor private func change(_ order: AdminOrder, to newStatus: String) async {
        do {
            var payload: [String: String] = ["status": newStatus]
            if newStatus == "paid" { payload["transaction_reference"] = transaction[order.id] ?? "" }
            let data = try JSONSerialization.data(withJSONObject: payload)
            let req = try request("/api/v1/admin/orders/" + order.id + "/status", method: "POST", body: data)
            let (_, response) = try await URLSession.shared.data(for: req)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else {
                status = "Không thể cập nhật đơn. Kiểm tra mã giao dịch và trạng thái."
                return
            }
            transaction[order.id] = nil
            await refresh()
        } catch { status = "Lỗi cập nhật: " + error.localizedDescription }
    }
}
