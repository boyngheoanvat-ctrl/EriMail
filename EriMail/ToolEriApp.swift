import SwiftUI
import UIKit
import UniformTypeIdentifiers
import CryptoKit

struct ContentView: View {
    @State private var tab = 0
    var body: some View {
        TabView(selection: $tab) {
            EmailToolsView().tabItem { Label("Email", systemImage: "envelope") }.tag(0)
            StoreIPAView().tabItem { Label("IPA", systemImage: "square.and.arrow.down") }.tag(1)
            SignIPAView().tabItem { Label("Ký IPA", systemImage: "checkmark.shield") }.tag(2)
            StoreOffersView().tabItem { Label("Ưu đãi", systemImage: "tag") }.tag(3)
            InstallToolsView().tabItem { Label("ESign/KSign", systemImage: "iphone") }.tag(4)
        }
        .accentColor(.cyan)
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
                        Text("Gmail alias chuyển thư về hộp thư Gmail gốc, không tạo tài khoản Google mới.").font(.footnote)
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
            }.navigationTitle("Tạo Gmail giả")
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
    @State private var link = ""
    @State private var appName = ""
    @State private var appID = ""
    @State private var message = ""
    @State private var catalog: [IPACatalogEntry] = []
    @State private var imported: URL?
    @State private var pick = false
    @State private var share = false
    @State private var busy = false
    private let catalogURL = URL(string: "https://raw.githubusercontent.com/boyngheoanvat-ctrl/EriMail/main/ipa-catalog/catalog.json")!
    private var matches: [IPACatalogEntry] { catalog.filter { $0.appStoreId == appID && $0.authorizedForDistribution } }
    var body: some View {
        NavigationView {
            Form {
                Section("Tìm App Store") {
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
                Section("IPA đã tải hoặc nhập") {
                    Button("Nhập IPA từ Tệp") { pick = true }
                    if let imported {
                        Text(imported.lastPathComponent)
                        Button("Lưu IPA / chia sẻ sang ứng dụng ký") { share = true }
                    }
                    Text(message).font(.footnote)
                }
            }
            .navigationTitle("Kho IPA")
            .task { await loadCatalog() }
            .fileImporter(isPresented: $pick, allowedContentTypes: [.data, .archive], allowsMultipleSelection: false) { result in
                do {
                    guard let url = try result.get().first else { return }
                    guard url.pathExtension.lowercased() == "ipa" else { message = "Vui lòng chọn .ipa"; return }
                    let access = url.startAccessingSecurityScopedResource()
                    defer { if access { url.stopAccessingSecurityScopedResource() } }
                    let dst = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".ipa")
                    try FileManager.default.copyItem(at: url, to: dst)
                    imported = dst; message = "Đã nhập IPA."
                } catch { message = error.localizedDescription }
            }
            .sheet(isPresented: $share) { if let imported { ShareSheet(items: [imported]) } }
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
            message = "Đã tải và xác minh SHA-256. Chọn Lưu IPA để xuất vào Tệp."
            share = true
        } catch { message = "Tải IPA thất bại: " + error.localizedDescription }
    }
}

