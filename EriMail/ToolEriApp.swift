import SwiftUI
import UIKit
import UniformTypeIdentifiers
import CryptoKit
import WebKit

struct ContentView: View {
    @State private var tab = 0
    var body: some View {
        TabView(selection: $tab) {
            EmailToolsView().tabItem { Label("Email", systemImage: "envelope") }.tag(0)
            StoreIPAView().tabItem { Label("IPA", systemImage: "square.and.arrow.down") }.tag(1)
            SignIPAView().tabItem { Label("Ký IPA", systemImage: "checkmark.shield") }.tag(2)
            StoreOffersView().tabItem { Label("Ưu đãi", systemImage: "tag") }.tag(3)
            InstallToolsView().tabItem { Label("ESign/KSign", systemImage: "iphone") }.tag(4)
            AIPlusStoreView().tabItem { Label("AI Plus", systemImage: "sparkles") }.tag(5)
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
                Section("Tải IPA · ERI NGUYỄN") {
                    Button(busy ? "Đang kiểm tra kho..." : "Tìm IPA để tải") {
                        Task { await loadCatalog() }
                    }.disabled(busy)
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
            .navigationTitle("ERI NGUYỄN · Xuất IPA")
            .task { await loadCatalog() }
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
    @State private var ipaURL: URL?
    @State private var pickIPA = false
    var body: some View {
        NavigationView {
            Form {
                Section("Ký IPA · ERI NGUYỄN") {
                    Button("Chọn IPA từ Tệp") { pickIPA = true }
                    if let ipaURL { Text(ipaURL.lastPathComponent) }
                    Text("Chưa có máy chủ ký IPA được cấu hình. Ký thật yêu cầu worker macOS, chứng chỉ P12 và provisioning profile hợp lệ. Không gửi khóa riêng đến máy chủ chưa được xác minh.")
                        .foregroundColor(.secondary)
                }
            }.navigationTitle("ERI NGUYỄN · Ký IPA")
            .fileImporter(isPresented: $pickIPA, allowedContentTypes: [.data, .archive]) { result in
                if case .success(let url) = result { ipaURL = url }
            }
        }
    }
}

struct StoreOffersView: View {
    @State private var items: [ERIOffer] = []
    @State private var status = "Đang chờ cấu hình API"
    var body: some View {
        NavigationView {
            Form {
                Section("Ưu đãi · ERI NGUYỄN") {
                    Button("Làm mới") { Task { await load() } }
                    Text(status).font(.footnote).foregroundColor(.secondary)
                    ForEach(items) { item in
                        VStack(alignment: .leading) {
                            Text(item.title).font(.headline)
                            Text(item.description)
                            Text(item.source_url).font(.caption).textSelection(.enabled)
                        }
                    }
                }
            }.navigationTitle("ERI NGUYỄN · Ưu đãi")
            .task { await load() }
        }
    }
    @MainActor private func load() async {
        do { items = try await ERIAPI.load("/api/v1/offers", as: ERIOffer.self); status = "Dữ liệu từ ERI Backend" }
        catch { status = error.localizedDescription }
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
                    Button("Làm mới danh mục chứng chỉ") { Task { await loadCertificates() } }
                    Text(catalogStatus).font(.footnote).foregroundColor(.secondary)
                    ForEach(certificates) { item in
                        VStack(alignment: .leading, spacing: 5) {
                            Text(item.name).font(.headline)
                            Text("Đơn vị phát hành: " + item.issuer).font(.caption)
                            Text(item.description)
                            Text(item.price_vnd.formatted(.currency(code: "VND"))).fontWeight(.semibold)
                            ERIBuyButton(kind: "certificate", productID: item.id)
                        }
                    }
                    Text("Chưa tích hợp đặt hàng, thanh toán hoặc cấp chứng chỉ. Chỉ đăng bán chứng chỉ có quyền phân phối; chứng chỉ của Khoindvn phải ghi đúng nguồn và có thỏa thuận bán lại.")
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

struct OfficialInstallWebView: UIViewRepresentable {
    func makeUIView(context: Context) -> WKWebView {
        let webView = WKWebView()
        webView.load(URLRequest(url: URL(string: "https://khoindvn.io.vn/install/")!))
        return webView
    }
    func updateUIView(_ view: WKWebView, context: Context) {}
}

struct ShareSheet: UIViewControllerRepresentable {
    let items: [Any]
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }
    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}

struct F4IPAWebView: UIViewRepresentable {
    func makeUIView(context: Context) -> WKWebView {
        let view = WKWebView()
        view.load(URLRequest(url: URL(string: "https://decrypt.160-191-242-70.sslip.io/")!))
        return view
    }
    func updateUIView(_ view: WKWebView, context: Context) {}
}

struct AIPlusStoreView: View {
    @State private var items: [ERIProduct] = []
    @State private var status = "Đang chờ cấu hình API"
    var body: some View {
        NavigationView {
            Form {
                Section("AI Plus · ERI NGUYỄN") {
                    Button("Làm mới bảng giá") { Task { await load() } }
                    Text(status).font(.footnote).foregroundColor(.secondary)
                }
                Section("Gói AI") {
                    ForEach(items) { item in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(item.name).font(.headline)
                            Text(item.description)
                            Text(item.price_vnd.formatted(.currency(code: "VND")))
                                .fontWeight(.semibold)
                            ERIBuyButton(kind: "ai", productID: item.id)
                        }
                    }
                    Text("Chưa tích hợp thanh toán hoặc giao hàng. Không thu tiền khi chưa có hệ thống đơn hàng.")
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

struct AIPlusStoreWebView: UIViewRepresentable {
    func makeUIView(context: Context) -> WKWebView {
        let view = WKWebView()
        view.load(URLRequest(url: URL(string: "https://sky-sky-sky.com/#bang-gia")!))
        return view
    }
    func updateUIView(_ view: WKWebView, context: Context) {}
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
    static func load<T: Decodable>(_ path: String, as: T.Type) async throws -> [T] {
        guard let base = Bundle.main.object(forInfoDictionaryKey: "ERI_API_BASE_URL") as? String,
              let url = URL(string: base.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + path),
              url.scheme == "https" else {
            throw NSError(domain: "ERI", code: 1, userInfo: [NSLocalizedDescriptionKey: "Chưa cấu hình ERI_API_BASE_URL HTTPS trong Info.plist."])
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
}

struct ERIBuyButton: View {
    let kind: String
    let productID: Int
    @State private var order: ERIOrder?
    @State private var errorMessage = ""
    @State private var busy = false
    @State private var showOrder = false
    var body: some View {
        Button(busy ? "Đang tạo đơn..." : "Mua ngay · QR ngân hàng") {
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
        guard let base = Bundle.main.object(forInfoDictionaryKey: "ERI_API_BASE_URL") as? String,
              let url = URL(string: base), url.scheme == "https" else {
            throw NSError(domain: "ERI", code: 1, userInfo: [NSLocalizedDescriptionKey: "Chưa cấu hình ERI_API_BASE_URL HTTPS."])
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
