import SwiftUI
import UIKit
import UniformTypeIdentifiers
import CryptoKit
import CoreImage.CIFilterBuiltins
import WebKit

final class ERIIPASelection: ObservableObject {
    @Published var ipaURL: URL?
}

struct ContentView: View {
    @StateObject private var ipaSelection = ERIIPASelection()
    @State private var tab = 0
    @AppStorage("ERI_CUSTOM_API_BASE_URL") private var customAPI = ""
    @State private var showAPISettings = false
    var body: some View {
        TabView(selection: $tab) {
            EmailToolsView().tabItem { Label("Email", systemImage: "envelope") }.tag(0)
            StoreIPAView(selection: ipaSelection, openSigner: { tab = 2 }).tabItem { Label("IPA", systemImage: "square.and.arrow.down") }.tag(1)
            SignIPAView(selection: ipaSelection).tabItem { Label("Ký IPA", systemImage: "checkmark.shield") }.tag(2)
            InstallToolsView().tabItem { Label("Chứng chỉ", systemImage: "checkmark.seal") }.tag(3)
            AIPlusStoreView().tabItem { Label("AI Plus", systemImage: "sparkles") }.tag(4)
        }
        .accentColor(.cyan)
        .toolbar { Button("Máy chủ") { showAPISettings = true } }
        .sheet(isPresented: $showAPISettings) {
            NavigationView {
                Form {
                    Section("Máy chủ ERI HTTPS") {
                        TextField("https://api.example.com", text: $customAPI)
                            .textInputAutocapitalization(.never).autocorrectionDisabled()
                        Text("Chỉ nhập địa chỉ backend ERI đã triển khai và tin cậy. Không nhập API key quản trị.")
                            .font(.footnote).foregroundColor(.secondary)
                    }
                }
                .navigationTitle("Cấu hình cửa hàng")
                .toolbar { ToolbarItem(placement: .navigationBarTrailing) { Button("Xong") { showAPISettings = false } } }
            }
        }
    }
}

