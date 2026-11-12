import SwiftUI
import UIKit
import UniformTypeIdentifiers

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

struct StoreIPAView: View {
    @State private var link = ""
    @State private var appName = ""
    @State private var appID = ""
    @State private var message = ""
    @State private var imported: URL?
    @State private var pick = false
    @State private var share = false
    @State private var busy = false
    var body: some View {
        NavigationView {
            Form {
                Section("Liên kết App Store") {
                    TextField("https://apps.apple.com/.../id123456", text: $link).textInputAutocapitalization(.never).autocorrectionDisabled()
                    Button("Tra cứu ứng dụng") { Task { await lookup() } }.disabled(busy)
                    if !appName.isEmpty { Text(appName).font(.headline); Text("App ID: " + appID) }
                }
                Section("IPA và cài đặt") {
                    Text("Apple không cung cấp API công khai tải IPA từ một liên kết App Store. Không thể xuất IPA thật chỉ từ URL.")
                        .font(.footnote).foregroundColor(.secondary)
                    Button("Nhập IPA có sẵn từ Tệp") { pick = true }
                    if let imported {
                        Text(imported.lastPathComponent)
                        Button("Tải / lưu IPA về máy") { share = true }
                        Button("Chia sẻ IPA sang ứng dụng ký") { share = true }
                    }
                    Text("Cài trực tiếp chỉ khả dụng với IPA đã ký hợp lệ và phương thức phân phối được iOS cho phép.")
                        .font(.footnote).foregroundColor(.secondary)
                    Text(message).font(.footnote)
                }
            }
            .navigationTitle("Xuất IPA")
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
    @MainActor private func lookup() async {
        busy = true; defer { busy = false }
        let parts = link.components(separatedBy: "id")
        guard let last = parts.last, let digits = last.split(whereSeparator: { !$0.isNumber }).first, let id = Int(digits) else {
            message = "Link không chứa App Store ID hợp lệ"; return
        }
        do {
            let url = URL(string: "https://itunes.apple.com/lookup?id=\(id)&country=vn")!
            let (data, _) = try await URLSession.shared.data(from: url)
            let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            let apps = obj?["results"] as? [[String: Any]] ?? []
            appName = apps.first?["trackName"] as? String ?? ""
            appID = String(id)
            message = appName.isEmpty ? "Không tìm thấy ứng dụng" : "Đã tra cứu thông tin App Store."
        } catch { message = error.localizedDescription }
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

struct InstallToolsView: View {
    @State private var tool = "ESign"
    @State private var manifest = ""
    @State private var message = ""
    var body: some View {
        NavigationView {
            Form {
                Section("Chọn công cụ") {
                    Picker("Ứng dụng", selection: $tool) {
                        Text("ESign").tag("ESign")
                        Text("KSign").tag("KSign")
                    }.pickerStyle(.segmented)
                    Text("Chỉ cài bằng manifest OTA HTTPS của gói được ký và phân phối hợp lệ. Không có đường cài đặt mặc định đáng tin cậy được tích hợp.")
                        .font(.footnote).foregroundColor(.secondary)
                }
                Section("Cài trực tiếp") {
                    TextField("https://.../manifest.plist", text: $manifest).textInputAutocapitalization(.never).autocorrectionDisabled()
                    Button("Cài " + tool) {
                        guard let url = URL(string: manifest), url.scheme == "https", url.pathExtension.lowercased() == "plist" else {
                            message = "Cần URL HTTPS đến manifest.plist hợp lệ."; return
                        }
                        guard let encoded = manifest.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed),
                              let installURL = URL(string: "itms-services://?action=download-manifest&url=" + encoded) else { return }
                        UIApplication.shared.open(installURL) { success in
                            message = success ? "Đã gửi yêu cầu cài đặt cho iOS; chưa xác nhận cài thành công." : "iOS không mở được yêu cầu cài đặt."
                        }
                    }
                    Text(message).font(.footnote)
                }
            }.navigationTitle("ESign / KSign")
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