struct SignIPAView: View {
    @State private var ipa: URL?
    @State private var p12: URL?
    @State private var provision: URL?
    @State private var password = ""
    @State private var picking: String?
    @State private var status = ""
    var body: some View {
        NavigationView {
            Form {
                Section("Tệp ký IPA") {
                    Button("Nhập IPA: " + (ipa?.lastPathComponent ?? "Chưa chọn")) { picking = "ipa" }
                    Button("Nhập P12: " + (p12?.lastPathComponent ?? "Chưa chọn")) { picking = "p12" }
                    Button("Nhập mobileprovision: " + (provision?.lastPathComponent ?? "Chưa chọn")) { picking = "mobileprovision" }
                    SecureField("Mật khẩu P12", text: $password)
                }
                Section("Ký ứng dụng") {
                    Button("Kiểm tra tệp đầu vào") {
                        if ipa == nil || p12 == nil || provision == nil || password.isEmpty {
                            status = "Cần đủ IPA, P12, mobileprovision và mật khẩu."
                        } else {
                            status = "Đã nhập đủ tệp. Chưa có bộ ký IPA thực tế trong ứng dụng; chưa tạo IPA đã ký."
                        }
                    }
                    Text(status).font(.footnote)
                    Text("Chứng chỉ và mật khẩu chỉ lưu trong phiên làm việc. Ký IPA yêu cầu xử lý Mach-O, entitlements, provisioning và chữ ký mã hợp lệ; nhập tệp không đồng nghĩa đã ký.")
                        .font(.footnote).foregroundColor(.secondary)
                }
            }.navigationTitle("Ký IPA")
            .fileImporter(isPresented: Binding(get: { picking != nil }, set: { if !$0 { picking = nil } }), allowedContentTypes: [.data, .archive], allowsMultipleSelection: false) { result in
                let kind = picking ?? ""
                picking = nil
                do {
                    guard let url = try result.get().first else { return }
                    guard url.pathExtension.lowercased() == kind else { status = "Cần tệp ." + kind; return }
                    let access = url.startAccessingSecurityScopedResource()
                    defer { if access { url.stopAccessingSecurityScopedResource() } }
                    let dst = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + "." + kind)
                    try FileManager.default.copyItem(at: url, to: dst)
                    if kind == "ipa" { ipa = dst }
                    if kind == "p12" { p12 = dst }
                    if kind == "mobileprovision" { provision = dst }
                    status = "Đã nhập ." + kind
                } catch { status = error.localizedDescription }
            }
        }
    }
}