struct EmailToolsView: View {
    @State private var mode = 0
    @State private var gmail = ""
    @State private var aliases: [String] = []
    @State private var address = ""
    @State private var token = ""
    @State private var inbox: [TempMessage] = []
    @State private var selectedMessage = ""
    @State private var status = "Email tạm sử dụng dịch vụ mail.tm; không phải tài khoản Gmail."
    @State private var busy = false
    var body: some View {
        NavigationView {
            Form {
                Picker("Chế độ", selection: $mode) {
                    Text("Gmail Alias").tag(0)
                    Text("Email tạm").tag(1)
                }.pickerStyle(.segmented)
                if mode == 0 {
                    Section("Gmail thật của bạn") {
                        TextField("example@gmail.com", text: $gmail).textInputAutocapitalization(.never).autocorrectionDisabled()
                        Button("Tạo Gmail alias") {
                            let base = gmail.trimmingCharacters(in: .whitespacesAndNewlines)
                            guard base.lowercased().hasSuffix("@gmail.com") || base.lowercased().hasSuffix("@googlemail.com") else { return }
                            let user = String(base.split(separator: "@").first ?? "")
                            let alias = user + "+eri" + String(Int.random(in: 100000...999999)) + "@gmail.com"
                            aliases.insert(alias, at: 0)
                        }
                    }
                    Section("Danh sách alias (chạm để sao chép)") {
                        ForEach(aliases, id: \.self) { alias in
                            Button(alias) { UIPasteboard.general.string = alias }
                        }
                        Text("Alias Gmail chuyển thư về Gmail gốc; không tạo tài khoản Google mới.").font(.footnote)
                    }
                } else {
                    Section("Hộp thư tạm") {
                        Button(busy ? "Đang xử lý..." : "Tạo email tạm") { Task { await createMailbox() } }.disabled(busy)
                        if !address.isEmpty {
                            Button(address + "  ·  Sao chép") { UIPasteboard.general.string = address }
                            Button("Làm mới hộp thư") { Task { await refreshInbox() } }.disabled(busy)
                        }
                        Text(status).font(.footnote).foregroundColor(.secondary)
                    }
                    Section("Thư đã nhận") {
                        ForEach(inbox) { mail in
                            Button {
                                Task { await readMessage(mail.id) }
                            } label: {
                                VStack(alignment: .leading) {
                                    Text(mail.subject).font(.headline)
                                    Text(mail.from.address).font(.caption)
                                }
                            }
                        }
                        if !selectedMessage.isEmpty {
                            Text(selectedMessage).textSelection(.enabled)
                        }
                    }
                }
            }.navigationTitle("Email · ERI NGUYỄN")
        }
    }
    @MainActor private func createMailbox() async {
        busy = true; defer { busy = false }
        do {
            let domainsURL = URL(string: "https://api.mail.tm/domains?page=1")!
            let (data, _) = try await URLSession.shared.data(from: domainsURL)
            let domains = try JSONDecoder().decode(TempDomains.self, from: data)
            guard let domain = domains.hydraMember.first?.domain else { status = "Không lấy được tên miền"; return }
            let name = "eri" + UUID().uuidString.replacingOccurrences(of: "-", with: "").prefix(12).lowercased()
            let mail = name + "@" + domain
            let password = UUID().uuidString + "aA1!"
            let payload = try JSONSerialization.data(withJSONObject: ["address": mail, "password": password])
            var req = URLRequest(url: URL(string: "https://api.mail.tm/accounts")!)
            req.httpMethod = "POST"; req.setValue("application/json", forHTTPHeaderField: "Content-Type"); req.httpBody = payload
            let (_, response) = try await URLSession.shared.data(for: req)
            guard (response as? HTTPURLResponse)?.statusCode == 201 else { status = "Dịch vụ từ chối tạo hộp thư"; return }
            req = URLRequest(url: URL(string: "https://api.mail.tm/token")!)
            req.httpMethod = "POST"; req.setValue("application/json", forHTTPHeaderField: "Content-Type"); req.httpBody = payload
            let (tokenData, tokenResponse) = try await URLSession.shared.data(for: req)
            guard (tokenResponse as? HTTPURLResponse)?.statusCode == 200 else { status = "Không đăng nhập được hộp thư"; return }
            token = try JSONDecoder().decode(TempToken.self, from: tokenData).token
            address = mail; inbox = []; selectedMessage = ""; status = "Đã tạo hộp thư. Chạm địa chỉ để sao chép."
        } catch { status = "Lỗi email tạm: " + error.localizedDescription }
    }
    @MainActor private func refreshInbox() async {
        guard !token.isEmpty else { return }
        busy = true; defer { busy = false }
        do {
            var req = URLRequest(url: URL(string: "https://api.mail.tm/messages?page=1")!)
            req.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
            let (data, _) = try await URLSession.shared.data(for: req)
            inbox = try JSONDecoder().decode(TempMessages.self, from: data).hydraMember
            status = "Đã tải " + String(inbox.count) + " thư."
        } catch { status = error.localizedDescription }
    }
    @MainActor private func readMessage(_ id: String) async {
        do {
            var req = URLRequest(url: URL(string: "https://api.mail.tm/messages/" + id)!)
            req.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
            let (data, _) = try await URLSession.shared.data(for: req)
            let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            selectedMessage = (obj?["text"] as? String) ?? (obj?["intro"] as? String) ?? "Không có nội dung văn bản"
        } catch { status = error.localizedDescription }
    }
}
struct TempDomains: Decodable {
    let hydraMember: [TempDomain]
    enum CodingKeys: String, CodingKey { case hydraMember = "hydra:member" }
}
struct TempDomain: Decodable { let domain: String }
struct TempToken: Decodable { let token: String }
struct TempMessages: Decodable {
    let hydraMember: [TempMessage]
    enum CodingKeys: String, CodingKey { case hydraMember = "hydra:member" }
}
struct TempMessage: Decodable, Identifiable {
    let id: String
    let subject: String
    let from: TempSender
}
struct TempSender: Decodable { let address: String }

