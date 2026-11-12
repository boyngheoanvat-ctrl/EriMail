import SwiftUI
import Security

private let server = "https://eri-api-dag8.onrender.com"

@main
struct ERIAdminApp: App {
    var body: some Scene { WindowGroup { AdminHome() } }
}

private enum SecureKey {
    static let account = "eri_admin_token"
    static let service = "vn.eri.admin"
    static func load() -> String {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service, kSecAttrAccount as String: account,
            kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else { return "" }
        return String(data: data, encoding: .utf8) ?? ""
    }
    static func save(_ value: String) {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service, kSecAttrAccount as String: account]
        SecItemDelete(query as CFDictionary)
        guard !value.isEmpty, let data = value.data(using: .utf8) else { return }
        var attributes = query
        attributes[kSecValueData as String] = data
        attributes[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(attributes as CFDictionary, nil)
    }
}

private struct Envelope<T: Decodable>: Decodable { let items: [T] }
private struct Order: Decodable, Identifiable {
    let id: String
    let kind: String
    let product_name: String
    let amount_vnd: Int
    let status: String
}
private struct IPAEntry: Encodable {
    let name: String
    let version: String
    let bundle_id: String
    let download_url: String
    let sha256: String
    let authorized: Bool
}

@MainActor
private final class AdminSession: ObservableObject {
    @Published var key = SecureKey.load()
    @Published var message = ""
    var authenticated: Bool { !key.isEmpty }
    func request(_ path: String, method: String = "GET", data: Data? = nil) throws -> URLRequest {
        guard let url = URL(string: server + path), url.scheme == "https" else { throw URLError(.badURL) }
        guard key.count >= 32 else { throw URLError(.userAuthenticationRequired) }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.httpBody = data
        req.setValue(key, forHTTPHeaderField: "X-ERI-Admin-Key")
        if data != nil { req.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        return req
    }
    func send(_ path: String, method: String = "GET", data: Data? = nil) async throws -> Data {
        let (body, response) = try await URLSession.shared.data(for: request(path, method: method, data: data))
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200...299).contains(status) else {
            if status == 401 || status == 403 { throw AdminError.invalidKey }
            throw AdminError.http(status)
        }
        return body
    }
    func activate(_ candidate: String) async {
        let old = key
        key = candidate.trimmingCharacters(in: .whitespacesAndNewlines)
        do {
            _ = try await send("/api/v1/admin/orders")
            SecureKey.save(key)
            message = ""
        } catch {
            key = old
            message = "Không xác thực được: " + error.localizedDescription
        }
    }
    func signOut() { SecureKey.save(""); key = ""; message = "" }
}
private enum AdminError: LocalizedError {
    case invalidKey
    case http(Int)
    var errorDescription: String? {
        switch self {
        case .invalidKey: return "Khóa quản trị không đúng hoặc đã hết hiệu lực."
        case .http(let status): return "Máy chủ trả về HTTP \(status)."
        }
    }
}

private struct AdminHome: View {
    @StateObject private var session = AdminSession()
    @State private var showLogin = false
    var body: some View {
        NavigationStack {
            List {
                NavigationLink { OrdersView(kind: "ai").environmentObject(session) } label: {
                    Label("Giao hàng AI", systemImage: "sparkles")
                }
                NavigationLink { IPAUpdateView().environmentObject(session) } label: {
                    Label("Update IPA Mod", systemImage: "square.and.arrow.up")
                }
                NavigationLink { OrdersView(kind: "certificate").environmentObject(session) } label: {
                    Label("Giao hàng chứng chỉ", systemImage: "checkmark.seal")
                }
            }
            .navigationTitle("ERI ADMIN")
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button { showLogin = true } label: {
                        Image(systemName: session.authenticated ? "lock.shield" : "lock")
                    }
                }
            }
            .sheet(isPresented: $showLogin) { LoginView().environmentObject(session) }
            .onAppear { if !session.authenticated { showLogin = true } }
        }
    }
}

private struct LoginView: View {
    @EnvironmentObject private var session: AdminSession
    @Environment(\.dismiss) private var dismiss
    @State private var draft = ""
    @State private var working = false
    var body: some View {
        NavigationStack {
            Form {
                Section("Kích hoạt quản trị một lần") {
                    SecureField("ERI_ADMIN_KEY từ Render", text: $draft)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    Button(working ? "Đang xác thực..." : "Kích hoạt thiết bị") {
                        working = true
                        Task {
                            await session.activate(draft)
                            working = false
                            if session.message.isEmpty { dismiss() }
                        }
                    }.disabled(draft.count < 32 || working)
                    if !session.message.isEmpty {
                        Text(session.message).foregroundStyle(.red)
                    }
                }
                Section {
                    Text("Máy chủ đã cài sẵn. Khóa chỉ lưu trong iOS Keychain trên thiết bị này, không nhúng vào IPA.")
                        .font(.footnote).foregroundStyle(.secondary)
                    if session.authenticated {
                        Button("Đăng xuất thiết bị", role: .destructive) {
                            session.signOut()
                            dismiss()
                        }
                    }
                }
            }
            .navigationTitle("Bảo mật")
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Đóng") { dismiss() } } }
        }
    }
}

