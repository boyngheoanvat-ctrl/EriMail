import SwiftUI
import Security
import UIKit

// Native SwiftUI management for ERI Login keys. Not a Safari/WebView interface.
// Uses a separate login-admin credential, never the ERI STORE admin key.
private let licenseBaseURL = "https://eri-login-api.onrender.com"

private enum LicenseSecureStore {
    private static let service = "com.eringuyen.admin.license"
    private static let account = "eri_login_admin_key"

    static func read() -> String {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else { return "" }
        return String(data: data, encoding: .utf8) ?? ""
    }

    static func save(_ value: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account
        ]
        SecItemDelete(query as CFDictionary)
        guard !value.isEmpty, let data = value.data(using: .utf8) else { return }
        var attributes = query
        attributes[kSecValueData as String] = data
        attributes[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        SecItemAdd(attributes as CFDictionary, nil)
    }
}

private struct ERILicenseAccount: Decodable, Identifiable {
    let username: String
    let expires_at: String
    let disabled: Bool
    let expired: Bool
    var id: String { username }
}

private struct ERILicenseAccounts: Decodable {
    let users: [ERILicenseAccount]
}

private struct ERILicenseResponse: Decodable {
    let error: String?
}

private enum ERILicenseError: LocalizedError {
    case message(String)
    var errorDescription: String? {
        switch self {
        case .message(let value): return value
        }
    }
}

@MainActor
private final class ERILicenseStore: ObservableObject {
    @Published var adminKey = LicenseSecureStore.read()
    @Published var appID = UserDefaults.standard.string(forKey: "eri.license.appID") ?? "com.garena.game.kgvo"
    @Published var username = ""
    @Published var password = ""
    @Published var days = "30"
    @Published var accounts: [ERILicenseAccount] = []
    @Published var message = "Nhập khóa ERI Login và xác thực để quản lý."
    @Published var busy = false
    @Published var verified = false
    @Published var lastCreatedCredentials = ""

    private func request(_ path: String, method: String = "GET",
                         data: [String: Any]? = nil) async throws -> Data {
        guard adminKey.count >= 32 else {
            throw ERILicenseError.message("Nhập ERI_ADMIN_KEY của ERI Login (ít nhất 32 ký tự).")
        }
        guard var parts = URLComponents(string: licenseBaseURL + path),
              parts.scheme == "https" else {
            throw ERILicenseError.message("Địa chỉ máy chủ không hợp lệ.")
        }
        if path == "/api/admin/list" {
            parts.queryItems = [URLQueryItem(name: "app_id", value: appID.trimmingCharacters(in: .whitespaces))]
        }
        guard let url = parts.url else { throw ERILicenseError.message("URL không hợp lệ.") }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.timeoutInterval = 90
        req.setValue(adminKey, forHTTPHeaderField: "X-ERI-ADMIN-KEY")
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        if let data {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONSerialization.data(withJSONObject: data)
        }
        let (body, response) = try await URLSession.shared.data(for: req)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200...299).contains(status) else {
            let parsed = try? JSONDecoder().decode(ERILicenseResponse.self, from: body)
            let description = parsed?.error ?? "Máy chủ trả về HTTP \(status)."
            throw ERILicenseError.message(description)
        }
        return body
    }

    private func validAppID() -> Bool {
        appID.range(of: "^[A-Za-z0-9_.-]{2,128}$", options: .regularExpression) != nil
    }

    private func validUsername() -> Bool {
        username.range(of: "^[A-Za-z0-9_.@-]{3,64}$", options: .regularExpression) != nil
    }

    private func daysNumber() -> Int? {
        guard let n = Int(days), (1...3650).contains(n) else { return nil }
        return n
    }

    private func doList() async throws {
        guard validAppID() else { throw ERILicenseError.message("App ID không hợp lệ.") }
        let result = try await request("/api/admin/list")
        let parsed = try JSONDecoder().decode(ERILicenseAccounts.self, from: result)
        accounts = parsed.users
        UserDefaults.standard.set(appID, forKey: "eri.license.appID")
    }

    func verify() async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        do {
            try await doList()
            LicenseSecureStore.save(adminKey)
            verified = true
            message = "Đã kết nối ERI Login. Có \(accounts.count) tài khoản trong App ID này."
        } catch {
            verified = false
            message = "Lỗi xác thực: " + error.localizedDescription
        }
    }

    func signOut() {
        LicenseSecureStore.save("")
        adminKey = ""
        verified = false
        accounts = []
        lastCreatedCredentials = ""
        message = "Đã xóa khóa ERI Login khỏi thiết bị."
    }

    private func randomString(_ length: Int) -> String {
        let alphabet = Array("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
        var generator = SystemRandomNumberGenerator()
        return String((0..<length).map { _ in
            alphabet[Int.random(in: 0..<alphabet.count, using: &generator)]
        })
    }

    func createRandom() async {
        username = "ERI-" + randomString(12)
        password = randomString(20)
        await execute("create")
    }

    func execute(_ action: String) async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        do {
            guard validAppID() else {
                throw ERILicenseError.message("App ID không hợp lệ.")
            }
            guard validUsername() else {
                throw ERILicenseError.message("Tên/key phải dài 3–64 ký tự (chữ, số, _ . @ -).")
            }
            var payload: [String: Any] = ["app_id": appID, "username": username]
            if action == "create" {
                guard password.count >= 8 else {
                    throw ERILicenseError.message("Mật khẩu tối thiểu 8 ký tự.")
                }
                guard let n = daysNumber() else {
                    throw ERILicenseError.message("Số ngày phải từ 1–3650.")
                }
                payload["password"] = password
                payload["days"] = n
            } else if action == "extend" {
                guard let n = daysNumber() else {
                    throw ERILicenseError.message("Số ngày phải từ 1–3650.")
                }
                payload["days"] = n
            }
            _ = try await request("/api/admin/" + action, method: "POST", data: payload)
            if action == "create" {
                lastCreatedCredentials = "App ID: \(appID)\nTài khoản/Key: \(username)\nMật khẩu: \(password)"
            } else {
                lastCreatedCredentials = ""
            }
            try await doList()
            LicenseSecureStore.save(adminKey)
            verified = true
            let names: [String: String] = [
                "create": "Tạo tài khoản/key",
                "extend": "Gia hạn",
                "disable": "Khóa",
                "enable": "Mở khóa",
                "delete": "Xóa"
            ]
            message = "\(names[action] ?? action) thành công: \(username)"
        } catch {
            message = "Lỗi: " + error.localizedDescription
        }
    }
}

