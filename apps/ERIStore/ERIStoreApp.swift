import SwiftUI
import Security

private func eriMoney(_ amount: Int) -> String {
    let formatter = NumberFormatter()
    formatter.numberStyle = .decimal
    formatter.locale = Locale(identifier: "vi_VN")
    formatter.maximumFractionDigits = 0
    return (formatter.string(from: NSNumber(value: amount)) ?? String(amount)) + "đ"
}


@main
struct ERIStoreApp: App {
    var body: some Scene { WindowGroup { StoreHome().frame(maxWidth: .infinity, maxHeight: .infinity) } }
}

private let publicAPI = "https://eri-api-dag8.onrender.com"


private enum BuyerToken {
    private static let service = "vn.eri.store.customer"
    private static let account = "customer_bearer_token"

    static func read() -> String {
        let attributes: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(attributes as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data else { return "" }
        return String(data: data, encoding: .utf8) ?? ""
    }

    static func write(_ value: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service, kSecAttrAccount as String: account
        ]
        SecItemDelete(query as CFDictionary)
        guard !value.isEmpty else { return }
        var data = query
        data[kSecValueData as String] = Data(value.utf8)
        data[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(data as CFDictionary, nil)
    }
}

private struct Buyer: Decodable {
    let id: Int
    let username: String
    let display_name: String
    let contact: String
}

private struct BuyerAuthentication: Decodable {
    let token: String
    let customer: Buyer
}
private struct BuyerOrderResponse: Decodable { let items: [CheckoutOrder] }

@MainActor
private final class BuyerSession: ObservableObject {
    @Published private(set) var buyer: Buyer?
    @Published private(set) var token = BuyerToken.read()

    private func call(_ path: String, method: String = "GET", data: Data? = nil,
                      needsLogin: Bool = true) async throws -> Data {
        guard let url = URL(string: publicAPI + path), url.scheme == "https" else {
            throw PurchaseError.server("Địa chỉ máy chủ không hợp lệ")
        }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.timeoutInterval = 30
        if data != nil {
            req.httpBody = data
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if needsLogin {
            guard !token.isEmpty else { throw PurchaseError.server("Vui lòng đăng nhập trước khi mua") }
            req.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        }
        let (body, result) = try await URLSession.shared.data(for: req)
        let code = (result as? HTTPURLResponse)?.statusCode ?? 0
        guard (200...299).contains(code) else {
            if code == 401 && needsLogin {
                token = ""
                buyer = nil
                BuyerToken.write("")
                throw PurchaseError.server("Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại.")
            }
            if code == 404 && (path == "/api/v1/auth/register" || path == "/api/v1/auth/login") {
                throw PurchaseError.server("Máy chủ chưa cập nhật chức năng tài khoản. Admin cần triển khai backend mới trên Render.")
            }
            if code == 409 { throw PurchaseError.server("Tên đăng nhập đã được sử dụng") }
            let detail = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any]
            let message = detail?["detail"] as? String ?? "Máy chủ trả về HTTP " + String(code)
            throw PurchaseError.server(message)
        }
        return body
    }

    func restore() async {
        guard !token.isEmpty else { return }
        do {
            buyer = try JSONDecoder().decode(Buyer.self,
                from: await call("/api/v1/auth/me"))
        } catch {
            buyer = nil
            token = ""
            BuyerToken.write("")
        }
    }