struct StoreOffersView: View {
    @State private var query = ""
    var body: some View {
        NavigationView {
            Form {
                Section("Tìm ưu đãi") {
                    TextField("Tên ứng dụng hoặc trò chơi", text: $query)
                    Button("Tìm chương trình ưu đãi") {
                        let q = query.trimmingCharacters(in: .whitespacesAndNewlines)
                        guard !q.isEmpty else { return }
                        let url = URL(string: "https://www.google.com/search?q=" + (q + " App Store promo code discount official").addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed)!)!
                        UIApplication.shared.open(url)
                    }
                    Text("Không có API công khai tổng hợp mã giảm giá thanh toán App Store. Chỉ sử dụng mã có nguồn phát hành xác thực; ứng dụng không tự tạo mã hoặc cam kết mã còn hiệu lực.")
                        .font(.footnote).foregroundColor(.secondary)
                }
            }.navigationTitle("Ưu đãi App Store")
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
struct InstallToolsView: View {
    @State private var tool = "ESign"
    @State private var catalog = EriInstallCatalog(esign: [], ksign: [])
    @State private var message = "Đang chờ danh mục cài đặt riêng."
    @State private var busy = false
    private let source = URL(string: "https://raw.githubusercontent.com/boyngheoanvat-ctrl/EriMail/main/ipa-catalog/install-tools.json")!
    private var items: [EriInstallItem] { tool == "ESign" ? catalog.esign : catalog.ksign }
    var body: some View {
        NavigationView {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    VStack(alignment: .leading, spacing: 7) {
                        Text("ERI NGUYỄN").font(.system(size: 27, weight: .black, design: .rounded))
                            .foregroundColor(.purple)
                        Text("CÀI ĐẶT ESIGN & KSIGN").font(.headline)
                        Text("Chọn phiên bản đã ký hợp lệ để gửi yêu cầu cài đặt tới iOS.")
                            .font(.subheadline).foregroundColor(.secondary)
                    }
                    .padding(20).frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color(UIColor.secondarySystemGroupedBackground))
                    .cornerRadius(22)
                    Picker("Công cụ", selection: $tool) {
                        Text("ESign").tag("ESign")
                        Text("KSign").tag("KSign")
                    }
                    .pickerStyle(.segmented)
                    .padding(.horizontal, 35)
                    if items.isEmpty {
                        VStack(spacing: 10) {
                            Image(systemName: "tray").font(.largeTitle).foregroundColor(.purple)
                            Text("Chưa có phiên bản " + tool).font(.headline)
                            Text("Danh mục riêng chưa có manifest được xác minh. Không hiển thị liên kết cài đặt giả.")
                                .font(.footnote).foregroundColor(.secondary).multilineTextAlignment(.center)
                        }
                        .frame(maxWidth: .infinity).padding(24)
                        .background(Color(UIColor.secondarySystemGroupedBackground)).cornerRadius(22)
                    }
                    ForEach(items) { item in
                        HStack(spacing: 14) {
                            ZStack {
                                RoundedRectangle(cornerRadius: 15).fill(Color.blue)
                                Text(tool == "ESign" ? "E" : "K")
                                    .font(.system(size: 32, weight: .bold, design: .rounded)).foregroundColor(.white)
                            }.frame(width: 62, height: 62)
                            VStack(alignment: .leading, spacing: 5) {
                                Text(item.name).font(.headline)
                                Text(item.certificate).font(.subheadline).foregroundColor(.secondary)
                            }
                            Spacer(minLength: 4)
                            Button("CÀI ĐẶT") { install(item) }
                                .font(.system(size: 12, weight: .bold))
                                .padding(.horizontal, 12).padding(.vertical, 10)
                                .background(Color.purple.opacity(0.12)).foregroundColor(.purple)
                                .clipShape(Capsule())
                                .disabled(item.status != "available")
                        }
                        .padding(15)
                        .background(Color(UIColor.secondarySystemGroupedBackground))
                        .cornerRadius(22)
                    }
                    Button(busy ? "Đang cập nhật..." : "Làm mới danh sách") {
                        Task { await load() }
                    }.disabled(busy)
                    .frame(maxWidth: .infinity)
                    Text(message).font(.footnote).foregroundColor(.secondary)
                    Text("iOS chỉ cho phép cài ứng dụng được ký và phân phối hợp lệ. Yêu cầu cài đặt không bảo đảm cài thành công.")
                        .font(.footnote).foregroundColor(.secondary)
                }
                .padding()
            }
            .background(Color(UIColor.systemGroupedBackground))
            .navigationBarTitleDisplayMode(.inline)
            .navigationTitle("ERI NGUYỄN")
            .task { await load() }
        }
    }
    @MainActor private func load() async {
        busy = true; defer { busy = false }
        do {
            var request = URLRequest(url: source)
            request.cachePolicy = .reloadIgnoringLocalCacheData
            let (data, response) = try await URLSession.shared.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.badServerResponse) }
            let decoded = try JSONDecoder().decode(EriInstallCatalog.self, from: data)
            catalog = EriInstallCatalog(
                esign: decoded.esign.filter { $0.manifestURL.scheme == "https" },
                ksign: decoded.ksign.filter { $0.manifestURL.scheme == "https" }
            )
            message = "Đã cập nhật " + String(catalog.esign.count + catalog.ksign.count) + " phiên bản."
        } catch { message = "Không cập nhật được danh sách: " + error.localizedDescription }
    }
    private func install(_ item: EriInstallItem) {
        guard item.status == "available", item.manifestURL.scheme == "https",
              let encoded = item.manifestURL.absoluteString.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed),
              let url = URL(string: "itms-services://?action=download-manifest&url=" + encoded) else {
            message = "Manifest không hợp lệ hoặc phiên bản chưa sẵn sàng."
            return
        }
        UIApplication.shared.open(url) { success in
            message = success ? "Đã gửi yêu cầu cài đặt tới iOS, chưa xác nhận cài thành công." : "iOS từ chối mở yêu cầu cài đặt."
        }
    }
}

struct ShareSheet: UIViewControllerRepresentable {
    let items: [Any]
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }
    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}