struct IPACatalog: Decodable {
    let schemaVersion: Int
    let apps: [IPACatalogEntry]
}
struct IPACatalogEntry: Decodable, Identifiable {
    let id: String
    let appStoreId: String
    let bundleId: String
    let name: String
    let version: String
    let sizeBytes: Int64
    let sha256: String
    let downloadURL: URL
    let authorizedForDistribution: Bool
}
struct StoreIPAView: View {
    @ObservedObject var selection: ERIIPASelection
    let openSigner: () -> Void
    @State private var showIPAImporter = false
    @State private var link = ""
    @State private var directIPALink = ""
    @State private var appName = ""
    @State private var appID = ""
    @State private var message = ""
    @State private var catalog: [IPACatalogEntry] = []
    @State private var imported: URL?
    @State private var share = false
    @State private var busy = false
    private let catalogURL = URL(string: "https://raw.githubusercontent.com/boyngheoanvat-ctrl/EriMail/main/ipa-catalog/catalog.json")!
    private var matches: [IPACatalogEntry] { catalog.filter { $0.appStoreId == appID && $0.authorizedForDistribution } }
    var body: some View {
        NavigationView {
            Form {
                Section("Tra cứu ứng dụng App Store") {
                    TextField("https://apps.apple.com/.../id123456", text: $link).textInputAutocapitalization(.never).autocorrectionDisabled()
                    Button("Tìm ứng dụng và kiểm tra kho") { Task { await lookup() } }.disabled(busy)
                    if !appName.isEmpty {
                        Text(appName).font(.headline)
                        Text("App Store ID: " + appID)
                    }
                }
                Section("Kho IPA riêng") {
                    if matches.isEmpty {
                        Text(appID.isEmpty ? "Nhập link để kiểm tra kho." : "Chưa có IPA được phép phân phối trong kho cho ứng dụng này.")
                            .foregroundColor(.secondary)
                    }
                    ForEach(matches) { entry in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(entry.name + " · " + entry.version).font(.headline)
                            Text(entry.bundleId).font(.caption)
                            Text(ByteCountFormatter.string(fromByteCount: entry.sizeBytes, countStyle: .file))
                            Button("Tải IPA về máy") { Task { await download(entry) } }.disabled(busy)
                        }
                    }
                    Button("Làm mới kho") { Task { await loadCatalog() } }.disabled(busy)
                    Text("Kho chỉ cung cấp IPA được chủ sở hữu cho phép phân phối. Không tự trích xuất ứng dụng thương mại từ App Store.")
                        .font(.footnote).foregroundColor(.secondary)
                }
                Section("IPA của bạn") {
                    Button { showIPAImporter = true } label: { Label("Nhập tệp IPA từ iPhone", systemImage: "doc.badge.plus") }
                    if let file = selection.ipaURL {
                        Text("Đã nhận: " + file.lastPathComponent).font(.footnote)
                        Button { openSigner() } label: { Label("Chuyển sang Ký IPA", systemImage: "checkmark.shield") }
                    }
                }
                Section("Tải IPA bằng link trực tiếp") {
                    TextField("https://example.com/app.ipa", text: $directIPALink)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                        .keyboardType(.URL)
                    Button(busy ? "Đang tải..." : "Tải IPA từ link") { Task { await downloadDirectIPA() } }.disabled(busy)
                    Text("Chỉ tải IPA từ nguồn bạn tin cậy và có quyền sử dụng.").font(.caption).foregroundColor(.secondary)
                    if let imported {
                        Text("IPA đã tải: " + imported.lastPathComponent)
                        Button("Lưu IPA vào Tệp") { share = true }
                    } else {
                        Text("Chọn phiên bản có sẵn phía trên và nhấn Tải IPA về máy. Nếu kho chưa có bản phù hợp, ứng dụng không thể tạo tệp IPA từ App Store ID.")
                            .font(.footnote).foregroundColor(.secondary)
                    }
                    Text(message).font(.footnote)
                }
            }
            .navigationTitle("ERI NGUYỄN · IPA")
            .task { await loadCatalog() }
            .sheet(isPresented: $share) { if let imported { ShareSheet(items: [imported]) } }
            .fileImporter(isPresented: $showIPAImporter, allowedContentTypes: [.data], allowsMultipleSelection: false) { result in
                do {
                    guard let url = try result.get().first else { return }
                    guard url.pathExtension.lowercased() == "ipa" else { message = "Vui lòng chọn tệp .ipa"; return }
                    let scoped = url.startAccessingSecurityScopedResource()
                    defer { if scoped { url.stopAccessingSecurityScopedResource() } }
                    let destination = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".ipa")
                    try FileManager.default.copyItem(at: url, to: destination)
                    let handle = try FileHandle(forReadingFrom: destination)
                    let magic = try handle.read(upToCount: 4) ?? Data()
                    try handle.close()
                    guard magic.starts(with: [0x50, 0x4B]) else {
                        try? FileManager.default.removeItem(at: destination)
                        message = "Tệp không phải IPA/ZIP hợp lệ."
                        return
                    }
                    selection.ipaURL = destination
                    imported = destination
                    message = "Đã nhập IPA thành công. Bấm Chuyển sang Ký IPA."
                } catch { message = "Không nhập được IPA: " + error.localizedDescription }
            }
        }
    }
    @MainActor private func loadCatalog() async {
        busy = true; defer { busy = false }
        do {
            var request = URLRequest(url: catalogURL)
            request.cachePolicy = .reloadIgnoringLocalCacheData
            let (data, response) = try await URLSession.shared.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.badServerResponse) }
            let decoded = try JSONDecoder().decode(IPACatalog.self, from: data)
            guard decoded.schemaVersion == 1 else { throw URLError(.cannotParseResponse) }
            catalog = decoded.apps.filter { $0.downloadURL.scheme == "https" && $0.sha256.count == 64 && $0.authorizedForDistribution }
            message = "Kho riêng: " + String(catalog.count) + " phiên bản IPA hợp lệ."
        } catch { catalog = []; message = "Không tải được danh mục kho: " + error.localizedDescription }
    }
    @MainActor private func lookup() async {
        guard let match = link.range(of: "id[0-9]+", options: .regularExpression),
              let id = Int(link[match].dropFirst(2)) else { message = "Link App Store không hợp lệ"; return }
        appID = String(id); appName = ""
        busy = true
        do {
            let url = URL(string: "https://itunes.apple.com/lookup?id=\(id)&country=vn")!
            let (data, _) = try await URLSession.shared.data(from: url)
            let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            let apps = obj?["results"] as? [[String: Any]] ?? []
            appName = apps.first?["trackName"] as? String ?? ""
            message = appName.isEmpty ? "Không tìm thấy ứng dụng trên App Store." : "Đã tìm thấy. Kiểm tra danh sách IPA bên dưới."
        } catch { message = error.localizedDescription }
        busy = false
        await loadCatalog()
    }
    @MainActor private func downloadDirectIPA() async {
        let input = directIPALink.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: input), url.scheme == "https", url.host != nil else {
            message = "Hãy nhập link HTTPS tải IPA hợp lệ."
            return
        }
        busy = true
        defer { busy = false }
        do {
            let (temp, response) = try await URLSession.shared.download(from: url)
            guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
                throw URLError(.badServerResponse)
            }
            let handle = try FileHandle(forReadingFrom: temp)
            defer { try? handle.close() }
            let signature = try handle.read(upToCount: 4) ?? Data()
            guard signature.starts(with: [0x50, 0x4B]) else {
                message = "Tệp tải xuống không có định dạng ZIP/IPA hợp lệ."
                return
            }
            let destination = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".ipa")
            try FileManager.default.moveItem(at: temp, to: destination)
            imported = destination
            selection.ipaURL = destination
            message = "Đã tải IPA. Bạn có thể lưu vào ứng dụng Tệp."
            share = true
        } catch {
            message = "Không tải được IPA: " + error.localizedDescription
        }
    }
    @MainActor private func download(_ entry: IPACatalogEntry) async {
        busy = true; defer { busy = false }
        do {
            let (tmp, response) = try await URLSession.shared.download(from: entry.downloadURL)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.badServerResponse) }
            let dst = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".ipa")
            try FileManager.default.moveItem(at: tmp, to: dst)
            let digest = SHA256.hash(data: try Data(contentsOf: dst)).map { String(format: "%02x", $0) }.joined()
            guard digest.lowercased() == entry.sha256.lowercased() else {
                try? FileManager.default.removeItem(at: dst)
                message = "SHA-256 không khớp. Đã hủy tệp tải xuống."
                return
            }
            imported = dst
            selection.ipaURL = dst
            message = "Đã tải và xác minh SHA-256. Chọn Lưu IPA để xuất vào Tệp."
            share = true
        } catch { message = "Tải IPA thất bại: " + error.localizedDescription }
    }
}

