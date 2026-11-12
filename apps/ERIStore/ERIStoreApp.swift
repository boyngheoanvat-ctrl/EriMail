import SwiftUI

@main
struct ERIStoreApp: App {
    var body: some Scene { WindowGroup { StoreHome() } }
}

private struct StoreHome: View {
    @State private var showSettings = false
    var body: some View {
        TabView {
            StoreCatalog(title: "AI Plus", endpoint: "products")
                .tabItem { Label("AI Plus", systemImage: "sparkles") }
            StoreCatalog(title: "Chứng chỉ", endpoint: "certificates")
                .tabItem { Label("Chứng chỉ", systemImage: "checkmark.seal") }
            ModCatalog()
                .tabItem { Label("Mod Game", systemImage: "gamecontroller") }
        }
        .sheet(isPresented: $showSettings) { ServerSettingsView() }
        .environment(\.openERISettings, { showSettings = true })
    }
}
private struct OpenERISettingsKey: EnvironmentKey {
    static let defaultValue: () -> Void = {}
}
private extension EnvironmentValues {
    var openERISettings: () -> Void {
        get { self[OpenERISettingsKey.self] }
        set { self[OpenERISettingsKey.self] = newValue }
    }
}
private struct ServerSettingsView: View {
    @Environment(\.dismiss) private var dismiss
    @AppStorage("eri_store_api") private var api = ""
    @State private var draft = ""
    @State private var message = ""
    var body: some View {
        NavigationView {
            Form {
                Section("Máy chủ ERI") {
                    TextField("https://your-api.example.com", text: $draft)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                    Button("Lưu địa chỉ máy chủ") {
                        let value = draft.trimmingCharacters(in: .whitespacesAndNewlines)
                            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
                        guard let url = URLComponents(string: value),
                              url.scheme == "https", url.host != nil,
                              url.user == nil, url.password == nil,
                              url.path.isEmpty, url.query == nil, url.fragment == nil else {
                            message = "Nhập URL HTTPS của máy chủ, không kèm đường dẫn."
                            return
                        }
                        api = value
                        message = "Đã lưu. Quay lại tab và nhấn Làm mới."
                    }
                    if !message.isEmpty { Text(message).font(.footnote) }
                }
                Section {
                    Text("Ứng dụng khách chỉ sử dụng API công khai. Không nhập khóa quản trị hoặc mật khẩu MoMo tại đây.")
                        .font(.footnote).foregroundColor(.secondary)
                }
            }
            .navigationTitle("Cấu hình")
            .toolbar { ToolbarItem(placement: .navigationBarTrailing) {
                Button("Xong") { dismiss() }
            }}
            .onAppear { draft = api }
        }
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
    @Environment(\.openERISettings) private var openSettings
    let title: String
    let endpoint: String
    @AppStorage("eri_store_api") private var api = ""
    @State private var items: [StoreItem] = []
    @State private var status = "Chưa kết nối máy chủ"
    var body: some View {
        NavigationView {
            List {
                if items.isEmpty { Text(status).foregroundColor(.secondary) }
                ForEach(items) { item in
                    VStack(alignment: .leading, spacing: 6) {
                        Text(item.name).font(.headline)
                        Text(item.description).font(.subheadline)
                        Text(item.price_vnd.formatted(.currency(code: "VND"))).fontWeight(.semibold)
                        Text("Chưa mở đặt hàng tự động").font(.caption).foregroundColor(.secondary)
                    }.padding(.vertical, 5)
                }
            }.navigationTitle(title)
                .toolbar {
                    ToolbarItem(placement: .navigationBarLeading) { Button { openSettings() } label: { Image(systemName: "gearshape") } }
                    ToolbarItem(placement: .navigationBarTrailing) { Button("Làm mới") { Task { await load() } } }
                }
                .task { await load() }
        }
    }
    @MainActor private func load() async {
        guard let url = URL(string: api + "/api/v1/" + endpoint), url.scheme == "https" else {
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
private struct ModItem: Decodable, Identifiable {
    let id: Int
    let name: String
    let version: String
    let bundle_id: String
    let download_url: String
    let sha256: String
}
private struct ModResponse: Decodable { let items: [ModItem] }
private struct ModCatalog: View {
    @Environment(\.openERISettings) private var openSettings
    @AppStorage("eri_store_api") private var api = ""
    @State private var items: [ModItem] = []
    @State private var status = "Chưa kết nối máy chủ"
    var body: some View {
        NavigationView {
            List {
                if items.isEmpty { Text(status).foregroundColor(.secondary) }
                ForEach(items) { item in
                    VStack(alignment: .leading, spacing: 6) {
                        Text(item.name).font(.headline)
                        Text("Phiên bản " + item.version)
                        Text(item.bundle_id).font(.caption).foregroundColor(.secondary)
                        if let url = URL(string: item.download_url), url.scheme == "https" {
                            Link("Tải IPA", destination: url)
                        }
                    }.padding(.vertical, 5)
                }
            }.navigationTitle("Mod Game")
                .toolbar { Button("Làm mới") { Task { await load() } } }
                .task { await load() }
        }
    }
    @MainActor private func load() async {
        guard let url = URL(string: api + "/api/v1/mods"), url.scheme == "https" else {
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
