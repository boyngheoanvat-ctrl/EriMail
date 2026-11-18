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
    var id: String
    let size: Int
    let chunkBytes: Int
    let partCount: Int
    var nextPart: Int
    var acknowledgedBytes: Int
    var failures: Int
    // Only non-secret product fields. No private key or admin key is persisted.
    var metadata: Data?
    var renewals: Int?
    // Sparse acknowledgments support background transfers completing out of order.
    // Optional to restore records created by prior releases.
    var acknowledgedParts: [Int]?
}

private enum ERIBackgroundError: LocalizedError {
    case uploadExists
    case invalidFile
    case insufficientSpace
    case keyMissing

    var errorDescription: String? {
        switch self {
        case .uploadExists:
            return "Một tệp đang tải nền. Vui lòng đợi hoàn tất hoặc bấm Tiếp tục để thử lại."
        case .invalidFile:
            return "Không thể chuẩn bị các phần của tệp để tải nền."
        case .insufficientSpace:
            return "iPhone không đủ chỗ trống để tạo các phần tải nền. Hãy dọn dung lượng rồi chọn lại."
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
    private var scheduling = false
    private var checkingServer = false
    private var renewing = false
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
        var prepared = false
        defer {
            if !prepared { try? manager.removeItem(at: folder) }
        }
        // Background URLSession requires separate on-disk chunk files.
        // Check real free space before making a second copy of the IPA.
        let fs = try manager.attributesOfFileSystem(forPath: folder.path)
        if let free = fs[.systemFreeSize] as? NSNumber,
           free.int64Value < Int64(size) + 128 * 1024 * 1024 {
            throw ERIBackgroundError.insufficientSpace
        }
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
        prepared = true
    }

    private func persist() {
        if let record, let bytes = try? JSONEncoder().encode(record) {
            UserDefaults.standard.set(bytes, forKey: eriBackgroundRecordKey)
        } else {
            UserDefaults.standard.removeObject(forKey: eriBackgroundRecordKey)
        }
    }

    private func receivedIndices(_ value: ERIBackgroundRecord) -> Set<Int> {
        // Old records contain only an in-order nextPart checkpoint.
        let parts = value.acknowledgedParts ??
            Array(0..<max(0, min(value.nextPart, value.partCount)))
        return Set(parts.filter { $0 >= 0 && $0 < value.partCount })
    }

    private func receivedBytes(_ value: ERIBackgroundRecord, _ parts: Set<Int>) -> Int {
        parts.reduce(0) { size, index in
            size + min(value.chunkBytes, value.size - index * value.chunkBytes)
        }
    }

    // Called by the app delegate on launch, including iOS relaunch for transfer events.
    func restore() {
        _ = transfers
        guard record != nil else { return }
        transfers.getAllTasks { tasks in
            DispatchQueue.main.async {
                guard self.record != nil else { return }
                self.sending = !tasks.isEmpty
                self.pending = true
                self.active = true
                if tasks.isEmpty {
                    // Explicit force-quit cancels iOS transfers. On reopening,
                    // ask the server which parts were committed before retrying.
                    self.status = "Đang đối chiếu các phần đã lưu trên máy chủ..."
                    Task { await self.reconcileServer() }
                } else {
                    self.status = "iOS đang tải IPA trong nền"
                    // Prequeue unsubmitted parts while the app can still run.
                    self.scheduleNext()
                }
            }
        }
    }

    private func reconcileServer() async {
        guard !checkingServer, var current = record else { return }
        checkingServer = true
        defer { checkingServer = false }
        do {
            let key = Self.storedAdminKey()
            guard key.count >= 32 else { throw ERIBackgroundError.keyMissing }
            guard let url = URL(string: eriBackgroundHost +
                "/api/v1/admin/upload-sessions/" + current.id + "/fast-parts") else {
                throw URLError(.badURL)
            }
            var request = URLRequest(url: url)
            request.setValue(key, forHTTPHeaderField: "X-ERI-Admin-Key")
            request.timeoutInterval = 60
            let (data, response) = try await URLSession.shared.data(for: request)
            let http = (response as? HTTPURLResponse)?.statusCode ?? 0
            if http == 404 {
                await renewMissingSession()
                return
            }
            guard http == 200,
                let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                let parts = json["received"] as? [Int],
                json["total_parts"] as? Int == current.partCount,
                json["chunk_bytes"] as? Int == current.chunkBytes,
                json["size_bytes"] as? Int == current.size else {
                throw URLError(.badServerResponse)
            }
            guard record?.id == current.id else { return }
            let confirmed = Set(parts.filter { $0 >= 0 && $0 < current.partCount })
            current.acknowledgedParts = confirmed.sorted()
            current.acknowledgedBytes = receivedBytes(current, confirmed)
            current.nextPart = (0..<current.partCount).first {
                !confirmed.contains($0)
            } ?? current.partCount
            current.failures = 0
            record = current
            progress = Double(current.acknowledgedBytes) / Double(max(1, current.size))
            persist()
            active = true
            retryBlocked = false
            status = "Đã khôi phục " + String(format: "%.1f", progress * 100) +
                "%; đang tải tiếp các phần còn thiếu."
            scheduleNext()
        } catch {
            retryBlocked = true
            active = false
            status = "Chưa đối chiếu được máy chủ: " + error.localizedDescription +
                ". Mở lại ứng dụng và bấm Tiếp tục."
        }
    }

    func setBackgroundEventsCompletion(_ handler: @escaping () -> Void) {
        backgroundEventsCompletion = handler
        restore()
    }

    func start(source: URL, uploadID: String, size: Int, chunkBytes: Int,
               metadata: Data) async throws {
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
                                     acknowledgedBytes: 0, failures: 0,
                                     metadata: metadata, renewals: 0, acknowledgedParts: [])
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

    func discardExpiredUpload() {
        guard !sending, !renewing else { return }
        if let current = record, let folder = try? Self.jobFolder(current.id) {
            try? FileManager.default.removeItem(at: folder)
        }
        record = nil
        pending = false
        active = false
        progress = 0
        retryBlocked = false
        status = "Đã bỏ phiên tải cũ. Thông tin sản phẩm vẫn được lưu; vui lòng chọn lại IPA."
        persist()
    }

    // Render's free ephemeral filesystem can lose an upload session on deploy.
    // Only a server acknowledgement counts. If the server returns HTTP 404,
    // create a new session and restart from byte zero using the cached chunks.
    private func renewMissingSession() async {
        defer { renewing = false }
        guard var current = record, let metadata = current.metadata else {
            retryBlocked = true
            active = false
            status = "Phiên cũ đã mất trên máy chủ. Hãy bỏ phiên cũ và chọn lại tệp."
            return
        }
        let renewals = current.renewals ?? 0
        guard renewals < 3 else {
            retryBlocked = true
            active = false
            status = "Máy chủ nhiều lần mất phiên upload. Hãy thử lại khi máy chủ ổn định."
            return
        }
        do {
            let key = Self.storedAdminKey()
            guard key.count >= 32 else { throw ERIBackgroundError.keyMissing }
            var request = URLRequest(url: URL(string: eriBackgroundHost +
                "/api/v1/admin/upload-sessions")!)
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.setValue(key, forHTTPHeaderField: "X-ERI-Admin-Key")
            request.httpBody = metadata
            request.timeoutInterval = 60
            let (body, response) = try await URLSession.shared.data(for: request)
            let http = (response as? HTTPURLResponse)?.statusCode ?? 0
            guard http == 201,
                  let data = try JSONSerialization.jsonObject(with: body) as? [String: Any],
                  let newID = data["upload_id"] as? String,
                  let chunkSize = data["chunk_bytes"] as? Int,
                  let totalSize = data["size_bytes"] as? Int,
                  data["fast"] as? Bool == true,
                  totalSize == current.size, chunkSize == current.chunkBytes else {
                throw URLError(.badServerResponse)
            }
            let original = try Self.jobFolder(current.id)
            let renamed = try Self.jobFolder(newID)
            try FileManager.default.moveItem(at: original, to: renamed)
            current.id = newID
            current.nextPart = 0
            current.acknowledgedBytes = 0
            current.acknowledgedParts = []
            current.failures = 0
            current.renewals = renewals + 1
            record = current
            progress = 0
            pending = true
            active = true
            retryBlocked = false
            persist()
            status = "Máy chủ đã mất phiên cũ. Đã tạo phiên mới; đang tải lại từ đầu..."
            renewing = false
            scheduleNext()
        } catch {
            retryBlocked = true
            active = false
            status = "Không tạo lại được phiên upload: " + error.localizedDescription +
                ". Tệp đã tách phần vẫn được giữ trên iPhone; bấm Tiếp tục để thử lại."
        }
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

    private func partIndex(_ name: String?, for current: ERIBackgroundRecord) -> Int? {
        guard let name else { return nil }
        let prefix = "part:" + current.id + ":"
        if name.hasPrefix(prefix) {
            return Int(name.dropFirst(prefix.count))
        }
        // A single outstanding task from the previous app version may still
        // be managed by iOS after upgrading. Never apply it to a renewed ID.
        if current.acknowledgedParts == nil && name.hasPrefix("part:"),
           !name.dropFirst(5).contains(":") {
            return Int(name.dropFirst(5))
        }
        return nil
    }

    private func isCompleteTask(_ name: String?, for current: ERIBackgroundRecord) -> Bool {
        name == "complete:" + current.id ||
        (current.acknowledgedParts == nil && name == "complete")
    }

    // Queue ALL remaining file-backed requests before iOS suspends the app.
    // The background URLSession runs in a system process. The session still
    // uses only ONE HTTP connection, and the API accepts out-of-order indexes.
    // Finalization must not be submitted until each chunk is acknowledged.
    private func scheduleNext() {
        guard !scheduling, !renewing, !retryBlocked, let current = record else { return }
        let key = Self.storedAdminKey()
        guard key.count >= 32 else {
            active = false
            retryBlocked = true
            status = ERIBackgroundError.keyMissing.localizedDescription
            return
        }
        scheduling = true
        let uploadID = current.id
        transfers.getAllTasks { tasks in
            DispatchQueue.main.async {
                defer { self.scheduling = false }
                guard let current = self.record, current.id == uploadID,
                      !self.renewing, !self.retryBlocked else { return }
                do {
                    let folder = try Self.jobFolder(uploadID)
                    let confirmed = self.receivedIndices(current)
                    let queued = Set(tasks.compactMap {
                        self.partIndex($0.taskDescription, for: current)
                    })
                    let finalTaskExists = tasks.contains {
                        self.isCompleteTask($0.taskDescription, for: current)
                    }
                    let base = eriBackgroundHost + "/api/v1/admin/upload-sessions/" + uploadID
                    var added = 0
                    if confirmed.count == current.partCount {
                        if !finalTaskExists {
                            let file = folder.appendingPathComponent("complete.body")
                            guard FileManager.default.fileExists(atPath: file.path),
                                  let url = URL(string: base + "/complete") else {
                                throw ERIBackgroundError.invalidFile
                            }
                            var request = URLRequest(url: url)
                            request.httpMethod = "POST"
                            request.setValue(key, forHTTPHeaderField: "X-ERI-Admin-Key")
                            request.setValue("application/octet-stream",
                                             forHTTPHeaderField: "Content-Type")
                            request.timeoutInterval = 180
                            let job = self.transfers.uploadTask(with: request, fromFile: file)
                            job.taskDescription = "complete:" + uploadID
                            job.resume()
                            added = 1
                        }
                        self.status = "Đã tải xong IPA; đang xác nhận sản phẩm trên máy chủ..."
                    } else {
                        // Limit connection count to one, NOT the number of
                        // scheduled tasks. iOS can transfer the queue without
                        // waiting for ERI ADMIN to run after every 4-MB file.
                        for i in 0..<current.partCount where
                            !confirmed.contains(i) && !queued.contains(i) {
                            let name = Self.partName(i)
                            let file = folder.appendingPathComponent(name + ".part")
                            guard FileManager.default.fileExists(atPath: file.path) else {
                                throw ERIBackgroundError.invalidFile
                            }
                            let digest = try String(contentsOf:
                                folder.appendingPathComponent(name + ".sha256"),
                                encoding: .utf8)
                            guard let url = URL(string: base + "/fast-parts/" + String(i)) else {
                                throw URLError(.badURL)
                            }
                            var request = URLRequest(url: url)
                            request.httpMethod = "POST"
                            request.setValue(key, forHTTPHeaderField: "X-ERI-Admin-Key")
                            request.setValue("application/octet-stream",
                                             forHTTPHeaderField: "Content-Type")
                            request.setValue(digest, forHTTPHeaderField: "X-ERI-Chunk-SHA256")
                            request.timeoutInterval = 180
                            let job = self.transfers.uploadTask(with: request, fromFile: file)
                            job.taskDescription = "part:" + uploadID + ":" + String(i)
                            job.resume()
                            added += 1
                        }
                        self.status = "iOS đang tải IPA trong nền; " +
                            String(confirmed.count) + "/" + String(current.partCount) +
                            " phần đã được máy chủ xác nhận."
                    }
                    self.sending = !tasks.isEmpty || added > 0
                    self.pending = true
                    self.active = true
                } catch {
                    self.retryBlocked = true
                    self.active = false
                    self.status = "Không thể chuẩn bị tải nền: " + error.localizedDescription +
                        ". Mở ERI ADMIN và bấm Tiếp tục."
                }
            }
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    didSendBodyData bytesSent: Int64,
                    totalBytesSent: Int64,
                    totalBytesExpectedToSend: Int64) {
        guard let current = record,
              partIndex(task.taskDescription, for: current) != nil,
              totalBytesExpectedToSend > 0 else { return }
        // The progress bar can include bytes in flight; only the server's
        // successful response advances the durable checkpoint.
        let display = Double(current.acknowledgedBytes) + Double(totalBytesSent)
        progress = min(0.999, display / Double(max(1, current.size)))
    }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    didCompleteWithError error: Error?) {
        guard var current = record else { return }
        let isComplete = isCompleteTask(task.taskDescription, for: current)
        let index = partIndex(task.taskDescription, for: current)
        // Ignore responses from an older upload ID after a renewed session.
        guard isComplete || index != nil else { return }
        if renewing { return }
        let code = (task.response as? HTTPURLResponse)?.statusCode ?? 0
        let expectedStatus = isComplete ? 201 : 200

        if error == nil && code == expectedStatus {
            if isComplete {
                let id = current.id
                record = nil
                pending = false
                active = false
                sending = false
                progress = 1.0
                status = "IPA đã tải xong và sản phẩm đã được máy chủ xác nhận."
                persist()
                Task.detached(priority: .utility) {
                    if let folder = try? Self.jobFolder(id) {
                        try? FileManager.default.removeItem(at: folder)
                    }
                }
                return
            }

            if let index, index >= 0, index < current.partCount {
                var confirmed = receivedIndices(current)
                confirmed.insert(index)
                current.acknowledgedParts = confirmed.sorted()
                current.acknowledgedBytes = receivedBytes(current, confirmed)
                current.nextPart = (0..<current.partCount).first {
                    !confirmed.contains($0)
                } ?? current.partCount
                current.failures = 0
                record = current
                progress = Double(current.acknowledgedBytes) / Double(max(1, current.size))
                persist()
                scheduleNext()
                return
            }
        }

        if code == 404 {
            progress = Double(current.acknowledgedBytes) / Double(max(1, current.size))
            if current.metadata == nil {
                active = false
                retryBlocked = true
                status = "Phiên upload đã hết hạn; vui lòng mở ERI ADMIN để chọn lại tệp."
            } else if !renewing {
                renewing = true
                active = true
                status = "Đang khôi phục phiên upload bị mất trên máy chủ..."
                // Cancel queued requests for the obsolete session, then
                // create a new session without deleting local cached IPA.
                session.getAllTasks { tasks in
                    for outstanding in tasks {
                        let description = outstanding.taskDescription ?? ""
                        if description.hasPrefix("part:" + current.id + ":") ||
                           description == "complete:" + current.id {
                            outstanding.cancel()
                        }
                    }
                }
                Task { await renewMissingSession() }
            }
            return
        }

        // A missing response can happen after the server persisted the part.
        // Retrying the same part is safe: the API checks its SHA-256 marker.
        current.failures += 1
        record = current
        progress = Double(current.acknowledgedBytes) / Double(max(1, current.size))
        persist()
        if current.failures <= 3 && !retryBlocked {
            status = "Kết nối gián đoạn; đang gửi lại phần chưa được xác nhận..."
            scheduleNext()
        } else {
            retryBlocked = true
            active = false
            status = "Tạm dừng (HTTP " + String(code) + "): " +
                (error?.localizedDescription ?? "Máy chủ chưa xác nhận") +
                ". Mở ERI ADMIN và bấm Tiếp tục."
            // Do not claim to have paused while iOS is still transferring.
            session.getAllTasks { tasks in tasks.forEach { $0.cancel() } }
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
