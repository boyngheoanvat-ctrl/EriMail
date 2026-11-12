import SwiftUI
import UniformTypeIdentifiers
import UIKit

// Native file operations. No third-party web pages or private-key uploads.
struct EriLocalFilesView: View {
    let mode: String
    @State private var picker = false
    @State private var file: URL?
    @State private var message = "Chưa chọn tệp"
    @State private var share = false
    @State private var selectedTool = "ESign"
    @State private var appStoreLink = ""
    @State private var appStoreNotice = ""
    @State private var appName = ""
    @State private var appVersion = ""
    @State private var appBundle = ""
    @State private var lookingUp = false
    @State private var certificateArchive: URL?
    @State private var certificatePassword = ""

    private var extensions: [String] {
        switch mode {
        case "certificates": return ["zip", "p12", "pfx", "cer", "mobileprovision"]
        case "inject": return ["ipa", "dylib", "deb"]
        default: return ["ipa"]
        }
    }

    var body: some View {
        Form {
            Section(mode == "certificates" ? "Chứng chỉ trên thiết bị" : mode == "inject" ? "Tệp IPA / Dylib" : "Quản lý IPA trên thiết bị") {
                Button {
                    picker = true
                } label: {
                    Label("Nhập tệp từ ứng dụng Tệp", systemImage: "folder.badge.plus")
                }
                Text(message).font(.footnote).foregroundColor(.secondary)
                if let file {
                    Text(file.lastPathComponent).font(.headline)
                    Button {
                        share = true
                    } label: {
                        Label("Xuất / Lưu vào Tệp", systemImage: "square.and.arrow.up")
                    }
                }
            }
            if mode == "ipa" {
                Section("Nhập liên kết App Store") {
                    TextField("https://apps.apple.com/vn/app/.../id6757736102", text: $appStoreLink)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                    Button {
                        Task { await lookupAppStore() }
                    } label: {
                        Label(lookingUp ? "Đang tìm ứng dụng..." : "Tìm ứng dụng", systemImage: "magnifyingglass")
                    }
                    .disabled(lookingUp)
                    if lookingUp { ProgressView() }
                    if !appName.isEmpty {
                        HStack { Text("Tên ứng dụng"); Spacer(); Text(appName).foregroundColor(.secondary) }
                        HStack { Text("Phiên bản"); Spacer(); Text(appVersion).foregroundColor(.secondary) }
                        HStack { Text("Bundle ID"); Spacer(); Text(appBundle).foregroundColor(.secondary) }
                    }
                    Text(appStoreNotice).font(.footnote).foregroundColor(.secondary)
                }
            }
            if mode == "tools" {
                Section("Chọn công cụ") {
                    Picker("Công cụ", selection: $selectedTool) {
                        ForEach(["ESign", "KSign", "Feather", "GBox", "Scarlet"], id: \.self) { Text($0) }
                    }
                    Text("Chọn IPA của " + selectedTool + " từ ứng dụng Tệp rồi chia sẻ sang ứng dụng ký.")
                    Button {
                        share = true
                    } label: {
                        Label("Xuất IPA " + selectedTool, systemImage: "square.and.arrow.up")
                    }
                    .disabled(file == nil)
                    Button {
                        message = "Mở ứng dụng ký đã cài để ký IPA trước khi cài."
                        share = true
                    } label: {
                        Label("Mở bằng ứng dụng ký / cài", systemImage: "iphone")
                    }
                    .disabled(file == nil)
                }
            }
            if mode == "tools" {
                Section("ESign · KSign · Feather · GBox · Scarlet") {
                    Text("Nhập tệp IPA của công cụ từ nguồn bạn tin cậy để lưu và chia sẻ trực tiếp trong Eri Mail. Không mở website trung gian.")
                    Text("Ký IPA yêu cầu chứng chỉ, provisioning profile và bộ xử lý chữ ký hợp lệ. Chức năng ký thực tế chưa được tích hợp; không hiển thị kết quả ký giả.")
                        .font(.footnote).foregroundColor(.secondary)
                }
            }
            if mode == "certificates" {
                Section("Gói chứng chỉ ZIP") {
                    Text("Nhập ZIP chứa cert.p12, cert.mobileprovision và pass.txt.")
                    if let certificateArchive {
                        HStack { Text("Đã nhập"); Spacer(); Text(certificateArchive.lastPathComponent).foregroundColor(.secondary) }
                        Text("ZIP đã được lưu; chưa giải nén hoặc xác minh chữ ký.").font(.footnote).foregroundColor(.secondary)
                    }
                    SecureField("Mật khẩu P12 (nếu cần)", text: $certificatePassword)
                    Text("iOS không cho phép tự lấy khóa riêng từ ESign/GBox.").font(.footnote).foregroundColor(.secondary)
                }
                Section("Bảo mật") {
                    Text("Tệp chứng chỉ chỉ được sao chép vào vùng tạm của ứng dụng. Không tải khóa riêng hoặc mật khẩu lên dịch vụ ngoài. Chưa hỗ trợ ký bằng chứng chỉ ngay trên máy.")
                        .font(.footnote).foregroundColor(.secondary)
                }
            }
            if mode == "inject" {
                Section("Inject Dylib") {
                    Text("Có thể nhập IPA, dylib hoặc deb từ Tệp và xuất lại tệp đã nhập. Chưa có bộ chỉnh sửa Mach-O và tái ký IPA; không tuyên bố đã inject khi chưa xử lý thực tế.")
                        .font(.footnote).foregroundColor(.secondary)
                }
            }
        }
        .fileImporter(isPresented: $picker, allowedContentTypes: [.data, .archive], allowsMultipleSelection: false) { result in
            do {
                guard let source = try result.get().first else { return }
                guard extensions.contains(source.pathExtension.lowercased()) else {
                    message = "Chỉ chấp nhận: " + extensions.joined(separator: ", ")
                    return
                }
                let access = source.startAccessingSecurityScopedResource()
                defer { if access { source.stopAccessingSecurityScopedResource() } }
                let target = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + "-" + source.lastPathComponent)
                try FileManager.default.copyItem(at: source, to: target)
                if mode == "certificates" {
                    certificateArchive = target.pathExtension.lowercased() == "zip" ? target : nil
                    message = "Đã nhập gói chứng chỉ"
                } else {
                    file = target
                    message = "Đã nhập IPA vào Eri Mail"
                }
            } catch {
                message = error.localizedDescription
            }
        }
        .sheet(isPresented: $share) {
            if let file { EriLocalShare(items: [file]) }
        }
    }

    @MainActor
    private func lookupAppStore() async {
        appName = ""
        appVersion = ""
        appBundle = ""
        let link = appStoreLink.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let input = URL(string: link), input.scheme?.lowercased() == "https",
              ["apps.apple.com", "itunes.apple.com"].contains(input.host?.lowercased() ?? ""),
              let range = input.path.range(of: "id[0-9]+", options: .regularExpression) else {
            appStoreNotice = "Link App Store không hợp lệ."
            return
        }
        let appID = String(input.path[range].dropFirst(2))
        let region = input.pathComponents.dropFirst().first ?? "vn"
        var components = URLComponents(string: "https://itunes.apple.com/lookup")!
        components.queryItems = [
            URLQueryItem(name: "id", value: appID),
            URLQueryItem(name: "country", value: region.count == 2 ? region : "vn")
        ]
        guard let url = components.url else { return }
        lookingUp = true
        defer { lookingUp = false }
        do {
            let (data, response) = try await URLSession.shared.data(from: url)
            guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode),
                  let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let result = (json["results"] as? [[String: Any]])?.first else {
                appStoreNotice = "Không tìm thấy ứng dụng trong khu vực này."
                return
            }
            appName = result["trackName"] as? String ?? "Không rõ"
            appVersion = result["version"] as? String ?? "Không rõ"
            appBundle = result["bundleId"] as? String ?? "Không rõ"
            appStoreNotice = "Đã lấy thông tin từ App Store. Để xuất IPA cần nguồn IPA được cấp quyền hoặc dịch vụ xử lý được cấu hình; link App Store không chứa IPA."
        } catch {
            appStoreNotice = "Không thể truy vấn App Store: " + error.localizedDescription
        }
    }

}

struct EriLocalShare: UIViewControllerRepresentable {
    let items: [Any]
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }
    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}
