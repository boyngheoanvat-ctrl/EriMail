import SwiftUI

private func eriMoney(_ amount: Int) -> String {
    let formatter = NumberFormatter()
    formatter.numberStyle = .decimal
    formatter.locale = Locale(identifier: "vi_VN")
    formatter.maximumFractionDigits = 0
    return (formatter.string(from: NSNumber(value: amount)) ?? String(amount)) + "đ"
}

import Security
import UniformTypeIdentifiers

private let server = "https://eri-api-dag8.onrender.com"

@main
struct ERIAdminApp: App {
    var body: some Scene { WindowGroup { AdminHome().frame(maxWidth: .infinity, maxHeight: .infinity) } }
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
    let created_at: String?
    let buyer_username: String?
    let buyer_name: String?
    let buyer_contact: String?
    let delivery_filename: String?
    let certificate_udid: String?
    let certificate_udid_source: String?
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
    // Preserve FastAPI's validation details; "HTTP 422" alone is not actionable.
    private func serverDetail(_ body: Data) -> String {
        guard let obj = try? JSONSerialization.jsonObject(with: body) as? [String: Any],
              let detail = obj["detail"] else {
            return ""
        }
        if let message = detail as? String {
            return String(message.prefix(400))
        }
        if let errors = detail as? [[String: Any]] {
            let descriptions = errors.prefix(3).compactMap { issue -> String? in
                let path = (issue["loc"] as? [Any])?.last.map { String(describing: $0) } ?? "thông tin"
                let fieldLabels = ["price_vnd": "Giá bán", "authorized": "Quyền phân phối",
                                   "name": "Tên sản phẩm", "filename": "Tên tệp",
                                   "size_bytes": "Kích thước tệp", "kind": "Loại sản phẩm",
                                   "description": "Mô tả", "issuer": "Nhà phát hành"]
                let label = fieldLabels[path] ?? path
                let msg = String((issue["msg"] as? String ?? "Không hợp lệ").prefix(140))
                if path == "price_vnd" && msg.lowercased().contains("greater than 0") {
                    return "Máy chủ chỉ chấp nhận giá lớn hơn 0đ (chưa hỗ trợ FREE)"
                }
                return label + ": " + msg
            }
            return descriptions.joined(separator: "; ")
        }
        return ""
    }

