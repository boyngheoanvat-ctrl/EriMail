import SwiftUI

@main
struct ERIStoreApp: App {
    var body: some Scene { WindowGroup { StoreHome() } }
}

private struct StoreHome: View {
    var body: some View {
        TabView {
            StoreCatalog(title: "AI Plus", endpoint: "products")
                .tabItem { Label("AI Plus", systemImage: "sparkles") }
            StoreCatalog(title: "Chứng chỉ", endpoint: "certificates")
                .tabItem { Label("Chứng chỉ", systemImage: "checkmark.seal") }
            ModCatalog()
                .tabItem { Label("Mod Game", systemImage: "gamecontroller") }
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
                .toolbar { Button("Làm mới") { Task { await load() } } }
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
private struct ModCatalog: View {
    var body: some View {
        NavigationView {
            List {
                Text("IPA mod game").font(.headline)
                Text("Chỉ phân phối IPA có quyền chia sẻ. Danh mục và cập nhật được quản lý qua ERI ADMIN.")
                    .foregroundColor(.secondary)
                Text("Chưa có bản IPA được xác minh để tải.")
            }.navigationTitle("Mod Game")
        }
    }
}
