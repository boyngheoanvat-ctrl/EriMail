import SwiftUI
import Foundation
import Security

// Standalone working proof of concept for licensed apps.
// This app does NOT open or modify any third-party game.
private enum SecureKeychain {
    static func read(_ account: String) -> String {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "vn.eri.login.demo.v1",
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else { return "" }
        return String(data: data, encoding: .utf8) ?? ""
    }

    static func save(_ account: String, _ value: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "vn.eri.login.demo.v1",
            kSecAttrAccount as String: account
        ]
        let update: [String: Any] = [kSecValueData as String: Data(value.utf8)]
        if SecItemUpdate(query as CFDictionary, update as CFDictionary) == errSecItemNotFound {
            var new = query
            new[kSecValueData as String] = Data(value.utf8)
            new[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            _ = SecItemAdd(new as CFDictionary, nil)
        }
    }

    static func delete(_ account: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "vn.eri.login.demo.v1",
            kSecAttrAccount as String: account
        ]
        _ = SecItemDelete(query as CFDictionary)
    }
}

struct ERIValidation: Decodable {
    let valid: Bool
    let expires_at: String
    let device_bound: Bool
}
struct ERIError: Decodable { let error: String? }

@MainActor
final class ERILoginModel: ObservableObject {
    @Published var keyInput: String = ""
    @Published var message: String = "Nhập key được cấp từ ERI Key Admin."
    @Published var expires: String = ""
    @Published var authenticated = false
    @Published var busy = false

    private let api = "https://eri-login-api.onrender.com"
    private let appId = "vn.eri.login.demo"
    private let licenseAccount = "license-key"
    private let deviceAccount = "device-id"

    init() {
        keyInput = SecureKeychain.read(licenseAccount)
    }

    private var deviceId: String {
        let prior = SecureKeychain.read(deviceAccount)
        if UUID(uuidString: prior) != nil {
            return prior.lowercased()
        }
        let next = UUID().uuidString.lowercased()
        SecureKeychain.save(deviceAccount, next)
        return next
    }

    func verifySavedOnLaunch() async {
        guard !keyInput.isEmpty else { return }
        await validate(activate: false)
    }

    func activate() async { await validate(activate: true) }

    private func validate(activate: Bool) async {
        guard !busy else { return }
        let code = keyInput.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !code.isEmpty else {
            message = "Chưa nhập key."
            return
        }

        busy = true
        defer { busy = false }
        authenticated = false
        message = "Đang kiểm tra key và thiết bị..."
        do {
            let endpoint = activate ? "/api/key/activate" : "/api/key/verify"
            guard let url = URL(string: api + endpoint) else { throw URLError(.badURL) }
            var request = URLRequest(url: url)
            request.httpMethod = "POST"
            request.timeoutInterval = 95
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: [
                "app_id": appId, "key": code, "device_id": deviceId
            ])
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let response = response as? HTTPURLResponse else { throw URLError(.badServerResponse) }
            guard (200...299).contains(response.statusCode) else {
                let reason = (try? JSONDecoder().decode(ERIError.self, from: data).error)
                    ?? "HTTP \(response.statusCode)"
                message = reason
                return
            }
            let decoded = try JSONDecoder().decode(ERIValidation.self, from: data)
            guard decoded.valid else {
                message = "Xác minh key không thành công."
                return
            }
            SecureKeychain.save(licenseAccount, code)
            expires = decoded.expires_at
            authenticated = true
            message = "Đăng nhập thành công!"
        } catch {
            message = "Không thể kết nối API: \(error.localizedDescription)"
        }
    }

    func logout() {
        SecureKeychain.delete(licenseAccount)
        keyInput = ""
        expires = ""
        authenticated = false
        message = "Đã thoát."
    }
}

struct ERILoginScreen: View {
    @StateObject private var model = ERILoginModel()
    var body: some View {
        NavigationStack {
            VStack(spacing: 20) {
                Image(systemName: model.authenticated ? "checkmark.shield.fill" : "lock.shield")
                    .font(.system(size: 56)).foregroundStyle(model.authenticated ? Color.green : Color.blue)
                Text("ERI LOGIN").font(.system(size: 30, weight: .bold))
                Text("1 KEY / 1 THIẾT BỊ").font(.headline).foregroundStyle(.secondary)
                if model.authenticated {
                    Text("KEY HỢP LỆ").font(.title2.bold()).foregroundStyle(.green)
                    Text("Hạn dùng: \(model.expires)").font(.footnote).textSelection(.enabled)
                    Text("Đây là màn hình minh họa tích hợp Login, không phải game đã chỉnh sửa.")
                        .multilineTextAlignment(.center).font(.footnote)
                    Button("Đăng xuất / nhập key khác") { model.logout() }
                        .buttonStyle(.bordered)
                } else {
                    TextField("ERI-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX", text: $model.keyInput)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                        .textFieldStyle(.roundedBorder)
                    Button("KÍCH HOẠT KEY") { Task { await model.activate() } }
                        .buttonStyle(.borderedProminent).disabled(model.busy)
                    ProgressView().opacity(model.busy ? 1 : 0)
                }
                Text(model.message).multilineTextAlignment(.center).foregroundStyle(.secondary)
                Spacer()
            }
            .padding(24)
            .navigationTitle("ERI License Demo")
            .task { await model.verifySavedOnLaunch() }
        }
    }
}

@main struct ERILoginDemoApp: App {
    var body: some Scene { WindowGroup { ERILoginScreen() } }
}