import SwiftUI

@main
struct ERIAdminApp: App {
    var body: some Scene { WindowGroup { AdminHome() } }
}

private let eriAPI = "https://eri-api-dag8.onrender.com"

private struct OrdersEnvelope: Decodable { let items: [AdminOrder] }
private struct AdminOrder: Decodable, Identifiable {
    let id: String
    let kind: String
    let product_name: String
    let amount_vnd: Int
    let status: String
}
private struct IPAInput: Encodable {
    let name: String
    let version: String
    let bundle_id: String
    let download_url: String
    let sha256: String
    let authorized: Bool
}

private struct AdminHome: View {
    @State private var key = ""
    @State private var showAuth = false
    @State private var section: AdminSection?
    var body: some View {
        NavigationStack {
            VStack(spacing: 18) {
                Text("Quản lý cửa hàng ERI").font(.subheadline).foregroundStyle(.secondary)
                menu("Giao hàng AI", icon: "sparkles", destination: .ai)
                menu("Update IPA Mod", icon: "square.and.arrow.up", destination: .ipa)
                menu("Giao hàng chứng chỉ", icon: "checkmark.seal", destination: .certificate)
                Spacer()
            }
            .padding(20)
            .navigationTitle("ERI ADMIN")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showAuth = true } label: { Image(systemName: "lock.shield") }
                        .accessibilityLabel("Xác thực quản trị")
                }
            }
            .sheet(isPresented: $showAuth) {
                NavigationStack {
                    Form {
                        Section("Bảo mật quản trị") {
                            SecureField("Nhập ERI_ADMIN_KEY từ Render", text: $key)
                                .textInputAutocapitalization(.never)
                                .autocorrectionDisabled()
                            Text("Khóa chỉ giữ trong bộ nhớ khi ứng dụng mở; không được nhúng vào IPA.")
                                .font(.footnote).foregroundStyle(.secondary)
                        }
                        Section("Máy chủ HTTPS") {
                            Text(eriAPI).font(.footnote).textSelection(.enabled)
                        }
                    }
                    .navigationTitle("Xác thực")
                    .toolbar { Button("Xong") { showAuth = false } }
                }
            }
            .navigationDestination(item: $section) { selected in
                if selected == .ipa {
                    IPAUpdateView(key: key)
                } else {
                    DeliveryView(key: key, kind: selected == .ai ? "ai" : "certificate",
                                 title: selected == .ai ? "Giao hàng AI" : "Giao hàng chứng chỉ")
                }
            }
        }
    }
    private func menu(_ title: String, icon: String, destination: AdminSection) -> some View {
        Button { section = destination } label: {
            HStack(spacing: 16) {
                Image(systemName: icon).font(.title2).frame(width: 32)
                Text(title).font(.headline)
                Spacer()
                Image(systemName: "chevron.right").font(.subheadline)
            }
            .padding(20)
            .frame(maxWidth: .infinity)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
        }
        .buttonStyle(.plain)
    }
}

private enum AdminSection: String, Hashable, Identifiable {
    case ai, ipa, certificate
    var id: String { rawValue }
}

private func adminRequest(_ path: String, key: String, method: String = "GET", body: Data? = nil) throws -> URLRequest {
    guard key.count >= 32 else { throw URLError(.userAuthenticationRequired) }
    guard let url = URL(string: eriAPI + path) else { throw URLError(.badURL) }
    var request = URLRequest(url: url)
    request.httpMethod = method
    request.setValue(key, forHTTPHeaderField: "X-ERI-Admin-Key")
    if body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
    request.httpBody = body
    return request
}