struct ERISigningResponse: Decodable {
    let download_url: String
    let message: String?
}

struct SignIPAView: View {
    @ObservedObject var selection: ERIIPASelection
    private enum ImportKind: String { case ipa, p12, mobileprovision }
    @State private var importing: ImportKind?
    @State private var showImporter = false
    @State private var ipaURL: URL?
    @State private var ipaLink = ""
    @State private var showAdvanced = false
    @State private var p12URL: URL?
    @State private var profileURL: URL?
    @State private var p12Password = ""
    @State private var signToken = ""
    @State private var removeEmbeddedProfile = false
    @State private var newBundleID = ""
    @State private var newBundleName = ""
    @State private var message = "Chọn đủ tệp để ký IPA bằng máy chủ ERI của bạn."
    @State private var busy = false
    @State private var signedURL: URL?
    @State private var showSignedShare = false

    private func choose(_ kind: ImportKind) {
        importing = kind
        showImporter = true
    }

    private func fileRow(_ title: String, kind: ImportKind, url: URL?) -> some View {
        Button { choose(kind) } label: {
            HStack {
                Image(systemName: "doc.badge.plus")
                VStack(alignment: .leading, spacing: 3) {
                    Text(title)
                    if let url {
                        Text(url.lastPathComponent).font(.caption)
                            .foregroundColor(.secondary).lineLimit(2)
                    }
                }
                Spacer()
                Text("Chọn").font(.subheadline)
            }
        }
    }

