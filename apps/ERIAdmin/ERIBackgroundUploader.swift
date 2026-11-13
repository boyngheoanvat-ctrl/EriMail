import Foundation
import Combine
import CryptoKit
import Security
import UIKit

// URLSession background uploads are owned by iOS, not a foreground SwiftUI Task.
// They can continue when the app is suspended or the screen is locked.
// Explicitly force-quitting the app can prevent iOS from resuming these transfers.
private let eriBackgroundHost = "https://eri-api-dag8.onrender.com"
private let eriBackgroundIdentifier = "com.eringuyen.admin.background-upload.v1"
private let eriBackgroundRecordKey = "eri.background.upload.record.v1"

private struct ERIBackgroundRecord: Codable {
    let id: String
    let size: Int
    let chunkBytes: Int
    let partCount: Int
    var nextPart: Int
    var acknowledgedBytes: Int
    var failures: Int
}

private enum ERIBackgroundError: LocalizedError {
    case uploadExists
    case invalidFile
    case keyMissing

    var errorDescription: String? {
        switch self {
        case .uploadExists:
            return "Một tệp đang tải nền. Vui lòng đợi hoàn tất hoặc bấm Tiếp tục để thử lại."
        case .invalidFile:
            return "Không thể chuẩn bị các phần của tệp để tải nền."
        case .keyMissing:
            return "Cần đăng nhập quản trị để tiếp tục tải nền."
        }
    }
}

final class ERIBackgroundUploader: NSObject, ObservableObject, URLSessionTaskDelegate {
    static let shared = ERIBackgroundUploader()

    @Published private(set) var active = false
    @Published private(set) var pending = false
    @Published private(set) var progress = 0.0
    @Published private(set) var status = ""

    private var record: ERIBackgroundRecord?
    private var sending = false
    private var retryBlocked = false
    private var backgroundEventsCompletion: (() -> Void)?

    private lazy var transfers: URLSession = {
        let config = URLSessionConfiguration.background(withIdentifier: eriBackgroundIdentifier)
        config.sessionSendsLaunchEvents = true
        config.isDiscretionary = false
        config.waitsForConnectivity = true
        config.allowsCellularAccess = true
        config.httpMaximumConnectionsPerHost = 1
        return URLSession(configuration: config, delegate: self, delegateQueue: .main)
    }()

    private override init() {
        super.init()
        if let data = UserDefaults.standard.data(forKey: eriBackgroundRecordKey),
           let saved = try? JSONDecoder().decode(ERIBackgroundRecord.self, from: data) {
            record = saved
            pending = true
            active = true
            progress = Double(saved.acknowledgedBytes) / Double(max(saved.size, 1))
            status = "Đã khôi phục phiên upload nền; đang kiểm tra với iOS..."
        }
    }

    private static func jobFolder(_ id: String) throws -> URL {
        guard id.range(of: "^[a-f0-9]{32}$", options: .regularExpression) != nil else {
            throw ERIBackgroundError.invalidFile
        }
        let support = try FileManager.default.url(for: .applicationSupportDirectory,
                                                  in: .userDomainMask, appropriateFor: nil,
                                                  create: true)
        return support.appendingPathComponent("ERIBackgroundUploads", isDirectory: true)
            .appendingPathComponent(id, isDirectory: true)
    }

    private static func partName(_ index: Int) -> String {
        String(format: "%06d", index)
    }

