import SwiftUI
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

    func uploadFile(kind: String, file: URL, fields: [(String, String)], maxBytes: Int) async throws {
        var req = try request("/api/v1/admin/uploads/" + kind, method: "POST")
        let boundary = "ERI-" + UUID().uuidString
        req.setValue("multipart/form-data; boundary=" + boundary, forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 600
        let multipart = try await Task.detached(priority: .userInitiated) {
            try MultipartBuilder.make(source: file, boundary: boundary, fields: fields, maxBytes: maxBytes)
        }.value
        defer { try? FileManager.default.removeItem(at: multipart) }
        let (_, response) = try await URLSession.shared.upload(for: req, fromFile: multipart)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200...299).contains(status) else {
            if status == 401 || status == 403 { throw AdminError.invalidKey }
            throw AdminError.http(status)
        }
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
                Section("Nhập tệp để cập nhật sản phẩm") {
                    NavigationLink {
                        AdminUploadView(kind: .ipa).environmentObject(session)
                    } label: { Label("IPA Mod Game", systemImage: "square.and.arrow.up") }
                    NavigationLink {
                        AdminUploadView(kind: .certificate).environmentObject(session)
                    } label: { Label("Chứng chỉ", systemImage: "checkmark.seal") }
                    NavigationLink {
                        AdminUploadView(kind: .ai).environmentObject(session)
                    } label: { Label("AI Plus", systemImage: "sparkles") }
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
    // Explicit types keep .ipa, .p12 and .mobileprovision selectable in Files.
    var contentTypes: [UTType] {
        switch self {
        case .ipa:
            return [UTType(filenameExtension: "ipa", conformingTo: .data)
                    ?? UTType(importedAs: "com.eri.ipa", conformingTo: .data),
                    .archive, .data, .item]
        case .certificate:
            return [.item, .data, .archive]
        case .ai:
            return [.item, .data, .archive, .text, .json, .commaSeparatedText]
        }
    }
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

private enum MultipartBuilder {
    static func make(source: URL, boundary: String, fields: [(String, String)], maxBytes: Int) throws -> URL {
        let permission = source.startAccessingSecurityScopedResource()
        defer { if permission { source.stopAccessingSecurityScopedResource() } }
        let output = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".multipart")
        guard FileManager.default.createFile(atPath: output.path, contents: nil) else {
            throw URLError(.cannotCreateFile)
        }
        do {
            let writer = try FileHandle(forWritingTo: output)
            defer { try? writer.close() }
            func append(_ value: String) throws { try writer.write(contentsOf: Data(value.utf8)) }
            for (name, value) in fields {
                try append("--" + boundary + "\r\n")
                try append("Content-Disposition: form-data; name=\"" + name + "\"\r\n\r\n")
                try append(value + "\r\n")
            }
            let filename = source.lastPathComponent
                .replacingOccurrences(of: "\"", with: "")
                .replacingOccurrences(of: "\r", with: "")
                .replacingOccurrences(of: "\n", with: "")
            try append("--" + boundary + "\r\n")
            try append("Content-Disposition: form-data; name=\"upload\"; filename=\"" + filename + "\"\r\n")
            try append("Content-Type: application/octet-stream\r\n\r\n")
            let reader = try FileHandle(forReadingFrom: source)
            defer { try? reader.close() }
            var total = 0
            while let chunk = try reader.read(upToCount: 1_048_576), !chunk.isEmpty {
                total += chunk.count
                guard total <= maxBytes else { throw URLError(.dataLengthExceedsMaximum) }
                try writer.write(contentsOf: chunk)
            }
            guard total > 0 else { throw URLError(.zeroByteResource) }
            try append("\r\n--" + boundary + "--\r\n")
            return output
        } catch {
            try? FileManager.default.removeItem(at: output)
            throw error
        }
    }
}

private struct AdminUploadView: View {
    @EnvironmentObject private var session: AdminSession
    let kind: UploadKind
    @State private var name = ""
    @State private var version = ""
    @State private var bundleID = ""
    @State private var issuer = ""
    @State private var description = ""
    @State private var price = ""
    @State private var authorized = false
    @State private var selectedFile: URL?
    @State private var showImporter = false
    @State private var importing = false
    @State private var working = false
    @State private var status = ""

    var body: some View {
        Form {
            Section("Thông tin sản phẩm") {
                TextField("Tên sản phẩm", text: $name)
                if kind == .ipa {
                    TextField("Phiên bản IPA", text: $version)
                    TextField("Bundle ID", text: $bundleID)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    Toggle("Tôi có quyền phân phối IPA này", isOn: $authorized)
                } else {
                    if kind == .certificate {
                        TextField("Nhà phát hành chứng chỉ", text: $issuer)
                    }
                    TextField("Mô tả", text: $description, axis: .vertical)
                        .lineLimit(2...4)
                }
                TextField("Giá vốn (VND)", text: $price)
                    .keyboardType(.numberPad)
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
                    Text("Sau khi chọn IPA, nhập phiên bản, Bundle ID, giá vốn và xác nhận quyền phân phối để bật nút cập nhật.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Text("Định dạng: " + kind.accepted.map { "." + $0 }.joined(separator: ", "))
                    .font(.footnote).foregroundStyle(.secondary)
                Button(working ? "Đang tải tệp..." : "Cập nhật lên máy chủ") {
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
        .fileImporter(isPresented: $showImporter, allowedContentTypes: kind.contentTypes,
                      allowsMultipleSelection: false) { result in
            switch result {
            case .success(let urls):
                guard let file = urls.first else {
                    status = "Chưa có tệp được chọn."
                    return
                }
                guard kind.accepted.contains(file.pathExtension.lowercased()) else {
                    status = "Tệp không đúng định dạng: ." + file.pathExtension.lowercased()
                    return
                }
                importing = true
                status = "Đang sao chép tệp vào ứng dụng..."
                Task {
                    do {
                        let copied = try await Task.detached(priority: .userInitiated) {
                            try ImportedFile.copy(file, maxBytes: kind.maxBytes)
                        }.value
                        if let previous = selectedFile {
                            try? FileManager.default.removeItem(at: previous.deletingLastPathComponent())
                        }
                        selectedFile = copied
                        if name.isEmpty {
                            name = file.deletingPathExtension().lastPathComponent
                        }
                        status = "Chọn tệp thành công. Hãy điền các thông tin còn thiếu."
                    } catch {
                        status = "Không đọc được tệp: " + error.localizedDescription
                    }
                    importing = false
                }
            case .failure(let error):
                status = "Không chọn được tệp: " + error.localizedDescription
            }
        }
    }

    private var valid: Bool {
        guard session.authenticated, selectedFile != nil,
              !name.trimmingCharacters(in: .whitespaces).isEmpty,
              let amount = Int(price), amount > 0 else { return false }
        switch kind {
        case .ipa: return !version.isEmpty && bundleID.count >= 3 && authorized
        case .certificate: return !issuer.isEmpty
        case .ai: return true
        }
    }
    private func publish() async {
        guard let file = selectedFile else { return }
        working = true
        defer { working = false }
        let fields: [(String, String)] = [
            ("name", name.trimmingCharacters(in: .whitespacesAndNewlines)),
            ("price_vnd", price),
            ("version", version),
            ("bundle_id", bundleID),
            ("issuer", issuer),
            ("description", description),
            ("authorized", authorized ? "true" : "false")
        ]
        do {
            try await session.uploadFile(kind: kind.rawValue, file: file,
                                         fields: fields, maxBytes: kind.maxBytes)
            status = "Đã tải tệp và cập nhật sản phẩm thành công."
            selectedFile = nil
            try? FileManager.default.removeItem(at: file.deletingLastPathComponent())
        } catch {
            status = "Cập nhật thất bại: " + error.localizedDescription
        }
    }
}