    var body: some View {
        NavigationView {
            Form {
                Section("Tệp ứng dụng") {
                    TextField("Dán link HTTPS tải IPA", text: $ipaLink)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                        .keyboardType(.URL)
                    fileRow("Hoặc chọn file .ipa", kind: .ipa, url: ipaURL ?? selection.ipaURL)
                    if let shared = selection.ipaURL { Text("IPA đã nhập: " + shared.lastPathComponent).font(.caption).foregroundColor(.secondary) }
                }
                Section("Chứng chỉ ký") {
                    fileRow("Chọn file .p12", kind: .p12, url: p12URL)
                    SecureField("Mật khẩu .p12", text: $p12Password)
                    fileRow("Chọn file .mobileprovision", kind: .mobileprovision, url: profileURL)
                }
                Section("Tùy chọn") {
                    Toggle("Xóa embedded.mobileprovision cũ", isOn: $removeEmbeddedProfile)
                    TextField("Bundle ID mới (tùy chọn)", text: $newBundleID)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                    TextField("Tên ứng dụng mới (tùy chọn)", text: $newBundleName)
                }
                Section("Kết nối ký ERI") {
                    DisclosureGroup("Cấu hình kết nối (chỉ cần thiết lập một lần)", isExpanded: $showAdvanced) {
                        SecureField("Mã truy cập dịch vụ ký", text: $signToken)
                        Text("Dịch vụ ký phải được kích hoạt trước.").font(.caption).foregroundColor(.secondary)
                    }
                }
                Section {
                    Button {
                        Task { await sign() }
                    } label: {
                        HStack {
                            Spacer()
                            if busy { ProgressView() }
                            Label(busy ? "Đang gửi tệp..." : "Ký IPA", systemImage: "signature")
                                .font(.headline)
                            Spacer()
                        }
                    }.disabled(busy)
                    Text(message).font(.footnote).foregroundColor(.secondary)
                    if let signedURL {
                        Button { showSignedShare = true } label: { Label("Chia sẻ / Lưu IPA đã ký", systemImage: "square.and.arrow.up") }
                    }
                }
                Section {
                    Text("Tệp P12 và mật khẩu chỉ được gửi đến dịch vụ ký ERI HTTPS khi bạn bấm Ký IPA. Chức năng chỉ hoạt động sau khi dịch vụ được cấu hình. Đổi Bundle ID/tên ứng dụng chưa được hỗ trợ.")
                        .font(.caption).foregroundColor(.secondary)
                }
            }
            .navigationTitle("ERI NGUYỄN · Ký IPA")
            .sheet(isPresented: $showSignedShare) {
                if let signedURL { ShareSheet(items: [signedURL]) }
            }
        }
        .fileImporter(isPresented: $showImporter,
                      allowedContentTypes: [UTType(filenameExtension: importing?.rawValue ?? "ipa") ?? .data, .data],
                      allowsMultipleSelection: false) { result in
            guard let kind = importing else { return }
            do {
                guard let selected = try result.get().first else { return }
                let expected = kind.rawValue
                guard selected.pathExtension.lowercased() == expected else {
                    message = "Hãy chọn tệp ." + expected
                    return
                }
                let scoped = selected.startAccessingSecurityScopedResource()
                defer { if scoped { selected.stopAccessingSecurityScopedResource() } }
                let folder = FileManager.default.temporaryDirectory
                    .appendingPathComponent("ERI-SigningInputs", isDirectory: true)
                try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
                let destination = folder.appendingPathComponent(UUID().uuidString + "." + expected)
                try FileManager.default.copyItem(at: selected, to: destination)
                switch kind {
                case .ipa: ipaURL = destination; selection.ipaURL = destination
                case .p12: p12URL = destination
                case .mobileprovision: profileURL = destination
                }
                signedURL = nil
                message = "Đã nhập " + selected.lastPathComponent
            } catch {
                message = "Lỗi nhập tệp: " + error.localizedDescription
            }
        }
    }

    @MainActor private func sign() async {
        guard let p12URL, let profileURL, !p12Password.isEmpty, !signToken.isEmpty else {
            message = "Cần P12, mobileprovision, mật khẩu và dịch vụ ký đã cấu hình."
            return
        }
        guard let base = ERIAPI.configuredBase(),
              let url = URL(string: base.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + "/api/v1/sign"),
              url.scheme == "https" else {
            message = "Chưa cấu hình backend ERI HTTPS trong Cấu hình cửa hàng."
            return
        }
        busy = true
        signedURL = nil
        defer { busy = false }
        do {
            let signingIPA: URL
            if !ipaLink.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                guard let remote = URL(string: ipaLink.trimmingCharacters(in: .whitespacesAndNewlines)), remote.scheme == "https" else {
                    throw NSError(domain: "ERI", code: 4, userInfo: [NSLocalizedDescriptionKey: "Link IPA phải bắt đầu bằng https://"])
                }
                let (downloaded, response) = try await URLSession.shared.download(from: remote)
                guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
                    throw NSError(domain: "ERI", code: 5, userInfo: [NSLocalizedDescriptionKey: "Không tải được IPA từ liên kết."])
                }
                let saved = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".ipa")
                try FileManager.default.moveItem(at: downloaded, to: saved)
                signingIPA = saved
            } else if let chosenIPA = ipaURL ?? selection.ipaURL {
                signingIPA = chosenIPA
            } else {
                throw NSError(domain: "ERI", code: 6, userInfo: [NSLocalizedDescriptionKey: "Dán link IPA hoặc chọn tệp IPA."])
            }
            let downloadedFromLink = !ipaLink.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            defer { if downloadedFromLink { try? FileManager.default.removeItem(at: signingIPA) } }
            let boundary = "ERI-" + UUID().uuidString
            let temporary = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".multipart")
            FileManager.default.createFile(atPath: temporary.path, contents: nil)
            defer { try? FileManager.default.removeItem(at: temporary) }
            let writer = try FileHandle(forWritingTo: temporary)
            defer { try? writer.close() }
            func write(_ string: String) throws {
                try writer.write(contentsOf: Data(string.utf8))
            }
            func field(_ name: String, _ value: String) throws {
                try write("--\(boundary)\r\nContent-Disposition: form-data; name=\(name)\r\n\r\n\(value)\r\n")
            }
            func attachment(_ name: String, _ file: URL, _ mime: String) throws {
                try write("--\(boundary)\r\nContent-Disposition: form-data; name=\(name); filename=\(file.lastPathComponent)\r\nContent-Type: \(mime)\r\n\r\n")
                let source = try FileHandle(forReadingFrom: file)
                defer { try? source.close() }
                while let chunk = try source.read(upToCount: 1024 * 1024), !chunk.isEmpty {
                    try writer.write(contentsOf: chunk)
                }
                try write("\r\n")
            }
            try field("password", p12Password)
            try field("identifier", newBundleID)
            try field("name", newBundleName)
            try field("remove_embedded", removeEmbeddedProfile ? "true" : "false")
            try attachment("ipa", signingIPA, "application/octet-stream")
            try attachment("p12", p12URL, "application/x-pkcs12")
            try attachment("mobileprovision", profileURL, "application/octet-stream")
            try write("--\(boundary)--\r\n")
            var request = URLRequest(url: url)
            request.httpMethod = "POST"
            request.setValue(signToken, forHTTPHeaderField: "X-ERI-SIGN-TOKEN")
            request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
            request.timeoutInterval = 600
            let (data, response) = try await URLSession.shared.upload(for: request, fromFile: temporary)
            guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
                let code = (response as? HTTPURLResponse)?.statusCode ?? 0
                throw NSError(domain: "ERI", code: code, userInfo: [NSLocalizedDescriptionKey: "Backend ký IPA chưa sẵn sàng (HTTP \(code))."])
            }
            let output = FileManager.default.temporaryDirectory.appendingPathComponent("ERI-signed-" + UUID().uuidString + ".ipa")
            try data.write(to: output, options: .atomic)
            signedURL = output
            message = "Đã nhận IPA đã ký. Dùng nút Chia sẻ để lưu tệp."
            p12Password = ""
        } catch {
            message = "Không thể ký IPA: " + error.localizedDescription
        }
    }
}

