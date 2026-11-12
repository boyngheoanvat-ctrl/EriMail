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
    @State private var remote = ""
    @State private var downloading = false
    @State private var selectedTool = "ESign"
    @State private var appStoreLink = ""
    @State private var appStoreNotice = ""
    @State private var appName = ""
    @State private var appVersion = ""
    @State private var appBundle = ""
    @State private var lookingUp = false

    private var extensions: [String] {
        switch mode {
        case "certificates": return ["p12", "pfx", "cer", "mobileprovision"]
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
                    Text("Chọn tệp IPA của " + selectedTool + " để xuất hoặc tải IPA từ URL HTTPS trực tiếp.")
                    Button {
                        share = true
                    } label: {
                        Label("Xuất IPA " + selectedTool, systemImage: "square.and.arrow.up")
                    }
                    .disabled(file == nil)
                    Button {
                        message = "Chưa có IPA được ký bằng chứng chỉ hợp lệ. Không thể cài trực tiếp bằng chứng chỉ free không xác minh được."
                    } label: {
                        Label("Cài trực tiếp", systemImage: "iphone")
                    }
                    .disabled(file == nil)
                }
            }
            if mode == "ipa" || mode == "tools" {
                Section("Tải IPA trực tiếp vào Eri Mail") {
                    TextField("https://example.com/app.ipa", text: $remote)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                    Button {
                        Task { await downloadIPA() }
                    } label: {
                        Label(downloading ? "Đang tải..." : "Tải IPA", systemImage: "arrow.down.circle")
                    }
                    .disabled(downloading)
                    if downloading { ProgressView() }
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
                Section("Chứng chỉ miễn phí và chứng chỉ riêng") {
                    Text("Eri Mail không thể đọc khóa riêng .p12 trong vùng dữ liệu ESign hoặc GBox. Hãy xuất/chia sẻ chứng chỉ từ ứng dụng ký để nhập một lần; sau đó mới có thể thiết lập quy trình ký hợp lệ.")
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
                file = target
                message = "Đã nhập tệp vào Eri Mail"
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

    @MainActor
    private func downloadIPA() async {
        guard let url = URL(string: remote.trimmingCharacters(in: .whitespacesAndNewlines)),
              url.scheme?.lowercased() == "https",
              url.path.lowercased().hasSuffix(".ipa"),
              url.user == nil, url.password == nil else {
            message = "Cần URL HTTPS trỏ trực tiếp tới file .ipa"
            return
        }
        downloading = true
        defer { downloading = false }
        do {
            let (temporary, response) = try await URLSession.shared.download(from: url)
            guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
                message = "Máy chủ không trả về IPA hợp lệ"
                return
            }
            let destination = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".ipa")
            try FileManager.default.moveItem(at: temporary, to: destination)
            file = destination
            message = "Đã tải IPA vào Eri Mail. Chưa xác minh chữ ký."
        } catch {
            message = "Lỗi tải: " + error.localizedDescription
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