    func authenticate(register: Bool, username: String, password: String,
                      name: String, contact: String) async throws {
        var parameters: [String: String] = [
            "username": username.trimmingCharacters(in: .whitespacesAndNewlines),
            "password": password
        ]
        if register {
            parameters["display_name"] = name.trimmingCharacters(in: .whitespacesAndNewlines)
            parameters["contact"] = contact.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        let body = try JSONEncoder().encode(parameters)
        let data = try await call(register ? "/api/v1/auth/register" : "/api/v1/auth/login",
                                  method: "POST", data: body, needsLogin: false)
        let auth = try JSONDecoder().decode(BuyerAuthentication.self, from: data)
        token = auth.token
        buyer = auth.customer
        BuyerToken.write(token)
    }

    func buy(kind: String, productID: Int) async throws -> CheckoutOrder {
        let payload = try JSONSerialization.data(withJSONObject: [
            "kind": kind, "product_id": productID
        ])
        return try JSONDecoder().decode(CheckoutOrder.self,
            from: await call("/api/v1/orders", method: "POST", data: payload))
    }

    func orders() async throws -> [CheckoutOrder] {
        let data = try await call("/api/v1/me/orders")
        return try JSONDecoder().decode(BuyerOrderResponse.self, from: data).items
    }

    func signOut() async {
        if !token.isEmpty {
            _ = try? await call("/api/v1/auth/logout", method: "POST")
        }
        token = ""
        buyer = nil
        BuyerToken.write("")
    }
}

private struct BuyerAuthView: View {
    @EnvironmentObject private var session: BuyerSession
    @Environment(\.dismiss) private var dismiss
    @State private var registerMode = false
    @State private var username = ""
    @State private var password = ""
    @State private var name = ""
    @State private var contact = ""
    @State private var waiting = false
    @State private var message = ""

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Picker("Tài khoản", selection: $registerMode) {
                        Text("Đăng nhập").tag(false)
                        Text("Đăng ký").tag(true)
                    }.pickerStyle(.segmented)
                }
                Section("Tài khoản ERI STORE") {
                    TextField("Tên đăng nhập (3–32 ký tự)", text: $username)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                        .textContentType(.username)
                    SecureField("Mật khẩu", text: $password)
                        .textContentType(registerMode ? .newPassword : .password)
                    if registerMode {
                        TextField("Tên người nhận", text: $name)
                            .textContentType(.name)
                        TextField("Liên hệ nhận hàng (Zalo / điện thoại)", text: $contact)
                            .textContentType(.telephoneNumber)
                    }
                }
                Section {
                    Button(waiting ? "Đang xử lý..." : (registerMode ? "Tạo tài khoản" : "Đăng nhập")) {
                        Task { await submit() }
                    }.disabled(waiting || username.isEmpty || password.isEmpty ||
                               (registerMode && (name.isEmpty || contact.isEmpty)))
                    if !message.isEmpty { Text(message).foregroundStyle(.red).font(.footnote) }
                }
                Section {
                    Text("Mỗi khách cần tài khoản riêng để mua hàng. Liên hệ nhận hàng chỉ hiển thị cho Admin, không hiển thị công khai. Không dùng mật khẩu MoMo.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            .navigationTitle(registerMode ? "Đăng ký" : "Đăng nhập")
            .toolbar { ToolbarItem(placement: .topBarTrailing) {
                Button("Đóng") { dismiss() }
            } }
        }
    }

    private func submit() async {
        waiting = true
        defer { waiting = false }
        do {
            if registerMode && password.count < 10 {
                throw PurchaseError.server("Mật khẩu phải có ít nhất 10 ký tự.")
            }
            try await session.authenticate(register: registerMode, username: username,
                                           password: password, name: name, contact: contact)
            dismiss()
        } catch {
            message = error.localizedDescription
        }
    }
}

private struct BuyerProfileView: View {
    @EnvironmentObject private var session: BuyerSession
    @Binding var showAuth: Bool
    @State private var history: [CheckoutOrder] = []
    @State private var selected: CheckoutOrder?
    @State private var message = ""

    var body: some View {
        NavigationStack {
            List {
                if let buyer = session.buyer {
                    Section("Tài khoản của tôi") {
                        LabeledContent("Tên", value: buyer.display_name)
                        LabeledContent("Tên đăng nhập", value: buyer.username)
                        LabeledContent("Liên hệ nhận hàng", value: buyer.contact)
                        Button("Đăng xuất", role: .destructive) {
                            Task {
                                await session.signOut()
                                history = []
                            }
                        }
                    }
                    Section("Lịch sử mua hàng") {
                        Button("Làm mới lịch sử") { Task { await refresh() } }
                        if !message.isEmpty { Text(message).font(.footnote) }
                        ForEach(history) { order in
                            Button { selected = order } label: {
                                VStack(alignment: .leading, spacing: 5) {
                                    Text(order.product_name).font(.headline)
                                    Text("Mã đơn: " + order.id)
                                    Text(eriMoney(order.amount_vnd) + " · " + order.status)
                                    Text(order.created_at ?? "").font(.caption)
                                }
                            }
                        }
                    }
                } else {
                    Section {
                        Text("Đăng ký hoặc đăng nhập để mua và xem lịch sử đơn hàng.")
                        Button("Đăng nhập / Đăng ký") { showAuth = true }
                    }
                }
            }
            .navigationTitle("Tài khoản")
            .sheet(item: $selected) { order in OrderDetailView(order: order) }
            .task { await refresh() }
        }
    }

    private func refresh() async {
        guard session.buyer != nil else { return }
        do {
            history = try await session.orders()
            message = history.isEmpty ? "Chưa có đơn hàng." : ""
        } catch { message = error.localizedDescription }
    }
}

private struct StoreHome: View {
    @StateObject private var session = BuyerSession()
    @State private var showAuth = false