struct EriInstallCatalog: Decodable {
    let esign: [EriInstallItem]
    let ksign: [EriInstallItem]
}
struct EriInstallItem: Decodable, Identifiable {
    let id: String
    let name: String
    let certificate: String
    let manifestURL: URL
    let status: String
}
struct ERICertificate: Decodable, Identifiable {
    let id: Int
    let name: String
    let issuer: String
    let description: String
    let price_vnd: Int
}

struct InstallToolsView: View {
    @State private var manifest = ""
    @State private var message = ""
    @State private var certificates: [ERICertificate] = []
    @State private var catalogStatus = "Đang chờ kết nối ERI Backend"
    var body: some View {
        NavigationView {
            Form {
                Section("Chứng chỉ · ERI NGUYỄN") {
                    Text("Danh mục riêng của ERI NGUYỄN").font(.subheadline)
                    Text("Giá hiển thị theo danh mục sản phẩm ERI.")
                        .font(.footnote).foregroundColor(.secondary)
                    Button("Làm mới danh mục chứng chỉ") { Task { await loadCertificates() } }
                    Text(catalogStatus).font(.footnote).foregroundColor(.secondary)
                    if certificates.isEmpty {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Danh mục chứng chỉ ERI").font(.headline)
                            Text("Chứng chỉ ký ứng dụng iOS (P12 + mobileprovision)")
                            Text("Gia hạn / cấp lại chứng chỉ iOS")
                            Text("Giá: Chờ báo giá từ nguồn cung").foregroundColor(.secondary)
                            Text("Chưa mở thanh toán vì chưa xác minh chứng chỉ có thể giao.").font(.footnote).foregroundColor(.secondary)
                        }
                    }
                    ForEach(certificates) { item in
                        VStack(alignment: .leading, spacing: 5) {
                            Text(item.name).font(.headline)
                            Text("Đơn vị phát hành: " + item.issuer).font(.caption)
                            Text(item.description)
                            Text(item.price_vnd.formatted(.currency(code: "VND"))).fontWeight(.semibold)
                            ERIBuyButton(kind: "certificate", productID: item.id)
                        }
                    }
                    Text("Chọn sản phẩm, xem giá và quét QR MoMo để chuyển khoản. Đơn chờ ERI xác nhận sau khi kiểm tra giao dịch. Danh mục cần được thêm vào backend ERI.")
                        .font(.footnote).foregroundColor(.secondary)
                }
                Section("Cài ESign / KSign") {
                    Text("Nhập manifest HTTPS hợp lệ của ứng dụng được phép phân phối.")
                    TextField("https://.../manifest.plist", text: $manifest)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                    Button("Yêu cầu cài đặt") {
                        guard let url = URL(string: manifest), url.scheme == "https",
                              url.pathExtension.lowercased() == "plist",
                              let encoded = manifest.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed),
                              let installURL = URL(string: "itms-services://?action=download-manifest&url=" + encoded) else {
                            message = "Cần manifest.plist HTTPS hợp lệ."; return
                        }
                        UIApplication.shared.open(installURL) { ok in
                            message = ok ? "Đã gửi yêu cầu đến iOS; chưa xác nhận cài đặt." : "iOS không mở được manifest."
                        }
                    }
                    Text(message).font(.footnote)
                }
            }.navigationTitle("ERI NGUYỄN · Chứng chỉ")
            .task { await loadCertificates() }
        }
    }
    @MainActor private func loadCertificates() async {
        do {
            certificates = try await ERIAPI.load("/api/v1/certificates", as: ERICertificate.self)
            catalogStatus = certificates.isEmpty ? "Chưa có chứng chỉ được đăng bán." : "Danh mục từ ERI Backend."
        } catch { catalogStatus = error.localizedDescription }
    }
}