    private static func prepareParts(source: URL, id: String, size: Int, chunkBytes: Int) throws {
        let folder = try jobFolder(id)
        let manager = FileManager.default
        try manager.createDirectory(at: folder, withIntermediateDirectories: true)
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        var mutableFolder = folder
        try mutableFolder.setResourceValues(values)

        let reader = try FileHandle(forReadingFrom: source)
        defer { try? reader.close() }
        let total = (size + chunkBytes - 1) / chunkBytes
        for i in 0..<total {
            let expected = min(chunkBytes, size - i * chunkBytes)
            guard let data = try reader.read(upToCount: expected),
                  data.count == expected else { throw ERIBackgroundError.invalidFile }
            let stem = partName(i)
            let partURL = folder.appendingPathComponent(stem + ".part")
            try data.write(to: partURL, options: .atomic)
            try manager.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication],
                                      ofItemAtPath: partURL.path)
            let hash = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
            try hash.write(to: folder.appendingPathComponent(stem + ".sha256"),
                           atomically: true, encoding: .utf8)
        }
        let empty = folder.appendingPathComponent("complete.body")
        try Data().write(to: empty)
        try manager.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication],
                                  ofItemAtPath: empty.path)
    }

    private func persist() {
        if let record, let bytes = try? JSONEncoder().encode(record) {
            UserDefaults.standard.set(bytes, forKey: eriBackgroundRecordKey)
        } else {
            UserDefaults.standard.removeObject(forKey: eriBackgroundRecordKey)
        }
    }

    // Called by the app delegate on launch, including iOS relaunch for transfer events.
    func restore() {
        _ = transfers
        guard record != nil else { return }
        transfers.getAllTasks { tasks in
            DispatchQueue.main.async {
                if tasks.isEmpty {
                    self.sending = false
                    if !self.retryBlocked { self.scheduleNext() }
                } else {
                    self.sending = true
                    self.active = true
                    self.pending = true
                    self.status = "iOS đang tiếp tục upload trong nền"
                }
            }
        }
    }

    func setBackgroundEventsCompletion(_ handler: @escaping () -> Void) {
        backgroundEventsCompletion = handler
        restore()
    }

    func start(source: URL, uploadID: String, size: Int, chunkBytes: Int) async throws {
        guard record == nil else { throw ERIBackgroundError.uploadExists }
        guard size > 0, chunkBytes > 0, chunkBytes <= 4 * 1024 * 1024 else {
            throw ERIBackgroundError.invalidFile
        }
        let parts = (size + chunkBytes - 1) / chunkBytes
        status = "Chuẩn bị tệp để iOS có thể tải tiếp khi bạn rời ứng dụng..."
        try await Task.detached(priority: .userInitiated) {
            try Self.prepareParts(source: source, id: uploadID, size: size, chunkBytes: chunkBytes)
        }.value

        record = ERIBackgroundRecord(id: uploadID, size: size, chunkBytes: chunkBytes,
                                     partCount: parts, nextPart: 0,
                                     acknowledgedBytes: 0, failures: 0)
        progress = 0
        pending = true
        active = true
        retryBlocked = false
        persist()
        scheduleNext()
    }

    func retry() {
        guard var current = record else { return }
        current.failures = 0
        record = current
        retryBlocked = false
        active = true
        status = "Đang tiếp tục upload nền..."
        persist()
        restore()
    }

    private static func storedAdminKey() -> String {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "vn.eri.admin",
            kSecAttrAccount as String: "eri_admin_token",
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else { return "" }
        return String(data: data, encoding: .utf8) ?? ""
    }

    private func scheduleNext() {
        guard !sending, !retryBlocked, let current = record else { return }
        let key = Self.storedAdminKey()
        guard key.count >= 32 else {
            active = false
            retryBlocked = true
            status = ERIBackgroundError.keyMissing.localizedDescription
            return
        }
        do {
            let folder = try Self.jobFolder(current.id)
            let complete = current.nextPart == current.partCount
            let path = "/api/v1/admin/upload-sessions/" + current.id
            let suffix = complete ? "/complete" :
                "/fast-parts/" + String(current.nextPart)
            guard let url = URL(string: eriBackgroundHost + path + suffix) else {
                throw URLError(.badURL)
            }
            var request = URLRequest(url: url)
            request.httpMethod = "POST"
            request.setValue(key, forHTTPHeaderField: "X-ERI-Admin-Key")
            request.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
            request.timeoutInterval = 180
            let file: URL
            if complete {
                file = folder.appendingPathComponent("complete.body")
            } else {
                let stem = Self.partName(current.nextPart)
                file = folder.appendingPathComponent(stem + ".part")
                let digest = try String(contentsOf:
                    folder.appendingPathComponent(stem + ".sha256"), encoding: .utf8)
                request.setValue(digest, forHTTPHeaderField: "X-ERI-Chunk-SHA256")
            }
            guard FileManager.default.fileExists(atPath: file.path) else {
                throw ERIBackgroundError.invalidFile
            }
            let upload = transfers.uploadTask(with: request, fromFile: file)
            upload.taskDescription = complete ? "complete" : "part:" + String(current.nextPart)
            sending = true
            active = true
            status = complete
                ? "Máy chủ đang kiểm tra và đăng sản phẩm..."
                : "iOS đang tải nền: phần " + String(current.nextPart + 1) +
                    "/" + String(current.partCount)
            upload.resume()
        } catch {
            retryBlocked = true
            active = false
            status = "Không thể tiếp tục tải nền: " + error.localizedDescription
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    didSendBodyData bytesSent: Int64,
                    totalBytesSent: Int64,
                    totalBytesExpectedToSend: Int64) {
        guard let current = record, task.taskDescription?.hasPrefix("part:") == true,
              totalBytesExpectedToSend > 0 else { return }
        // Informational progress while bytes are on the wire; the server still
        // must acknowledge the entire chunk before the checkpoint advances.
        let display = Double(current.acknowledgedBytes) + Double(totalBytesSent)
        progress = min(0.999, display / Double(current.size))
    }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    didCompleteWithError error: Error?) {
        guard var current = record else { return }
        sending = false
        let code = (task.response as? HTTPURLResponse)?.statusCode ?? 0
        let isComplete = task.taskDescription == "complete"
        let expectedStatus = isComplete ? 201 : 200

        if error == nil && code == expectedStatus {
            if isComplete {
                let id = current.id
                record = nil
                pending = false
                active = false
                progress = 1.0
                status = "Đã upload 100% và cập nhật sản phẩm lên máy chủ."
                persist()
                Task.detached(priority: .utility) {
                    if let folder = try? Self.jobFolder(id) {
                        try? FileManager.default.removeItem(at: folder)
                    }
                }
                return
            }

            if task.taskDescription == "part:" + String(current.nextPart) {
                current.acknowledgedBytes += min(current.chunkBytes,
                    current.size - current.nextPart * current.chunkBytes)
                current.nextPart += 1
                current.failures = 0
                record = current
                progress = Double(current.acknowledgedBytes) / Double(current.size)
                persist()
                scheduleNext()
                return
            }
        }

        // A missing HTTP response can happen even when the server committed the
        // chunk. The endpoint is idempotent for the same SHA-256 checksum.
        current.failures += 1
        record = current
        progress = Double(current.acknowledgedBytes) / Double(current.size)
        persist()
        if current.failures <= 3 {
            status = "Kết nối gián đoạn, iOS đang gửi lại phần chưa xác nhận..."
            scheduleNext()
        } else {
            retryBlocked = true
            active = false
            status = "Tạm dừng upload (HTTP " + String(code) + "): " +
                (error?.localizedDescription ?? "Máy chủ chưa xác nhận") +
                ". Mở ERI ADMIN và bấm Tiếp tục."
        }
    }

    func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
        let completion = backgroundEventsCompletion
        backgroundEventsCompletion = nil
        DispatchQueue.main.async { completion?() }
    }
}

final class ERIBackgroundAppDelegate: NSObject, UIApplicationDelegate {
    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        ERIBackgroundUploader.shared.restore()
        return true
    }

    func application(_ application: UIApplication,
                     handleEventsForBackgroundURLSession identifier: String,
                     completionHandler: @escaping () -> Void) {
        guard identifier == eriBackgroundIdentifier else {
            completionHandler()
            return
        }
        ERIBackgroundUploader.shared.setBackgroundEventsCompletion(completionHandler)
    }
}