    func send(_ path: String, method: String = "GET", data: Data? = nil) async throws -> Data {
        let (body, response) = try await URLSession.shared.data(for: request(path, method: method, data: data))
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200...299).contains(status) else {
            if status == 401 || status == 403 { throw AdminError.invalidKey }
            let detail = serverDetail(body)
            if !detail.isEmpty { throw AdminError.httpDetail(status, detail) }
            throw AdminError.http(status)
        }
        return body
    }

    func ensureFreeModSupported() async throws {
        // The live API may still reject price_vnd=0 even when the app UI has FREE.
        // Fail BEFORE starting a 320MB+ upload and never silently turn FREE into paid.
        let schemaData = try await send("/openapi.json")
        guard let schema = try? JSONSerialization.jsonObject(with: schemaData) as? [String: Any],
              let components = schema["components"] as? [String: Any],
              let schemas = components["schemas"] as? [String: Any],
              let upload = schemas["EriChunkStart"] as? [String: Any],
              let properties = upload["properties"] as? [String: Any],
              let price = properties["price_vnd"] as? [String: Any],
              let minimum = price["minimum"] as? NSNumber,
              minimum.doubleValue == 0,
              price["exclusiveMinimum"] == nil,
              let routes = schema["paths"] as? [String: Any],
              routes["/api/v1/mods/{product_id}/download"] != nil else {
            throw AdminError.freeNotSupported
        }
    }


    private struct OrderDeliveryBegin: Decodable {
        let upload_id: String
        let size_bytes: Int
        let chunk_bytes: Int
    }
    private struct OrderDeliveryOffset: Decodable { let offset: Int }

    func deliverFile(orderID: String, file: URL,
                     onProgress: @escaping @MainActor (Double) -> Void) async throws {
        let attrs = try FileManager.default.attributesOfItem(atPath: file.path)
        guard let size = (attrs[.size] as? NSNumber)?.intValue,
              size > 0 && size <= 300 * 1024 * 1024 else {
            throw URLError(.dataLengthExceedsMaximum)
        }
        let base = "/api/v1/admin/orders/" + orderID + "/delivery-sessions"
        let payload = try JSONSerialization.data(withJSONObject: [
            "filename": file.lastPathComponent, "size_bytes": size
        ])
        let started = try await send(base, method: "POST", data: payload)
        let session = try JSONDecoder().decode(OrderDeliveryBegin.self, from: started)
        guard session.size_bytes == size, session.chunk_bytes > 0,
              session.chunk_bytes <= 4 * 1024 * 1024 else {
            throw URLError(.badServerResponse)
        }
        let path = base + "/" + session.upload_id
        let reader = try FileHandle(forReadingFrom: file)
        defer { try? reader.close() }
        var offset = 0
        while offset < size {
            let count = min(session.chunk_bytes, size - offset)
            try reader.seek(toOffset: UInt64(offset))
            guard let bytes = try reader.read(upToCount: count), bytes.count == count else {
                throw URLError(.cannotDecodeRawData)
            }
            var attempt = 0
            while true {
                do {
                    var req = try request(path, method: "PUT")
                    req.timeoutInterval = 120
                    req.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
                    req.setValue(String(offset), forHTTPHeaderField: "X-ERI-Upload-Offset")
                    let (reply, response) = try await URLSession.shared.upload(for: req, from: bytes)
                    let status = (response as? HTTPURLResponse)?.statusCode ?? 0
                    guard (200...299).contains(status) else { throw AdminError.http(status) }
                    let received = try JSONDecoder().decode(OrderDeliveryOffset.self, from: reply)
                    guard received.offset == offset + count else { throw URLError(.badServerResponse) }
                    offset = received.offset
                    onProgress(Double(offset) / Double(size))
                    break
                } catch {
                    attempt += 1
                    guard attempt <= 3 else { throw error }
                    let data = try await send(path)
                    let received = try JSONDecoder().decode(OrderDeliveryOffset.self, from: data)
                    if received.offset == offset + count {
                        offset = received.offset
                        onProgress(Double(offset) / Double(size))
                        break
                    }
                    guard received.offset == offset else { throw URLError(.badServerResponse) }
                }
            }
        }
        _ = try await send(path + "/complete", method: "POST")
        onProgress(1)
    }

    private struct UploadBegin: Decodable {
        let upload_id: String
        let size_bytes: Int
        let chunk_bytes: Int
        let offset: Int
    }
    private struct UploadPosition: Decodable { let offset: Int }

    // Send 4MB chunks instead of a 200+ MB multipart body.
    // When a request drops, read the server's acknowledged offset and resume.
    func uploadFile(kind: String, file: URL, fields: [(String, String)], maxBytes: Int,
                    onProgress: @escaping @MainActor (Double) -> Void) async throws {
        let attributes = try FileManager.default.attributesOfItem(atPath: file.path)
        guard let number = attributes[.size] as? NSNumber else { throw URLError(.fileDoesNotExist) }
        let size = number.intValue
        guard size > 0 && size <= maxBytes else { throw URLError(.dataLengthExceedsMaximum) }
        let values = Dictionary(fields, uniquingKeysWith: { _, newer in newer })
        guard let price = Int(values["price_vnd"] ?? ""),
              price >= 0, (kind == "ipa" || price > 0) else {
            throw URLError(.badURL)
        }
        if kind == "ipa" && price == 0 {
            try await ensureFreeModSupported()
        }
        let payload: [String: Any] = [
            "kind": kind,
            "filename": file.lastPathComponent,
            "size_bytes": size,
            "name": values["name"] ?? "",
            "description": values["description"] ?? "",
            "price_vnd": price,
            "issuer": values["issuer"] ?? "",
            "authorized": values["authorized"] == "true"
        ]
        let body = try JSONSerialization.data(withJSONObject: payload)
        let fileInfo = try FileManager.default.attributesOfItem(atPath: file.path)
        let modified = (fileInfo[.modificationDate] as? Date)?.timeIntervalSince1970 ?? 0
        let checkpointSeed = kind + "|" + file.path + "|" + String(size) + "|" +
            String(modified) + "|" + (values["name"] ?? "") + "|" + String(price)
        let checkpointKey = "eri.upload.resume." +
            checkpointSeed.data(using: .utf8)!.base64EncodedString()
        var session: UploadBegin?
        if let savedID = UserDefaults.standard.string(forKey: checkpointKey),
           savedID.range(of: "^[a-f0-9]{32}$", options: .regularExpression) != nil {
            let endpoint = "/api/v1/admin/upload-sessions/" + savedID
            if let data = try? await send(endpoint),
               let server = try? JSONDecoder().decode(UploadPosition.self, from: data),
               server.offset >= 0 && server.offset <= size {
                session = UploadBegin(upload_id: savedID,
                                      size_bytes: size,
                                      chunk_bytes: 4 * 1024 * 1024,
                                      offset: server.offset)
            } else {
                UserDefaults.standard.removeObject(forKey: checkpointKey)
            }
        }
        if session == nil {
            let startData = try await send("/api/v1/admin/upload-sessions", method: "POST", data: body)
            session = try JSONDecoder().decode(UploadBegin.self, from: startData)
            if let started = session {
                UserDefaults.standard.set(started.upload_id, forKey: checkpointKey)
            }
        }
        guard let active = session, active.size_bytes == size, active.chunk_bytes > 0,
              active.chunk_bytes <= 4 * 1024 * 1024 else { throw URLError(.badServerResponse) }

        let reader = try FileHandle(forReadingFrom: file)
        defer { try? reader.close() }
        var offset = active.offset
        let path = "/api/v1/admin/upload-sessions/" + active.upload_id
        onProgress(Double(offset) / Double(size))
        while offset < size {
            try Task.checkCancellation()
            try reader.seek(toOffset: UInt64(offset))
            let count = min(active.chunk_bytes, size - offset)
            guard let chunk = try reader.read(upToCount: count), chunk.count == count else {
                throw URLError(.cannotOpenFile)
            }
            var sent = false
            var lastError: Error = URLError(.networkConnectionLost)
            for attempt in 0..<4 {
                do {
                    var req = try request(path, method: "PUT")
                    req.setValue(String(offset), forHTTPHeaderField: "X-ERI-Upload-Offset")
                    req.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
                    req.timeoutInterval = 90
                    let (result, response) = try await URLSession.shared.upload(for: req, from: chunk)
                    let http = (response as? HTTPURLResponse)?.statusCode ?? 0
                    guard (200...299).contains(http) else { throw AdminError.http(http) }
                    let next = try JSONDecoder().decode(UploadPosition.self, from: result).offset
                    guard next > offset && next <= size else { throw URLError(.badServerResponse) }
                    offset = next
                    onProgress(Double(offset) / Double(size))
                    sent = true
                    break
                } catch {
                    lastError = error
                    // A lost response does not mean the server missed the chunk.
                    // Always ask its committed offset before re-sending.
                    if let data = try? await send(path),
                       let position = try? JSONDecoder().decode(UploadPosition.self, from: data),
                       position.offset > offset && position.offset <= size {
                        offset = position.offset
                        onProgress(Double(offset) / Double(size))
                        sent = true
                        break
                    }
                    if attempt < 3 {
                        try? await Task.sleep(nanoseconds: UInt64(attempt + 1) * 700_000_000)
                    }
                }
            }
            if !sent { throw lastError }
        }
        _ = try await send(path + "/complete", method: "POST", data: Data())
        UserDefaults.standard.removeObject(forKey: checkpointKey)
        onProgress(1.0)
    }


    func uploadMoMoQR(file: URL) async throws {
        let bytes = try Data(contentsOf: file, options: .mappedIfSafe)
        guard !bytes.isEmpty && bytes.count <= 2 * 1024 * 1024 else {
            throw URLError(.dataLengthExceedsMaximum)
        }
        let boundary = "ERIQR-" + UUID().uuidString
        var body = Data()
        body.append(Data(("--" + boundary + "\r\nContent-Disposition: form-data; name=\"qr\"; filename=\"momo-qr.png\"\r\nContent-Type: application/octet-stream\r\n\r\n").utf8))
        body.append(bytes)
        body.append(Data(("\r\n--" + boundary + "--\r\n").utf8))
        var req = try request("/api/v1/admin/payment/momo", method: "POST")
        req.setValue("multipart/form-data; boundary=" + boundary, forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 45
        let (_, resp) = try await URLSession.shared.upload(for: req, from: body)
        let status = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard (200...299).contains(status) else { throw AdminError.http(status) }
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
    case httpDetail(Int, String)
    case freeNotSupported
    var errorDescription: String? {
        switch self {
        case .invalidKey: return "Khóa quản trị không đúng hoặc đã hết hiệu lực."
        case .freeNotSupported:
            return "API trên Render chưa hỗ trợ Mod Game FREE. Không thể đăng miễn phí khi giá là 0đ. Hãy dùng MUA với giá lớn hơn 0đ hoặc nâng backend sau khi bảo toàn dữ liệu."
        case .httpDetail(let status, let detail):
            return "HTTP \(status): " + detail
        case .http(let status):
            if status == 404 {
                return "HTTP 404: Máy chủ chưa có tính năng này. Hãy cập nhật backend trên Render."
            }
            if status == 413 {
                return "HTTP 413: Dữ liệu tải lên vượt giới hạn của máy chủ."
            }
            return "Máy chủ trả về HTTP \(status)."
        }
    }
}

private struct AdminHome: View {
    @StateObject private var session = AdminSession()
    @State private var showLogin = false
    var body: some View {
        NavigationStack {
            List {
                Section("IPA Mod Game") {
                    NavigationLink {
                        AdminUploadView(kind: .ipa).environmentObject(session)
                    } label: { Label("Nhập tệp IPA", systemImage: "square.and.arrow.up") }
                }
                Section("Đăng sản phẩm — giao sau khi khách mua") {
                    NavigationLink {
                        ManualListingView(kind: .certificate).environmentObject(session)
                    } label: { Label("Đăng bán chứng chỉ", systemImage: "checkmark.seal") }
                    NavigationLink {
                        ManualListingView(kind: .ai).environmentObject(session)
                    } label: { Label("Đăng bán AI Plus", systemImage: "sparkles") }
                }
                Section("Thanh toán MoMo") {
                    NavigationLink {
                        MoMoLinkView().environmentObject(session)
                    } label: {
                        Label("Liên kết MoMo", systemImage: "qrcode")
                    }
                }
                Section("Quản lý sản phẩm đã đăng") {
                    NavigationLink { AdminCatalogView(kind: .ipa).environmentObject(session) } label: {
                        Label("Xóa IPA Mod Game", systemImage: "trash")
                    }
                    NavigationLink { AdminCatalogView(kind: .certificate).environmentObject(session) } label: {
                        Label("Xóa chứng chỉ", systemImage: "trash")
                    }
                    NavigationLink { AdminCatalogView(kind: .ai).environmentObject(session) } label: {
                        Label("Xóa AI Plus", systemImage: "trash")
                    }
                }
                Section("Quản lý tài khoản khách hàng") {
                    NavigationLink {
                        CustomerManagementView().environmentObject(session)
                    } label: {
                        Label("Danh sách và xóa tài khoản", systemImage: "person.crop.circle.badge.xmark")
                    }
                }
                Section("Thiết bị đăng ký Chứng chỉ") {
                    NavigationLink {
                        CertificateUDIDListView().environmentObject(session)
                    } label: {
                        Label("UDID khách đã gửi", systemImage: "iphone")
                    }
                }
                Section("Lịch sử khách đã đặt mua") {
                    NavigationLink {
                        OrdersView(kind: "all").environmentObject(session)
                    } label: { Label("Tất cả đơn hàng / Khách mua", systemImage: "person.2") }
                }
                Section("Xử lý đơn hàng") {
                    NavigationLink { OrdersView(kind: "mod").environmentObject(session) } label: {
                        Label("Đơn Mod Game", systemImage: "gamecontroller")
                    }
                    NavigationLink { OrdersView(kind: "certificate").environmentObject(session) } label: {
                        Label("Đơn chứng chỉ", systemImage: "doc.text")
                    }
                    NavigationLink { OrdersView(kind: "ai").environmentObject(session) } label: {
                        Label("Đơn AI Plus", systemImage: "cart")
                    }
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


private struct ManagedCustomer: Decodable, Identifiable {
    let id: Int
    let username: String
    let display_name: String
    let contact: String
    let created_at: String
    let order_count: Int
    let pending_count: Int
    let paid_count: Int
    let fulfilled_count: Int

}

private struct ManagedCustomerEnvelope: Decodable {
    let items: [ManagedCustomer]
}


private struct CustomerListRow: View {
    let customer: ManagedCustomer
    let busy: Bool
    let onDelete: () -> Void

    private var orderDetails: String {
        "Tổng đơn: \(customer.order_count) · Chờ tiền: \(customer.pending_count) · " +
        "Chờ giao: \(customer.paid_count) · Đã giao: \(customer.fulfilled_count)"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(customer.display_name).font(.headline)
            Text("@" + customer.username)
                .font(.subheadline).foregroundStyle(.secondary)
            Text("Liên hệ: " + customer.contact)
                .textSelection(.enabled)
            Text(orderDetails)
                .font(.footnote).foregroundStyle(.secondary)
            Text("Ngày đăng ký: " + customer.created_at)
                .font(.caption).foregroundStyle(.secondary)
            Button("Xóa tài khoản", role: .destructive, action: onDelete)
                .disabled(busy)
        }
        .padding(.vertical, 6)
    }
}

private struct CustomerManagementView: View {
    @EnvironmentObject private var session: AdminSession
    @State private var customers: [ManagedCustomer] = []
    @State private var selected: ManagedCustomer?
    @State private var busy = false
    @State private var status = ""
    @State private var searchText = ""

    private var shown: [ManagedCustomer] {
        let keyword = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !keyword.isEmpty else { return customers }
        return customers.filter {
            $0.username.localizedCaseInsensitiveContains(keyword) ||
            $0.display_name.localizedCaseInsensitiveContains(keyword) ||
            $0.contact.localizedCaseInsensitiveContains(keyword)
        }
    }

    var body: some View {
        List {
            Section {
                Text("Xóa vĩnh viễn tài khoản được chọn, toàn bộ đơn hàng, phiên đăng nhập và file đã giao.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            if !status.isEmpty {
                Section {
                    Text(status).font(.footnote).foregroundStyle(.secondary)
                }
            }
            Section("Khách hàng (" + String(customers.count) + ")") {
                if shown.isEmpty {
                    Text(customers.isEmpty ? "Chưa có khách đăng ký." : "Không tìm thấy tài khoản.")
                        .foregroundStyle(.secondary)
                }
                ForEach(shown) { customer in
                    CustomerListRow(customer: customer, busy: busy) {
                        selected = customer
                    }
                }
            }
        }
        .searchable(text: $searchText, prompt: "Tên, tài khoản hoặc liên hệ")
        .navigationTitle("Tài khoản khách")
        .toolbar {
            Button("Làm mới") { Task { await reload() } }
                .disabled(busy)
        }
        .task { await reload() }
        .sheet(item: $selected) { customer in
            CustomerPurgeConfirmationView(customer: customer) {
                selected = nil
                Task { await delete(customer) }
            }
        }
    }

    private func reload() async {
        do {
            customers = try JSONDecoder().decode(
                ManagedCustomerEnvelope.self,
                from: await session.send("/api/v1/admin/customers")
            ).items
            status = ""
        } catch {
            status = "Không tải được khách hàng: " + error.localizedDescription
        }
    }

    private func delete(_ customer: ManagedCustomer) async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        do {
            let data = try JSONSerialization.data(withJSONObject: [
                "confirm_username": customer.username
            ])
            let result = try await session.send(
                "/api/v1/admin/customers/" + String(customer.id),
                method: "DELETE", data: data
            )
            let receipt = try JSONDecoder().decode(CustomerPurgeReceipt.self, from: result)
            await reload()
            status = "Đã xóa sạch @" + customer.username +
                     " và " + String(receipt.deleted_orders) + " đơn hàng. " +
                     "Đã xóa " + String(receipt.deleted_delivery_files) + " file."
        } catch {
            status = "Không xóa được @" + customer.username +
                     ": " + error.localizedDescription
            await reload()
        }
    }
}


private struct CustomerPurgeReceipt: Decodable {
    let deleted: Bool
    let deleted_orders: Int
    let deleted_delivery_files: Int
}

private struct CustomerPurgeConfirmationView: View {
    @Environment(\.dismiss) private var dismiss
    let customer: ManagedCustomer
    let onDelete: () -> Void
    @State private var typedUsername = ""

    private var verified: Bool {
        typedUsername.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
            == customer.username.lowercased()
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Xóa sạch dữ liệu khách hàng") {
                    Text(customer.display_name).font(.headline)
                    Text("Tài khoản: @" + customer.username)
                    Text("Sẽ xóa " + String(customer.order_count) +
                         " đơn hàng và toàn bộ file đã giao trong các đơn đó.")
                        .foregroundStyle(.red)
                    Text("Mọi phiên đăng nhập bị thu hồi. Khách không thể tải các file cũ hoặc xem lịch sử mua hàng nữa.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Section("Xác nhận xóa vĩnh viễn") {
                    Text("Nhập chính xác tên tài khoản @" + customer.username +
                         " để xác nhận.")
                        .font(.footnote)
                    TextField("Tên tài khoản cần xóa", text: $typedUsername)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    Button("Xóa sạch tài khoản và toàn bộ dữ liệu", role: .destructive) {
                        guard verified else { return }
                        onDelete()
                        dismiss()
                    }
                    .disabled(!verified)
                }
                Section {
                    Text("Chỉ xóa dữ liệu của tài khoản được chọn trong ERI STORE. Bản sao ngoài máy chủ, nhật ký hạ tầng hoặc dữ liệu bên thứ ba không bị xóa bởi chức năng này.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Xác nhận xóa tài khoản")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Hủy") { dismiss() }
                }
            }
        }
    }
}


private struct CertificateUDIDRecord: Decodable, Identifiable {
    let order_id: String
    let udid: String
    let source: String
    let submitted_at: String
    let product_name: String
    let amount_vnd: Int
    let order_status: String
    let buyer_username: String?
    let buyer_name: String?
    let buyer_contact: String?
    var id: String { order_id }
}

private struct CertificateUDIDListView: View {
    @EnvironmentObject private var session: AdminSession
    @State private var records: [CertificateUDIDRecord] = []
    @State private var notice = ""

    var body: some View {
        List {
            Section {
                Text("UDID gắn với đơn Chứng chỉ của khách đã đăng nhập.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            if !notice.isEmpty {
                Text(notice).foregroundStyle(.secondary)
            }
            if records.isEmpty {
                Text("Chưa có UDID thiết bị nào được gửi.")
                    .foregroundStyle(.secondary)
            }
            ForEach(records) { item in
                CertificateUDIDRow(item: item)
            }
        }
        .navigationTitle("UDID khách mua")
        .toolbar {
            Button("Làm mới") { Task { await reload() } }
        }
        .task { await reload() }
    }

    private func reload() async {
        do {
            let data = try await session.send("/api/v1/admin/certificate-udids")
            records = try JSONDecoder().decode(Envelope<CertificateUDIDRecord>.self, from: data).items
            notice = ""
        } catch {
            notice = "Không tải được UDID: " + error.localizedDescription
        }
    }
}

private struct CertificateUDIDRow: View {
    let item: CertificateUDIDRecord
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(item.product_name).font(.headline)
            Text("Khách: " + (item.buyer_name ?? "Không có tên") +
                 " (@" + (item.buyer_username ?? "đã xóa") + ")")
            if let contact = item.buyer_contact {
                Text("Liên hệ: " + contact).textSelection(.enabled)
            }
            Text("Mã đơn: " + item.order_id).textSelection(.enabled)
            Text("UDID: " + item.udid).font(.subheadline).fontWeight(.semibold)
                .textSelection(.enabled)
            Text(item.source == "apple_profile"
                 ? "Hồ sơ Apple: chữ ký đã xác minh"
                 : "Khách tự nhập: chưa xác minh")
                .font(.footnote).foregroundStyle(.secondary)
            Text("Thời gian: " + item.submitted_at)
                .font(.caption).foregroundStyle(.secondary)
        }
        .padding(.vertical, 6)
    }
}

private struct OrdersView: View {
    @EnvironmentObject private var session: AdminSession
    let kind: String
    @State private var orders: [Order] = []
    @State private var references: [String: String] = [:]
    @State private var status = ""
    @State private var toDeliver: Order?
    @State private var fileDeliveryOrder: Order?
    @State private var updatingOrderID: String?

    private func referenceFor(_ order: Order) -> String {
        (references[order.id] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func validReference(_ order: Order) -> Bool {
        referenceFor(order).range(
            of: "^[A-Za-z0-9_-]{6,128}$", options: .regularExpression
        ) != nil
    }

    private var visible: [Order] {
        kind == "all" ? orders : orders.filter { $0.kind == kind }
    }
    private func productCategory(_ type: String) -> String {
        switch type {
        case "mod": return "Mod Game"
        case "ai": return "AI Plus"
        case "certificate": return "Chứng chỉ"
        default: return type
        }
    }
    private func readableStatus(_ value: String) -> String {
        switch value {
        case "pending": return "Chờ xác nhận thanh toán"
        case "paid": return "Đã thanh toán · Chưa giao"
        case "fulfilled": return "Đã giao hàng"
        case "cancelled": return "Đã hủy"
        default: return value
        }
    }

    var body: some View {
        List {
            Section {
                Text("Đơn hàng gắn với tài khoản khách. Kiểm tra giao dịch MoMo thật trước khi xác nhận nhận tiền; chỉ đánh dấu Đã giao khi đã giao cho khách.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            if !status.isEmpty { Text(status).foregroundStyle(.secondary) }
            ForEach(visible) { order in
                VStack(alignment: .leading, spacing: 7) {
                    Text(order.product_name).font(.headline)
                    Text("Loại: " + productCategory(order.kind))
                    Text("Khách: " + (order.buyer_name ?? "Đơn cũ chưa có tài khoản"))
                        .fontWeight(.semibold)
                    if let username = order.buyer_username {
                        Text("Tài khoản: @" + username)
                    }
                    if let contact = order.buyer_contact, !contact.isEmpty {
                        Text("Liên hệ giao hàng: " + contact)
                            .textSelection(.enabled)
                    }
                    Text("Mã đơn: " + order.id).textSelection(.enabled)
                    if order.kind == "certificate", let udid = order.certificate_udid {
                        Text("UDID thiết bị: " + udid)
                            .fontWeight(.semibold).textSelection(.enabled)
                        Text(order.certificate_udid_source == "apple_profile"
                             ? "Từ hồ sơ Apple đã xác minh chữ ký"
                             : "Khách tự nhập — chưa xác minh")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    if let created = order.created_at {
                        Text("Đặt lúc: " + created).font(.caption).foregroundStyle(.secondary)
                    }
                    Text("Giá: " + eriMoney(order.amount_vnd))
                    Text(readableStatus(order.status))
                        .fontWeight(.medium)
                    if let delivered = order.delivery_filename {
                        Label("Đã gửi file: " + delivered, systemImage: "paperclip")
                            .font(.footnote).foregroundStyle(.green)
                    }
                    if order.status == "pending" {
                        TextField("Mã giao dịch MoMo đã đối chiếu", text: Binding(
                            get: { references[order.id] ?? "" },
                            set: { references[order.id] = $0 }
                        ))
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        if !validReference(order) {
                            Text("Nhập mã giao dịch MoMo hợp lệ (6–128 ký tự) sau khi đã kiểm tra tiền để bật nút Đã giao.")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Button("Xác nhận đã nhận tiền") {
                            Task { await update(order, "paid") }
                        }
                        .disabled(!validReference(order) || updatingOrderID != nil)
                        Button("Hủy đơn", role: .destructive) {
                            Task { await update(order, "cancelled") }
                        }
                        .disabled(updatingOrderID != nil)
                    } else if order.status == "paid" {
                        Button {
                            fileDeliveryOrder = order
                        } label: {
                            Label("Giao file trực tiếp cho khách", systemImage: "square.and.arrow.up.fill")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(updatingOrderID != nil)
                        Text("Hoặc xác nhận giao hàng bên ngoài sau khi đã gửi bằng cách khác.")
                            .font(.footnote).foregroundStyle(.secondary)
                        Button {
                            toDeliver = order
                        } label: {
                            Label("Đã giao bên ngoài", systemImage: "checkmark.seal.fill")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(.green)
                        .disabled(updatingOrderID != nil)
                    }
                }
                .padding(.vertical, 7)
            }
        }
        .navigationTitle(kind == "all" ? "Lịch sử khách mua" : "Đơn " + productCategory(kind))
        .toolbar { Button("Làm mới") { Task { await refresh() } } }
        .confirmationDialog(
            "Xác nhận đã giao bên ngoài website?",
            isPresented: Binding(
                get: { toDeliver != nil },
                set: { if !$0 { toDeliver = nil } }
            ),
            titleVisibility: .visible
        ) {
            Button("Xác nhận Đã giao") {
                guard let order = toDeliver else { return }
                toDeliver = nil
                Task { await markDelivered(order) }
            }
            Button("Chưa giao", role: .cancel) { toDeliver = nil }
        } message: {
            Text("Chỉ xác nhận khi bạn đã tự giao sản phẩm bên ngoài. Để khách nhận file trực tiếp trên ERI STORE, dùng nút Giao file trực tiếp.")
        }
        .sheet(item: $fileDeliveryOrder) { order in
            OrderFileDeliveryView(order: order, onDelivered: {
                Task { await refresh() }
            }).environmentObject(session)
        }
        .task { await refresh() }
    }

    private func refresh() async {
        do {
            orders = try JSONDecoder().decode(
                Envelope<Order>.self,
                from: await session.send("/api/v1/admin/orders")
            ).items
            status = visible.isEmpty ? "Chưa có đơn hàng." : ""
        } catch {
            status = "Không tải được đơn: " + error.localizedDescription
        }
    }

    private func setServerStatus(_ order: Order, to next: String) async throws {
        var payload = ["status": next]
        if next == "paid" {
            payload["transaction_reference"] = referenceFor(order)
        }
        let body = try JSONEncoder().encode(payload)
        _ = try await session.send("/api/v1/admin/orders/" + order.id + "/status",
                                   method: "POST", data: body)
    }

    private func update(_ order: Order, _ newStatus: String) async {
        guard updatingOrderID == nil else { return }
        updatingOrderID = order.id
        defer { updatingOrderID = nil }
        do {
            try await setServerStatus(order, to: newStatus)
            await refresh()
            status = "Đơn " + order.id + ": " + readableStatus(newStatus)
        } catch {
            status = "Không cập nhật được đơn: " + error.localizedDescription
        }
    }

    private func markDelivered(_ order: Order) async {
        guard updatingOrderID == nil, order.status == "paid" else {
            status = "Vui lòng xác nhận đã nhận tiền trước khi giao hàng."
            return
        }
        updatingOrderID = order.id
        defer { updatingOrderID = nil }
        do {
            try await setServerStatus(order, to: "fulfilled")
            await refresh()
            status = "Đơn " + order.id + ": Đã giao bên ngoài ERI STORE"
        } catch {
            status = "Không cập nhật được: " + error.localizedDescription
        }
    }

}


private struct OrderFileDeliveryView: View {
    @EnvironmentObject private var session: AdminSession
    @Environment(\.dismiss) private var dismiss
    let order: Order
    let onDelivered: () -> Void
    @State private var picker = false
    @State private var fileURL: URL?
    @State private var working = false
    @State private var progress = 0.0
    @State private var message = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("Đúng đơn · Đúng khách") {
                    Text(order.product_name).font(.headline)
                    Text("Tên khách: " + (order.buyer_name ?? "Không có tên"))
                    if let username = order.buyer_username {
                        Text("Tài khoản: @" + username)
                    }
                    Text("Mã đơn: " + order.id).textSelection(.enabled)
                    Text("Đã thanh toán: " + eriMoney(order.amount_vnd))
                    Label("Chỉ chủ tài khoản đặt mua đơn này được tải file.",
                          systemImage: "lock.shield.fill")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Section("Giao file từ ứng dụng Tệp") {
                    Button("Chọn file cần giao") { picker = true }.disabled(working)
                    if let file = fileURL {
                        Text("Đã chọn: " + file.lastPathComponent)
                            .font(.footnote)
                    }
                    if working {
                        ProgressView(value: progress)
                        Text("Đã tải lên: " + String(Int(progress * 100)) + "%")
                            .font(.footnote)
                    }
                    Button(working ? "Đang gửi file..." : "Gửi file trực tiếp cho khách") {
                        Task { await sendFile() }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(fileURL == nil || working || order.status != "paid")
                    if !message.isEmpty {
                        Text(message).font(.footnote).foregroundStyle(.red)
                    }
                }
                Section {
                    Text("Chọn IPA, ZIP, P12, mobileprovision, PDF, TXT hoặc file hỗ trợ khác, tối đa 300 MB. Khi gửi đủ file, đơn chuyển sang Đã giao và khách sẽ thấy nút Tải file trong tài khoản.")
                        .font(.footnote).foregroundStyle(.secondary)
                    Text("Lưu ý: File hiện nằm trên ổ tạm Render Free, có thể mất khi server khởi động lại. Không lưu thông tin bí mật quan trọng khi chưa chuyển sang kho lưu trữ bền vững.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Giao hàng trực tiếp")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Đóng") { dismiss() }.disabled(working)
                }
            }
            .sheet(isPresented: $picker) {
                ERIDocumentPicker(onPick: { url in
                    fileURL = url
                    picker = false
                }, onCancel: { picker = false })
            }
        }
    }

    private func sendFile() async {
        guard !working, let source = fileURL else { return }
        working = true
        progress = 0
        message = ""
        defer { working = false }
        do {
            let imported = try await Task.detached(priority: .userInitiated) {
                try ImportedFile.copy(source, maxBytes: 300 * 1024 * 1024)
            }.value
            defer { try? FileManager.default.removeItem(at: imported.deletingLastPathComponent()) }
            try await session.deliverFile(orderID: order.id, file: imported) { value in
                progress = value
            }
            onDelivered()
            dismiss()
        } catch {
            message = "Giao hàng thất bại: " + error.localizedDescription +
                      ". Nếu vẫn là Đã thanh toán, hãy thử gửi lại."
        }
    }
}

private enum UploadKind: String {
    case ipa, certificate, ai
    var title: String {
        switch self {
        case .ipa: return "Cập nhật IPA Mod Game"
        case .certificate: return "Cập nhật chứng chỉ"
        case .ai: return "Cập nhật AI Plus"
        }
    }
    var accepted: [String] {
        switch self {
        case .ipa: return ["ipa"]
        case .certificate: return ["p12", "mobileprovision", "zip"]
        case .ai: return ["txt", "json", "csv", "zip"]
        }
    }
    // Use a broad system document picker; validate file extension after selection.
    var maxBytes: Int {
        switch self {
        case .ipa: return 512 * 1_048_576
        case .certificate: return 15 * 1_048_576
        case .ai: return 20 * 1_048_576
        }
    }
}

private enum ImportedFile {
    // The Files picker may return a security-scoped document-provider URL.
    // Copy it into our private sandbox before the picker access expires.
    static func copy(_ source: URL, maxBytes: Int) throws -> URL {
        let access = source.startAccessingSecurityScopedResource()
        defer { if access { source.stopAccessingSecurityScopedResource() } }
        let folder = FileManager.default.temporaryDirectory
            .appendingPathComponent("eri-import-" + UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let destination = folder.appendingPathComponent(source.lastPathComponent)
        var coordinationError: NSError?
        var copyingError: Error?
        let coordinator = NSFileCoordinator(filePresenter: nil)
        coordinator.coordinate(readingItemAt: source, options: [], error: &coordinationError) { readable in
            do {
                guard FileManager.default.createFile(atPath: destination.path, contents: nil) else {
                    throw URLError(.cannotCreateFile)
                }
                let input = try FileHandle(forReadingFrom: readable)
                defer { try? input.close() }
                let output = try FileHandle(forWritingTo: destination)
                defer { try? output.close() }
                var total = 0
                while let chunk = try input.read(upToCount: 1_048_576), !chunk.isEmpty {
                    total += chunk.count
                    guard total <= maxBytes else { throw URLError(.dataLengthExceedsMaximum) }
                    try output.write(contentsOf: chunk)
                }
                guard total > 0 else { throw URLError(.zeroByteResource) }
            } catch {
                copyingError = error
            }
        }
        if let issue = coordinationError ?? (copyingError as NSError?) {
            try? FileManager.default.removeItem(at: folder)
            throw issue
        }
        return destination
    }
}

private struct ERIDocumentPicker: UIViewControllerRepresentable {
    let onPick: (URL) -> Void
    let onCancel: () -> Void

    func makeUIViewController(context: Context) -> UIDocumentPickerViewController {
        let picker = UIDocumentPickerViewController(forOpeningContentTypes: [.item], asCopy: true)
        picker.allowsMultipleSelection = false
        picker.shouldShowFileExtensions = true
        picker.delegate = context.coordinator
        return picker
    }

    func updateUIViewController(_ controller: UIDocumentPickerViewController, context: Context) {}

    func makeCoordinator() -> Coordinator {
        Coordinator(onPick: onPick, onCancel: onCancel)
    }

    final class Coordinator: NSObject, UIDocumentPickerDelegate {
        let onPick: (URL) -> Void
        let onCancel: () -> Void

        init(onPick: @escaping (URL) -> Void, onCancel: @escaping () -> Void) {
            self.onPick = onPick
            self.onCancel = onCancel
        }

        func documentPicker(_ controller: UIDocumentPickerViewController,
                            didPickDocumentsAt urls: [URL]) {
            guard let file = urls.first else {
                onCancel()
                return
            }
            onPick(file)
        }

        func documentPickerWasCancelled(_ controller: UIDocumentPickerViewController) {
            onCancel()
        }
    }
}

private struct AdminUploadView: View {
    @EnvironmentObject private var session: AdminSession
    let kind: UploadKind
    @State private var name = ""
    @State private var issuer = ""
    @State private var description = ""
    @State private var price = ""
    @State private var modIsFree = false
    @State private var authorized = false
    @State private var selectedFile: URL?
    @State private var showImporter = false
    @State private var importing = false
    @State private var working = false
    @State private var status = ""
    @State private var progress = 0.0

    var body: some View {
        Form {
            Section("Thông tin sản phẩm") {
                TextField("Tên sản phẩm", text: $name)
                if kind == .ipa {
                    Picker("Loại Mod Game", selection: $modIsFree) {
                        Text("FREE").tag(true)
                        Text("BÁN").tag(false)
                    }
                    .pickerStyle(.segmented)
                    Toggle("Tôi có quyền phân phối IPA này", isOn: $authorized)
                    if modIsFree {
                        Text("Khách được tải trực tiếp IPA miễn phí, không tạo đơn thanh toán.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                }
                if kind == .certificate {
                    TextField("Nhà phát hành chứng chỉ", text: $issuer)
                }
                TextField("Mô tả sản phẩm", text: $description, axis: .vertical)
                    .lineLimit(2...5)
                if kind != .ipa || !modIsFree {
                    TextField(kind == .ipa ? "Giá bán (VND)" : "Giá vốn (VND)", text: $price)
                        .keyboardType(.numberPad)
                }
            }
            Section("Nhập tệp từ ứng dụng Tệp") {
                Button(importing ? "Đang đọc tệp..." : "Chọn tệp từ Files") { showImporter = true }
                    .disabled(working || importing)
                if let file = selectedFile {
                    Label("Đã chọn: " + file.lastPathComponent, systemImage: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                    if let attributes = try? FileManager.default.attributesOfItem(atPath: file.path),
                       let count = attributes[.size] as? NSNumber {
                        Text(ByteCountFormatter.string(fromByteCount: count.int64Value, countStyle: .file))
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                }
                if kind == .ipa {
                    Text("Phiên bản và Bundle ID được tự động đọc từ tệp IPA sau khi tải xong.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Text("Chọn tệp trong Files; hệ thống cho phép duyệt mọi định dạng và ứng dụng chỉ chấp nhận: " + kind.accepted.map { "." + $0 }.joined(separator: ", "))
                    .font(.footnote).foregroundStyle(.secondary)
                if working {
                    ProgressView(value: progress)
                    Text("Đã gửi " + Int(progress * 100).description + "% · Khi mất mạng, bấm lại để tiếp tục")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Button(working ? "Đang tải " + Int(progress * 100).description + "%" : "Cập nhật lên máy chủ") {
                    Task { await publish() }
                }
                .disabled(!valid || working || importing)
                if !status.isEmpty { Text(status).font(.footnote) }
            }
            Section {
                Text("Chỉ Admin được gửi tệp qua HTTPS. Tệp lưu riêng trên máy chủ; ứng dụng Store chỉ nhận tên và giá bán, không nhận tệp gốc hay khóa quản trị.")
                    .font(.footnote).foregroundStyle(.secondary)
                if kind == .certificate {
                    Text("Tệp .p12 có thể chứa khóa riêng. Chỉ sử dụng máy chủ do bạn kiểm soát, có lưu trữ an toàn và sao lưu.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
        }
        .navigationTitle(kind.title)
        .sheet(isPresented: $showImporter) {
            ERIDocumentPicker(
                onPick: { file in
                    showImporter = false
                    receivePickedFile(file)
                },
                onCancel: { showImporter = false }
            )
        }
    }

    private func receivePickedFile(_ file: URL) {
        guard kind.accepted.contains(file.pathExtension.lowercased()) else {
            status = "Sai định dạng. Vui lòng chọn: " + kind.accepted.map { "." + $0 }.joined(separator: ", ")
            return
        }
        importing = true
        status = "Đang sao chép tệp từ Files..."
        Task {
            do {
                let limit = kind.maxBytes
                let copied = try await Task.detached(priority: .userInitiated) {
                    try ImportedFile.copy(file, maxBytes: limit)
                }.value
                if let previous = selectedFile {
                    try? FileManager.default.removeItem(at: previous.deletingLastPathComponent())
                }
                selectedFile = copied
                if name.isEmpty {
                    name = file.deletingPathExtension().lastPathComponent
                }
                status = "Đã chọn và sao chép tệp thành công. Hãy điền thông tin rồi tải lên."
            } catch {
                status = "Không thể đọc tệp đã chọn: " + error.localizedDescription
            }
            importing = false
        }
    }

    private var valid: Bool {
        let amount = (kind == .ipa && modIsFree) ? 0 : (Int(price) ?? -1)
        guard session.authenticated, selectedFile != nil,
              !name.trimmingCharacters(in: .whitespaces).isEmpty,
              amount >= 0, (kind == .ipa || amount > 0) else { return false }
        switch kind {
        case .ipa: return authorized
        case .certificate: return !issuer.isEmpty
        case .ai: return true
        }
    }
    private func publish() async {
        guard let file = selectedFile else { return }
        working = true
        progress = 0
        defer { working = false }
        let fields: [(String, String)] = [
            ("name", name.trimmingCharacters(in: .whitespacesAndNewlines)),
            ("price_vnd", (kind == .ipa && modIsFree) ? "0" : price),
            ("issuer", issuer),
            ("description", description),
            ("authorized", authorized ? "true" : "false")
        ]
        do {
            try await session.uploadFile(kind: kind.rawValue, file: file,
                                         fields: fields, maxBytes: kind.maxBytes) { ratio in
                progress = ratio
                status = "Đang tải lên máy chủ: " + String(Int(ratio * 100)) + "%"
            }
            status = "Đã tải tệp và cập nhật sản phẩm thành công."
            selectedFile = nil
            try? FileManager.default.removeItem(at: file.deletingLastPathComponent())
        } catch {
            status = "Không thể tải lên: " + error.localizedDescription +
                ". Tệp IPA đã chọn vẫn được giữ trong ERI ADMIN."
        }
    }
}


// AI Plus and Chứng chỉ: public listings with manual fulfillment after payment.
// No IPA, private certificate, passwords or AI credentials are uploaded here.
private struct ManualListingView: View {
    @EnvironmentObject private var session: AdminSession
    let kind: UploadKind
    @State private var name = ""
    @State private var description = ""
    @State private var issuer = ""
    @State private var price = ""
    @State private var posting = false
    @State private var message = ""

    private var certificate: Bool { kind == .certificate }
    private var valid: Bool {
        session.authenticated &&
        (1...160).contains(name.trimmingCharacters(in: .whitespacesAndNewlines).count) &&
        description.count <= 2000 && (Int(price) ?? 0) > 0 && !posting
    }

    var body: some View {
        Form {
            Section("Hiển thị tại ERI STORE") {
                TextField(certificate ? "Tên gói chứng chỉ" : "Tên gói AI Plus", text: $name)
                TextField("Mô tả và thời gian giao hàng", text: $description, axis: .vertical)
                    .lineLimit(3...6)
                if certificate {
                    TextField("Nhà phát hành (không bắt buộc)", text: $issuer)
                }
                TextField("Giá vốn (VND)", text: $price)
                    .keyboardType(.numberPad)
            }
            Section("Đăng bán") {
                Button(posting ? "Đang đăng..." : "Đăng lên cửa hàng") {
                    Task { await post() }
                }.disabled(!valid)
                if !message.isEmpty { Text(message).font(.footnote) }
            }
            Section("Chỉ đăng thông tin") {
                Text("Không cần tải chứng chỉ, tài khoản AI hay dữ liệu riêng tư trước. Khách đặt mua bằng tài khoản ERI STORE, bạn sẽ thấy tên và liên hệ tại Lịch sử khách mua.")
                    .font(.footnote).foregroundStyle(.secondary)
                Text("Sau khi đối chiếu giao dịch MoMo, liên hệ khách để giao sản phẩm rồi nhấn Xác nhận đã giao.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
        .navigationTitle(certificate ? "Đăng chứng chỉ" : "Đăng AI Plus")
    }

    private func post() async {
        guard valid, let amount = Int(price) else { return }
        posting = true
        defer { posting = false }
        do {
            var payload: [String: Any] = [
                "name": name.trimmingCharacters(in: .whitespacesAndNewlines),
                "description": description.trimmingCharacters(in: .whitespacesAndNewlines),
                "price_vnd": amount, "active": true
            ]
            if certificate {
                let text = issuer.trimmingCharacters(in: .whitespacesAndNewlines)
                payload["issuer"] = text.isEmpty ? "Chưa xác định" : text
            }
            let body = try JSONSerialization.data(withJSONObject: payload)
            let path = certificate ? "/api/v1/admin/certificates" : "/api/v1/admin/products"
            _ = try await session.send(path, method: "POST", data: body)
            message = "Đã đăng bán. Chờ khách đặt mua, xác nhận tiền rồi giao thủ công."
            name = ""
            description = ""
            issuer = ""
            price = ""
        } catch {
            message = "Không đăng được: " + error.localizedDescription
        }
    }
}

private struct CatalogProduct: Decodable, Identifiable {
    let id: Int
    let name: String
    let description: String
    let price_vnd: Int
}

private struct CatalogList: Decodable {
    let items: [CatalogProduct]
}

private struct AdminCatalogView: View {
    @EnvironmentObject private var session: AdminSession
    let kind: UploadKind
    @State private var products: [CatalogProduct] = []
    @State private var showFree = false
    @State private var toDelete: CatalogProduct?
    @State private var busy = false
    @State private var message = ""

    private var visibleProducts: [CatalogProduct] {
        kind == .ipa ? products.filter { ($0.price_vnd == 0) == showFree } : products
    }

    var body: some View {
        List {
            if kind == .ipa {
                Picker("Loại Mod Game", selection: $showFree) {
                    Text("FREE").tag(true)
                    Text("BÁN").tag(false)
                }
                .pickerStyle(.segmented)
            }
            if !message.isEmpty { Text(message).foregroundStyle(.secondary) }
            if visibleProducts.isEmpty && !products.isEmpty {
                Text("Chưa có Mod Game thuộc tab này.").foregroundStyle(.secondary)
            }
            ForEach(visibleProducts) { item in
                VStack(alignment: .leading, spacing: 6) {
                    Text(item.name).font(.headline)
                    if !item.description.isEmpty { Text(item.description) }
                    Text(kind == .ipa && item.price_vnd == 0 ? "FREE · Tải trực tiếp" : eriMoney(item.price_vnd))
                    Button("Xóa sản phẩm", role: .destructive) { toDelete = item }
                        .disabled(busy)
                }
                .padding(.vertical, 5)
            }
        }
        .navigationTitle("Quản lý " + kind.title.replacingOccurrences(of: "Cập nhật ", with: ""))
        .toolbar { Button("Làm mới") { Task { await reload() } } }
        .task { await reload() }
        .confirmationDialog("Xóa sản phẩm khỏi ERI STORE?", isPresented: Binding(
            get: { toDelete != nil }, set: { if !$0 { toDelete = nil } }
        ), titleVisibility: .visible) {
            Button("Xóa vĩnh viễn", role: .destructive) {
                guard let item = toDelete else { return }
                toDelete = nil
                Task { await delete(item) }
            }
            Button("Hủy", role: .cancel) { toDelete = nil }
        } message: {
            Text("Sản phẩm sẽ biến mất khỏi cửa hàng. Tệp đã upload được xóa trên server, còn lịch sử đơn hàng được giữ lại.")
        }
    }

    private func reload() async {
        do {
            let result = try await session.send("/api/v1/admin/catalog/" + kind.rawValue)
            products = try JSONDecoder().decode(CatalogList.self, from: result).items
            message = products.isEmpty ? "Chưa có sản phẩm." : ""
        } catch { message = error.localizedDescription }
    }

    private func delete(_ item: CatalogProduct) async {
        busy = true
        defer { busy = false }
        do {
            _ = try await session.send("/api/v1/admin/catalog/" + kind.rawValue +
                                       "/" + String(item.id), method: "DELETE")
            await reload()
            message = "Đã xóa " + item.name
        } catch {
            message = "Xóa thất bại: " + error.localizedDescription
        }
    }
}

private struct MoMoStatus: Decodable {
    let linked: Bool
    let updated_at: String?
}

private struct MoMoLinkView: View {
    @EnvironmentObject private var session: AdminSession
    @State private var linked = false
    @State private var updated = ""
    @State private var picker = false
    @State private var working = false
    @State private var message = ""
    @State private var unlinkConfirm = false

    var body: some View {
        Form {
            Section("Liên kết MoMo") {
                Label(linked ? "Đã liên kết mã QR" : "Chưa có mã QR", systemImage: "qrcode")
                    .foregroundStyle(linked ? .green : .secondary)
                if linked {
                    AsyncImage(url: URL(string: server + "/api/v1/payment/momo-qr?preview=" +
                                        updated.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed).orEmpty)) { image in
                        image.resizable().scaledToFit()
                    } placeholder: {
                        ProgressView()
                    }
                    .frame(maxWidth: .infinity)
                    .frame(height: 220)
                }
                Button(working ? "Đang cập nhật..." : (linked ? "Thay ảnh QR MoMo" : "Liên kết MoMo")) {
                    picker = true
                }
                .disabled(working)
                if linked {
                    Button("Hủy liên kết", role: .destructive) {
                        unlinkConfirm = true
                    }
                    .disabled(working)
                }
                if !message.isEmpty { Text(message).font(.footnote) }
            }
            Section {
                Text("Chọn ảnh QR nhận tiền của tài khoản MoMo bạn quản lý (PNG hoặc JPG, tối đa 2 MB). Khách sẽ thấy ảnh này khi bấm Mua ngay, kèm số tiền và nội dung đơn hàng. Không phải liên kết API MoMo; không tự xác nhận thanh toán.")
                    .font(.footnote).foregroundStyle(.secondary)
                Text("Chỉ nhập ảnh QR, không nhập mật khẩu hay mã OTP MoMo.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
        .navigationTitle("Liên kết MoMo")
        .task { await refresh() }
        .sheet(isPresented: $picker) {
            ERIDocumentPicker(onPick: { url in
                picker = false
                Task { await upload(url) }
            }, onCancel: { picker = false })
        }
        .confirmationDialog("Hủy mã QR MoMo đã liên kết?", isPresented: $unlinkConfirm,
                            titleVisibility: .visible) {
            Button("Hủy liên kết", role: .destructive) { Task { await unlink() } }
            Button("Giữ lại", role: .cancel) {}
        }
    }

    private func refresh() async {
        do {
            let data = try await session.send("/api/v1/admin/payment/momo")
            let response = try JSONDecoder().decode(MoMoStatus.self, from: data)
            linked = response.linked
            updated = response.updated_at ?? ""
        } catch { message = "Không lấy được trạng thái: " + error.localizedDescription }
    }

    private func upload(_ source: URL) async {
        guard ["png", "jpg", "jpeg"].contains(source.pathExtension.lowercased()) else {
            message = "Chỉ chọn ảnh .png, .jpg hoặc .jpeg."
            return
        }
        working = true
        defer { working = false }
        do {
            let copied = try await Task.detached(priority: .userInitiated) {
                try ImportedFile.copy(source, maxBytes: 2 * 1024 * 1024)
            }.value
            defer { try? FileManager.default.removeItem(at: copied.deletingLastPathComponent()) }
            try await session.uploadMoMoQR(file: copied)
            message = "Đã cập nhật mã QR. Khách có thể xem ở bước thanh toán."
            await refresh()
        } catch { message = "Liên kết thất bại: " + error.localizedDescription }
    }

    private func unlink() async {
        working = true
        defer { working = false }
        do {
            _ = try await session.send("/api/v1/admin/payment/momo", method: "DELETE")
            linked = false
            updated = ""
            message = "Đã hủy liên kết QR."
        } catch { message = error.localizedDescription }
    }
}

private extension Optional where Wrapped == String {
    var orEmpty: String { self ?? "" }
}