private struct DeliveryView: View {
    let key: String
    let kind: String
    let title: String
    @State private var orders: [AdminOrder] = []
    @State private var references: [String: String] = [:]
    @State private var message = ""
    var body: some View {
        List {
            if !message.isEmpty { Text(message).foregroundStyle(.secondary) }
            if orders.isEmpty { Text("Chưa có đơn hàng thuộc mục này.").foregroundStyle(.secondary) }
            ForEach(orders) { order in
                VStack(alignment: .leading, spacing: 10) {
                    Text(order.product_name).font(.headline)
                    Text("Mã đơn: " + order.id).font(.caption)
                    Text(order.amount_vnd.formatted(.currency(code: "VND")))
                    Text("Trạng thái: " + order.status)
                    if order.status == "pending" {
                        TextField("Mã giao dịch MoMo đã kiểm tra", text: Binding(
                            get: { references[order.id] ?? "" },
                            set: { references[order.id] = $0 }
                        )).textInputAutocapitalization(.never).autocorrectionDisabled()
                        Button("Xác nhận đã nhận tiền") { Task { await change(order, status: "paid") } }
                            .disabled((references[order.id] ?? "").count < 6)
                        Button("Hủy đơn", role: .destructive) { Task { await change(order, status: "cancelled") } }
                    }
                    if order.status == "paid" {
                        Text("Chỉ đánh dấu sau khi đã giao sản phẩm thật cho khách.")
                            .font(.caption).foregroundStyle(.secondary)
                        Button("Đánh dấu đã giao") { Task { await change(order, status: "fulfilled") } }
                    }
                }.padding(.vertical, 5)
            }
        }
        .navigationTitle(title)
        .toolbar { Button("Làm mới") { Task { await load() } } }
        .task { await load() }
    }
    @MainActor private func load() async {
        guard key.count >= 32 else { message = "Nhấn quay lại và mở biểu tượng ổ khóa để nhập khóa quản trị."; return }
        do {
            let (data, response) = try await URLSession.shared.data(for: adminRequest("/api/v1/admin/orders", key: key))
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { message = "Máy chủ từ chối khóa quản trị."; return }
            orders = try JSONDecoder().decode(OrdersEnvelope.self, from: data).items.filter { $0.kind == kind }
            message = ""
        } catch { message = "Không tải được đơn: " + error.localizedDescription }
    }
    @MainActor private func change(_ order: AdminOrder, status: String) async {
        do {
            var payload = ["status": status]
            if status == "paid" { payload["transaction_reference"] = references[order.id] ?? "" }
            let data = try JSONSerialization.data(withJSONObject: payload)
            let req = try adminRequest("/api/v1/admin/orders/" + order.id + "/status", key: key, method: "POST", body: data)
            let (_, response) = try await URLSession.shared.data(for: req)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else {
                message = "Không cập nhật được đơn. Kiểm tra mã giao dịch và trạng thái."; return
            }
            await load()
        } catch { message = error.localizedDescription }
    }
}

private struct IPAUpdateView: View {
    let key: String
    @State private var name = ""
    @State private var version = ""
    @State private var bundleID = ""
    @State private var url = ""
    @State private var sha256 = ""
    @State private var authorized = false
    @State private var message = ""
    var body: some View {
        Form {
            Section("Đăng phiên bản IPA") {
                TextField("Tên game", text: $name)
                TextField("Phiên bản", text: $version)
                TextField("Bundle ID", text: $bundleID)
                    .textInputAutocapitalization(.never)
                TextField("Đường dẫn HTTPS tải IPA", text: $url)
                    .textInputAutocapitalization(.never).keyboardType(.URL)
                TextField("SHA-256 của file IPA (64 ký tự)", text: $sha256)
                    .textInputAutocapitalization(.never)
                Toggle("Tôi có quyền phân phối IPA này", isOn: $authorized)
                Button("Đăng IPA lên ERI STORE") { Task { await upload() } }
                    .disabled(!authorized || name.isEmpty || version.isEmpty || bundleID.isEmpty ||
                              !url.hasPrefix("https://") || sha256.count != 64)
                Text("Chức năng này đăng thông tin và link tải IPA, không tải trực tiếp file từ iPhone lên máy chủ.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            if !message.isEmpty { Section { Text(message) } }
        }
        .navigationTitle("Update IPA Mod")
    }
    @MainActor private func upload() async {
        do {
            let payload = IPAInput(name: name, version: version, bundle_id: bundleID,
                                   download_url: url, sha256: sha256, authorized: authorized)
            let req = try adminRequest("/api/v1/admin/ipa", key: key, method: "POST",
                                       body: JSONEncoder().encode(payload))
            let (_, response) = try await URLSession.shared.data(for: req)
            if (response as? HTTPURLResponse)?.statusCode == 201 {
                message = "Đã thêm IPA vào danh mục ERI STORE."
            } else { message = "Máy chủ từ chối yêu cầu. Kiểm tra khóa, URL và SHA-256." }
        } catch {
            message = key.count < 32 ? "Mở biểu tượng ổ khóa để nhập khóa quản trị." : error.localizedDescription
        }
    }
}