private struct OrdersView: View {
    @EnvironmentObject private var session: AdminSession
    let kind: String
    @State private var orders: [Order] = []
    @State private var references: [String: String] = [:]
    @State private var status = ""
    var body: some View {
        List {
            if !status.isEmpty { Text(status).foregroundStyle(.secondary) }
            ForEach(orders.filter { $0.kind == kind }) { order in
                VStack(alignment: .leading, spacing: 8) {
                    Text(order.product_name).font(.headline)
                    Text("Mã đơn: " + order.id).font(.caption)
                    Text(order.amount_vnd.formatted(.currency(code: "VND")))
                    Text("Trạng thái: " + order.status)
                    if order.status == "pending" {
                        TextField("Mã giao dịch MoMo đã đối chiếu", text: Binding(
                            get: { references[order.id] ?? "" },
                            set: { references[order.id] = $0 }
                        )).textInputAutocapitalization(.never)
                        Button("Xác nhận đã nhận tiền") { Task { await update(order, "paid") } }
                            .disabled((references[order.id] ?? "").count < 6)
                        Button("Hủy đơn", role: .destructive) { Task { await update(order, "cancelled") } }
                    } else if order.status == "paid" {
                        Button("Đã giao sản phẩm thực tế") { Task { await update(order, "fulfilled") } }
                    }
                }.padding(.vertical, 6)
            }
        }
        .navigationTitle(kind == "ai" ? "Giao hàng AI" : "Giao chứng chỉ")
        .toolbar { Button("Làm mới") { Task { await refresh() } } }
        .task { await refresh() }
    }
    private func refresh() async {
        do {
            orders = try JSONDecoder().decode(Envelope<Order>.self,
                from: await session.send("/api/v1/admin/orders")).items
            status = orders.filter { $0.kind == kind }.isEmpty ? "Chưa có đơn hàng." : ""
        } catch { status = error.localizedDescription }
    }
    private func update(_ order: Order, _ newStatus: String) async {
        var payload = ["status": newStatus]
        if newStatus == "paid" { payload["transaction_reference"] = references[order.id] ?? "" }
        do {
            let body = try JSONEncoder().encode(payload)
            _ = try await session.send("/api/v1/admin/orders/" + order.id + "/status",
                                       method: "POST", data: body)
            await refresh()
        } catch { status = error.localizedDescription }
    }
}

private struct IPAUpdateView: View {
    @EnvironmentObject private var session: AdminSession
    @State private var name = ""
    @State private var version = ""
    @State private var bundle = ""
    @State private var link = ""
    @State private var hash = ""
    @State private var authorized = false
    @State private var status = ""
    var body: some View {
        Form {
            Section("Cập nhật IPA Mod") {
                TextField("Tên game", text: $name)
                TextField("Phiên bản", text: $version)
                TextField("Bundle ID", text: $bundle)
                    .textInputAutocapitalization(.never)
                TextField("Link tải IPA HTTPS", text: $link)
                    .textInputAutocapitalization(.never).keyboardType(.URL)
                TextField("SHA-256 của file IPA", text: $hash)
                    .textInputAutocapitalization(.never)
                Toggle("Tôi có quyền phân phối IPA này", isOn: $authorized)
                Button("Đăng IPA lên danh mục") { Task { await publish() } }
                    .disabled(!valid)
                if !status.isEmpty { Text(status).font(.footnote) }
            }
            Section {
                Text("Ứng dụng đăng thông tin IPA qua API. File IPA cần được lưu ở đường dẫn HTTPS riêng; chưa hỗ trợ tải file trực tiếp lên máy chủ.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
        .navigationTitle("Update IPA Mod")
    }
    private var valid: Bool {
        !name.isEmpty && !version.isEmpty && !bundle.isEmpty && authorized &&
        link.hasPrefix("https://") && hash.range(of: "^[a-fA-F0-9]{64}$", options: .regularExpression) != nil
    }
    private func publish() async {
        do {
            let item = IPAEntry(name: name, version: version, bundle_id: bundle,
                                download_url: link, sha256: hash, authorized: true)
            let data = try JSONEncoder().encode(item)
            _ = try await session.send("/api/v1/admin/ipa", method: "POST", data: data)
            status = "Đã cập nhật danh mục IPA."
        } catch { status = "Không cập nhật được: " + error.localizedDescription }
    }
}