struct ShareSheet: UIViewControllerRepresentable {
    let items: [Any]
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }
    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}

struct AIPlusStoreView: View {
    @State private var items: [ERIProduct] = []
    @State private var status = "Đang chờ cấu hình API"
    var body: some View {
        NavigationView {
            Form {
                Section("AI Plus · ERI NGUYỄN") {
                    Text("Danh mục riêng của ERI NGUYỄN").font(.subheadline)
                    Button("Làm mới bảng giá") { Task { await load() } }
                    Text(status).font(.footnote).foregroundColor(.secondary)
                }
                Section("Gói AI") {
                    if items.isEmpty {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Danh mục AI ERI").font(.headline)
                            Text("Gói sử dụng công cụ AI chính hãng")
                            Text("Gói AI cho cá nhân và công việc")
                            Text("Giá: Chờ báo giá và xác nhận quyền phân phối").foregroundColor(.secondary)
                            Text("Chưa mở thanh toán khi chưa có sản phẩm được xác minh để giao.").font(.footnote).foregroundColor(.secondary)
                        }
                    }
                    Text("Giá bán theo danh mục riêng của ERI NGUYỄN.")
                        .font(.footnote).foregroundColor(.secondary)
                    ForEach(items) { item in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(item.name).font(.headline)
                            Text(item.description)
                            Text(item.price_vnd.formatted(.currency(code: "VND")))
                                .fontWeight(.semibold)
                            ERIBuyButton(kind: "ai", productID: item.id)
                        }
                    }
                    Text("Xem giá, chọn Mua ngay và quét QR MoMo. ERI kiểm tra chuyển khoản trước khi xác nhận đơn. Chỉ đặt hàng khi máy chủ ERI đã được triển khai và có sản phẩm hợp lệ.")
                        .font(.footnote).foregroundColor(.secondary)
                }
            }.navigationTitle("ERI NGUYỄN · AI Plus")
            .task { await load() }
        }
    }
    @MainActor private func load() async {
        do { items = try await ERIAPI.load("/api/v1/products", as: ERIProduct.self); status = "Bảng giá từ ERI Backend" }
        catch { status = error.localizedDescription }
    }
}

struct ERIList<T: Decodable>: Decodable { let items: [T] }
struct ERIProduct: Decodable, Identifiable {
    let id: Int
    let name: String
    let description: String
    let price_vnd: Int
}
struct ERIOffer: Decodable, Identifiable {
    let id: Int
    let title: String
    let description: String
    let source_url: String
}
enum ERIAPI {
    static func configuredBase() -> String? {
        let custom = UserDefaults.standard.string(forKey: "ERI_CUSTOM_API_BASE_URL")?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !custom.isEmpty { return custom }
        return Bundle.main.object(forInfoDictionaryKey: "ERI_API_BASE_URL") as? String
    }
    static func load<T: Decodable>(_ path: String, as: T.Type) async throws -> [T] {
        guard let base = configuredBase(),
              let url = URL(string: base.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + path),
              url.scheme == "https" else {
            throw NSError(domain: "ERI", code: 1, userInfo: [NSLocalizedDescriptionKey: "Chưa có máy chủ ERI HTTPS. Mở Cấu hình cửa hàng để nhập URL backend."])
        }
        let (data, response) = try await URLSession.shared.data(from: url)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw NSError(domain: "ERI", code: 2, userInfo: [NSLocalizedDescriptionKey: "Máy chủ ERI không phản hồi thành công."])
        }
        return try JSONDecoder().decode(ERIList<T>.self, from: data).items
    }
}

struct ERIOrder: Decodable, Identifiable {
    let id: String
    let kind: String
    let product_name: String
    let amount_vnd: Int
    let status: String
    let qr_url: String?
    let bank_account: String?
    let bank_name: String?
    let transfer_note: String?
    let payment_method: String?
}