    var body: some View {
        TabView {
            StoreCatalog(title: "AI Plus", endpoint: "products", showAuth: $showAuth)
                .tabItem { Label("AI Plus", systemImage: "sparkles") }
            StoreCatalog(title: "Chứng chỉ", endpoint: "certificates", showAuth: $showAuth)
                .tabItem { Label("Chứng chỉ", systemImage: "checkmark.seal") }
            ModCatalog(showAuth: $showAuth)
                .tabItem { Label("Mod Game", systemImage: "gamecontroller") }
            BuyerProfileView(showAuth: $showAuth)
                .tabItem { Label("Tài khoản", systemImage: "person.crop.circle") }
        }
        .environmentObject(session)
        .sheet(isPresented: $showAuth) { BuyerAuthView().environmentObject(session) }
        .task { await session.restore() }
    }
}

private struct StoreItem: Decodable, Identifiable {
    let id: Int
    let name: String
    let description: String
    let price_vnd: Int
}
private struct StoreResponse: Decodable { let items: [StoreItem] }
private struct StoreCatalog: View {
    @EnvironmentObject private var session: BuyerSession
    let title: String
    let endpoint: String
    @Binding var showAuth: Bool
    @State private var items: [StoreItem] = []
    @State private var checkout: CheckoutOrder?
    @State private var buying = false
    @State private var status = "Chưa kết nối máy chủ"
    var body: some View {
        NavigationView {
            List {
                if items.isEmpty { Text(status).foregroundColor(.secondary) }
                ForEach(items) { item in
                    VStack(alignment: .leading, spacing: 6) {
                        Text(item.name).font(.headline)
                        Text(item.description).font(.subheadline)
                        Text(eriMoney(item.price_vnd)).fontWeight(.semibold)
                        Text("Giao thủ công sau khi đối chiếu tiền MoMo")
                            .font(.footnote).foregroundStyle(.secondary)
                        Button(buying ? "Đang tạo đơn..." : "Mua ngay") { Task { await buy(item) } }
                            .buttonStyle(.borderedProminent).disabled(buying)
                    }.padding(.vertical, 5)
                }
            }.navigationTitle(title)
                .sheet(item: $checkout) { order in OrderDetailView(order: order) }
                .toolbar {
                    ToolbarItem(placement: .navigationBarTrailing) { Button("Làm mới") { Task { await load() } } }
                }
                .task { await load() }
        }
    }
    @MainActor private func buy(_ item: StoreItem) async {
        guard session.buyer != nil else {
            showAuth = true
            return
        }
        buying = true
        defer { buying = false }
        do {
            checkout = try await session.buy(
                kind: endpoint == "products" ? "ai" : "certificate",
                productID: item.id
            )
        } catch { status = "Không tạo được đơn: " + error.localizedDescription }
    }
    @MainActor private func load() async {
        guard let url = URL(string: publicAPI + "/api/v1/" + endpoint), url.scheme == "https" else {
            status = "Cần cấu hình máy chủ ERI"; return
        }
        do {
            let (data, response) = try await URLSession.shared.data(from: url)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.badServerResponse) }
            items = try JSONDecoder().decode(StoreResponse.self, from: data).items
            status = items.isEmpty ? "Chưa có sản phẩm được xác minh" : ""
        } catch { status = "Không tải được danh mục: \(error.localizedDescription)" }
    }
}