struct ERILicenseAdminView: View {
    @StateObject private var model = ERILicenseStore()
    @State private var deleteConfirm = false

    var body: some View {
        Form {
            Section {
                Label("Tạo và gia hạn key ngay trong ERI ADMIN IPA", systemImage: "key.horizontal")
                    .font(.subheadline)
                Text("Máy chủ: \(licenseBaseURL)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Kết nối ERI Login") {
                TextField("App ID", text: $model.appID)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                SecureField("ERI_ADMIN_KEY của ERI Login", text: $model.adminKey)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                Button {
                    Task { await model.verify() }
                } label: {
                    Label(model.busy ? "Đang kết nối..." : "Xác thực / Tải danh sách", systemImage: "checkmark.shield")
                }
                .disabled(model.busy)
                Button("Đăng xuất / Xóa khóa đã lưu", role: .destructive) {
                    model.signOut()
                }
                Text("Khóa được lưu riêng trong iOS Keychain, không nhúng vào ứng dụng.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }

            Section("Tạo tài khoản / Key mới") {
                TextField("Tên tài khoản hoặc Key", text: $model.username)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                SecureField("Mật khẩu mới (ít nhất 8 ký tự)", text: $model.password)
                HStack {
                    Text("Số ngày")
                    Spacer()
                    TextField("30", text: $model.days)
                        .keyboardType(.numberPad)
                        .multilineTextAlignment(.trailing)
                        .frame(width: 100)
                }
                Button {
                    Task { await model.execute("create") }
                } label: {
                    Label("Tạo tài khoản + mật khẩu", systemImage: "person.badge.plus")
                }
                .disabled(model.busy)
                Button {
                    Task { await model.createRandom() }
                } label: {
                    Label("Tạo Key + mật khẩu ngẫu nhiên", systemImage: "key.fill")
                }
                .disabled(model.busy)
            }

            Section("Quản lý key đang chọn") {
                Button {
                    Task { await model.execute("extend") }
                } label: { Label("Gia hạn thêm số ngày", systemImage: "calendar.badge.plus") }
                    .disabled(model.busy)
                Button {
                    Task { await model.execute("disable") }
                } label: { Label("Khóa tài khoản/key", systemImage: "lock") }
                    .disabled(model.busy)
                Button {
                    Task { await model.execute("enable") }
                } label: { Label("Mở khóa tài khoản/key", systemImage: "lock.open") }
                    .disabled(model.busy)
                Button(role: .destructive) {
                    deleteConfirm = true
                } label: { Label("Xóa key/tài khoản", systemImage: "trash") }
                    .disabled(model.busy)
            }

            if !model.lastCreatedCredentials.isEmpty {
                Section("Thông tin vừa tạo") {
                    Text(model.lastCreatedCredentials)
                        .font(.system(.footnote, design: .monospaced))
                        .textSelection(.enabled)
                    Button {
                        UIPasteboard.general.string = model.lastCreatedCredentials
                    } label: { Label("Sao chép tài khoản + mật khẩu", systemImage: "doc.on.doc") }
                    Text("Chỉ sao chép khi cần; bộ nhớ tạm có thể được ứng dụng khác đọc.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }

            Section("Kết quả") {
                Text(model.message).font(.callout)
                if model.busy { ProgressView() }
            }

            Section("Danh sách key online (\(model.accounts.count))") {
                ForEach(model.accounts) { account in
                    Button {
                        model.username = account.username
                    } label: {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(account.username).font(.headline)
                            Text("Hết hạn: \(String(account.expires_at.prefix(10)))")
                                .font(.caption).foregroundStyle(.secondary)
                            if account.disabled {
                                Text("Đã khóa").font(.caption).foregroundStyle(.red)
                            } else if account.expired {
                                Text("Đã hết hạn").font(.caption).foregroundStyle(.orange)
                            } else {
                                Text("Còn hiệu lực").font(.caption).foregroundStyle(.green)
                            }
                        }
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .navigationTitle("Key ERI Login")
        .confirmationDialog("Xóa tài khoản/key \(model.username)?",
                            isPresented: $deleteConfirm, titleVisibility: .visible) {
            Button("Xóa vĩnh viễn", role: .destructive) {
                Task { await model.execute("delete") }
            }
            Button("Hủy", role: .cancel) { }
        } message: {
            Text("Thao tác này cũng thu hồi toàn bộ phiên đăng nhập của tài khoản.")
        }
    }
}