struct ERIBuyButton: View {
    let kind: String
    let productID: Int
    @State private var order: ERIOrder?
    @State private var errorMessage = ""
    @State private var busy = false
    @State private var showOrder = false
    var body: some View {
        Button(busy ? "Đang tạo đơn..." : "Mua ngay · QR MoMo") {
            Task { await create() }
        }.disabled(busy)
        .sheet(isPresented: $showOrder) {
            NavigationView {
                Form {
                    if let order {
                        Section("Đơn hàng " + order.id) {
                            Text(order.product_name).font(.headline)
                            Text(order.amount_vnd.formatted(.currency(code: "VND")))
                            Text("Trạng thái: " + order.status)
                            if let bank = order.bank_name { Text("Ngân hàng: " + bank) }
                            if let account = order.bank_account { Text("Số tài khoản: " + account) }
                            if let note = order.transfer_note {
                                Text("Nội dung: " + note)
                                Button("Sao chép nội dung") { UIPasteboard.general.string = note }
                            }
                            if order.payment_method == "static_momo" {
                                Text("Quét mã MoMo để thanh toán").font(.headline)
                                if let qr = ERIMomoQRCode.makeImage() {
                                    Image(uiImage: qr)
                                        .interpolation(.none)
                                        .resizable()
                                        .scaledToFit()
                                        .frame(maxWidth: 290, maxHeight: 290)
                                        .frame(maxWidth: .infinity)
                                }
                                Text("Người nhận: NGUYEN MINH THIEN")
                                Text("Số tài khoản được che trên ảnh gốc (đuôi 790).")
                                    .font(.footnote).foregroundColor(.secondary)
                                Text("QR MoMo cố định: vui lòng nhập đúng số tiền hiển thị và nội dung chuyển khoản. Mã QR không tự điền thông tin đơn hàng.")
                                    .font(.footnote).foregroundColor(.secondary)
                            }
                            if let raw = order.qr_url, let url = URL(string: raw) {
                                AsyncImage(url: url) { image in image.resizable().scaledToFit() }
                                placeholder: { ProgressView() }
                                .frame(maxHeight: 320)
                            }
                            Text("Chuyển đúng số tiền và nội dung. Đơn chỉ được xác nhận khi người bán kiểm tra tiền thực nhận; QR không tự xác minh giao dịch.")
                                .font(.footnote).foregroundColor(.secondary)
                            Button("Kiểm tra trạng thái") { Task { await refresh() } }
                        }
                    }
                }
                .navigationTitle("Thanh toán ERI")
                .toolbar { ToolbarItem(placement: .navigationBarTrailing) { Button("Đóng") { showOrder = false } } }
            }
        }
        if !errorMessage.isEmpty { Text(errorMessage).font(.footnote).foregroundColor(.secondary) }
    }
    @MainActor private func create() async {
        busy = true
        defer { busy = false }
        do {
            let body = try JSONSerialization.data(withJSONObject: ["kind": kind, "product_id": productID])
            order = try await ERIAPI.createOrder(body)
            errorMessage = ""
            showOrder = true
        } catch { errorMessage = error.localizedDescription }
    }
    @MainActor private func refresh() async {
        guard let id = order?.id else { return }
        do { order = try await ERIAPI.fetchOrder(id) }
        catch { errorMessage = error.localizedDescription }
    }
}

extension ERIAPI {
    static func baseURL() throws -> URL {
        guard let base = configuredBase(),
              let url = URL(string: base), url.scheme == "https" else {
            throw NSError(domain: "ERI", code: 1, userInfo: [NSLocalizedDescriptionKey: "Chưa có máy chủ ERI HTTPS. Hãy cấu hình URL backend."])
        }
        return url
    }
    static func createOrder(_ data: Data) async throws -> ERIOrder {
        let base = try baseURL()
        var req = URLRequest(url: base.appendingPathComponent("api/v1/orders"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = data
        let (responseData, response) = try await URLSession.shared.data(for: req)
        guard (response as? HTTPURLResponse)?.statusCode == 201 else {
            throw NSError(domain: "ERI", code: 2, userInfo: [NSLocalizedDescriptionKey: "Không tạo được đơn hàng. Kiểm tra backend và ngân hàng."])
        }
        return try JSONDecoder().decode(ERIOrder.self, from: responseData)
    }
    static func fetchOrder(_ id: String) async throws -> ERIOrder {
        let base = try baseURL()
        let (data, response) = try await URLSession.shared.data(from: base.appendingPathComponent("api/v1/orders/" + id))
        guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.badServerResponse) }
        return try JSONDecoder().decode(ERIOrder.self, from: data)
    }
}

enum ERIMomoQRCode {
    // Exact payment QR payload decoded from the user-supplied MoMo/VietQR image.
    // Static QR: no per-order amount or automatic payment verification.
    private static let payload = "00020101021138540010A00000072701240006971025011008967347900208QRIBFTTA53037045802VN62450515MOMOW2W586762860622L0xzbAclKK6dzYgrgG2ZLA630494EF"
    static func makeImage() -> UIImage? {
        let filter = CIFilter.qrCodeGenerator()
        filter.message = Data(payload.utf8)
        filter.correctionLevel = "H"
        guard let output = filter.outputImage else { return nil }
        let scaled = output.transformed(by: CGAffineTransform(scaleX: 9, y: 9))
        let context = CIContext()
        guard let cgImage = context.createCGImage(scaled, from: scaled.extent) else { return nil }
        return UIImage(cgImage: cgImage)
    }
}