private enum PurchaseError: LocalizedError {
    case server(String)
    var errorDescription: String? {
        switch self { case .server(let message): return message }
    }
}
private struct CheckoutOrder: Decodable, Identifiable {
    let id: String
    let product_name: String
    let amount_vnd: Int
    let status: String
    let payment_method: String?
    let created_at: String?
    let kind: String?
    let payment_instructions: String?
    let transfer_note: String?
    let checkout_url: String?
    let qr_url: String?
    let payment_setup_required: Bool?
}
private struct OrderDetailView: View {
    let order: CheckoutOrder
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            Form {
                Section("Đơn hàng đã tạo") {
                    Text(order.product_name).font(.headline)
                    LabeledContent("Mã đơn", value: order.id)
                    LabeledContent("Số tiền", value: eriMoney(order.amount_vnd))
                    LabeledContent("Trạng thái", value: order.status)
                }
                if order.kind == "ai" || order.kind == "certificate" {
                    Section("Nhận sản phẩm") {
                        Text("Sản phẩm được giao thủ công sau khi Admin xác nhận thanh toán. Dùng mã đơn để liên hệ nhận hàng.")
                            .font(.footnote)
                    }
                }
                Section("Thanh toán") {
                    if let path = order.qr_url,
                       let url = URL(string: path.hasPrefix("https://") ? path : publicAPI + path),
                       url.scheme == "https" {
                        VStack(spacing: 12) {
                            Text("Quét mã QR MoMo").font(.headline)
                            AsyncImage(url: url) { phase in
                                switch phase {
                                case .success(let image):
                                    image.resizable().interpolation(.none)
                                        .scaledToFit().frame(maxWidth: 300, maxHeight: 300)
                                case .failure:
                                    Text("Không tải được ảnh QR. Vui lòng thử lại.")
                                        .foregroundStyle(.red)
                                default:
                                    ProgressView().frame(height: 220)
                                }
                            }
                            Text("Mã QR là ảnh tài khoản nhận tiền, không tự điền số tiền hoặc mã đơn.")
                                .font(.footnote).foregroundStyle(.secondary)
                        }.frame(maxWidth: .infinity)
                    } else if order.payment_setup_required == true && order.checkout_url == nil {
                        Text("Chủ cửa hàng chưa liên kết QR MoMo. Vui lòng liên hệ người bán trước khi thanh toán.")
                            .foregroundStyle(.red)
                    }
                    if let instructions = order.payment_instructions {
                        Text(instructions)
                    }
                    if let note = order.transfer_note {
                        LabeledContent("Nội dung chuyển", value: note)
                            .textSelection(.enabled)
                    }
                    if let link = order.checkout_url, let url = URL(string: link),
                       url.scheme == "https" {
                        Link("Đi đến trang thanh toán", destination: url)
                    } else {
                        Text("Chuyển khoản theo hướng dẫn của người bán. Đơn chỉ được xác nhận sau khi người bán kiểm tra giao dịch.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                }
            }
            .navigationTitle("Đặt hàng")
            .toolbar { ToolbarItem(placement: .topBarTrailing) {
                Button("Đóng") { dismiss() }
            }}
        }
    }
}
private struct ModItem: Decodable, Identifiable {
    let id: Int
    let name: String
    let description: String?
    let version: String
    let bundle_id: String
    let download_url: String?
    let sha256: String?
    let price_vnd: Int
}
private struct ModResponse: Decodable { let items: [ModItem] }
private struct ModCatalog: View {
    @EnvironmentObject private var session: BuyerSession
    @Binding var showAuth: Bool
    @State private var items: [ModItem] = []
    @State private var status = ""
    @State private var buying = false
    @State private var checkout: CheckoutOrder?
    var body: some View {
        NavigationStack {
            List {
                if items.isEmpty { Text(status).foregroundStyle(.secondary) }
                ForEach(items) { item in
                    modRow(item)
                }
            }
            .navigationTitle("Mod Game")
            .toolbar { Button("Làm mới") { Task { await load() } } }
            .sheet(item: $checkout) { order in OrderDetailView(order: order) }
            .task { await load() }
        }
    }
    private func modRow(_ item: ModItem) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(item.name).font(.headline)
            if let detail = item.description, !detail.isEmpty { Text(detail).font(.subheadline) }
            Text("Phiên bản " + item.version)
            Text(item.bundle_id).font(.caption).foregroundStyle(.secondary)
            Text(eriMoney(item.price_vnd)).fontWeight(.semibold)
            Button(buying ? "Đang tạo đơn..." : "Mua ngay") {
                Task { await buy(item) }
            }
            .buttonStyle(.borderedProminent)
            .disabled(buying)
        }
        .padding(.vertical, 6)
    }
    @MainActor private func buy(_ item: ModItem) async {
        guard session.buyer != nil else {
            showAuth = true
            return
        }
        buying = true
        defer { buying = false }
        do {
            checkout = try await session.buy(kind: "mod", productID: item.id)
        } catch { status = "Không tạo được đơn: " + error.localizedDescription }
    }
    @MainActor private func load() async {
        guard let url = URL(string: publicAPI + "/api/v1/mods"), url.scheme == "https" else {
            status = "Cần cấu hình máy chủ ERI"; return
        }
        do {
            let (data, response) = try await URLSession.shared.data(from: url)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.badServerResponse) }
            items = try JSONDecoder().decode(ModResponse.self, from: data).items
            status = items.isEmpty ? "Chưa có IPA được phép phân phối" : ""
        } catch { status = "Không tải được IPA: " + error.localizedDescription }
    }
}
