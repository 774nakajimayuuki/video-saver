import SwiftUI
import AppKit
import CryptoKit
import Security

/*
 Cloudflare R2 へのアップロード（2026-11-17）

 R2 の S3 互換 API（AWS Signature V4）で、保存した動画・音声をバケットへ PUT する。
 認証情報（アクセスキー ID / シークレット）は macOS のキーチェーンにだけ保存し、ファイルやログには残さない。
 1 回の PUT で上げられるのは 5 GiB まで。それを超えるファイルは対象外（分割アップロードは未対応）。
 */

// MARK: - キーチェーン

enum Keychain {
    static let service = "local.videosaver.r2"

    static func get(_ account: String) -> String? {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                    kSecAttrAccount as String: account, kSecReturnData as String: true,
                                    kSecMatchLimit as String: kSecMatchLimitOne]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess, let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    @discardableResult
    static func set(_ value: String, for account: String) -> Bool {
        let base: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                   kSecAttrAccount as String: account]
        SecItemDelete(base as CFDictionary)
        guard !value.isEmpty else { return true }
        var add = base
        add[kSecValueData as String] = Data(value.utf8)
        return SecItemAdd(add as CFDictionary, nil) == errSecSuccess
    }
}

// MARK: - AWS Signature Version 4

enum SigV4 {
    static func hmac(_ key: Data, _ message: String) -> Data {
        Data(HMAC<SHA256>.authenticationCode(for: Data(message.utf8), using: SymmetricKey(data: key)))
    }

    static func hex<S: Sequence>(_ bytes: S) -> String where S.Element == UInt8 {
        bytes.map { String(format: "%02x", $0) }.joined()
    }

    static func sha256Hex(_ text: String) -> String { hex(SHA256.hash(data: Data(text.utf8))) }

    /// パスを URI エンコードする（"/" は残す。英数字と - _ . ~ 以外は %XX）。
    static func encodePath(_ path: String) -> String {
        let unreserved = Set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~")
        var out = ""
        for byte in path.utf8 {
            let ch = Character(UnicodeScalar(byte))
            if byte == UInt8(ascii: "/") || (byte < 128 && unreserved.contains(ch)) { out.append(ch) }
            else { out += String(format: "%%%02X", byte) }
        }
        return out
    }

    static func amzDate(_ date: Date) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyyMMdd'T'HHmmss'Z'"
        return f.string(from: date)
    }

    /// Authorization ヘッダーの値を作る。headers は署名に含めるヘッダー全部（host, x-amz-date など）。
    static func authorization(method: String, canonicalURI: String, canonicalQuery: String = "",
                              headers: [String: String], payloadHash: String, amzDate: String,
                              region: String, service: String, accessKey: String, secretKey: String) -> String {
        let names = headers.keys.map { $0.lowercased() }.sorted()
        let lowered = Dictionary(uniqueKeysWithValues: headers.map { ($0.key.lowercased(), $0.value.trimmingCharacters(in: .whitespaces)) })
        let canonicalHeaders = names.map { "\($0):\(lowered[$0]!)\n" }.joined()
        let signedHeaders = names.joined(separator: ";")
        let canonicalRequest = [method, canonicalURI, canonicalQuery, canonicalHeaders, signedHeaders, payloadHash].joined(separator: "\n")
        let dateStamp = String(amzDate.prefix(8))
        let scope = "\(dateStamp)/\(region)/\(service)/aws4_request"
        let stringToSign = ["AWS4-HMAC-SHA256", amzDate, scope, sha256Hex(canonicalRequest)].joined(separator: "\n")
        let kDate = hmac(Data("AWS4\(secretKey)".utf8), dateStamp)
        let kRegion = hmac(kDate, region)
        let kService = hmac(kRegion, service)
        let kSigning = hmac(kService, "aws4_request")
        let signature = hex(hmac(kSigning, stringToSign))
        return "AWS4-HMAC-SHA256 Credential=\(accessKey)/\(scope), SignedHeaders=\(signedHeaders), Signature=\(signature)"
    }
}

// MARK: - R2 クライアント

struct R2Config {
    let accountID: String
    let bucket: String
    let accessKey: String
    let secretKey: String
    var host: String { "\(accountID).r2.cloudflarestorage.com" }
}

enum R2Error: LocalizedError {
    case http(Int, String), tooLarge, invalid(String)
    var errorDescription: String? {
        switch self {
        case .http(let code, let body):
            let hint: String
            switch code {
            case 403: hint = "（認証情報か、バケットへの書き込み権限を確認してください）"
            case 404: hint = "（バケット名かアカウント ID を確認してください）"
            default: hint = ""
            }
            return "R2 がエラーを返しました (\(code))\(hint) \(body)"
        case .tooLarge: return "ファイルが 5 GiB を超えています（分割アップロードには未対応です）"
        case .invalid(let message): return message
        }
    }
}

final class UploadProgress: NSObject, URLSessionTaskDelegate {
    let onProgress: @Sendable (Double) -> Void
    init(_ onProgress: @escaping @Sendable (Double) -> Void) { self.onProgress = onProgress }
    func urlSession(_ session: URLSession, task: URLSessionTask, didSendBodyData bytesSent: Int64,
                    totalBytesSent: Int64, totalBytesExpectedToSend: Int64) {
        if totalBytesExpectedToSend > 0 { onProgress(Double(totalBytesSent) / Double(totalBytesExpectedToSend)) }
    }
}

enum R2Client {
    static let region = "auto"
    static let limit: Int64 = 5 * 1024 * 1024 * 1024

    static func contentType(for url: URL) -> String {
        switch url.pathExtension.lowercased() {
        case "mp4": return "video/mp4"
        case "m4a": return "audio/mp4"
        case "ts": return "video/mp2t"
        default: return "application/octet-stream"
        }
    }

    private static func signedRequest(_ config: R2Config, method: String, path: String, contentType: String? = nil) -> URLRequest {
        let encoded = SigV4.encodePath(path)
        let date = SigV4.amzDate(Date())
        let payload = "UNSIGNED-PAYLOAD"
        let auth = SigV4.authorization(method: method, canonicalURI: encoded,
                                       headers: ["host": config.host, "x-amz-content-sha256": payload, "x-amz-date": date],
                                       payloadHash: payload, amzDate: date, region: region, service: "s3",
                                       accessKey: config.accessKey, secretKey: config.secretKey)
        var request = URLRequest(url: URL(string: "https://\(config.host)\(encoded)")!)
        request.httpMethod = method
        request.setValue(payload, forHTTPHeaderField: "x-amz-content-sha256")
        request.setValue(date, forHTTPHeaderField: "x-amz-date")
        request.setValue(auth, forHTTPHeaderField: "Authorization")
        if let contentType { request.setValue(contentType, forHTTPHeaderField: "Content-Type") }
        return request
    }

    private static func check(_ data: Data, _ response: URLResponse) throws -> HTTPURLResponse {
        guard let http = response as? HTTPURLResponse else { throw R2Error.invalid("応答を読み取れません") }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(decoding: data.prefix(400), as: UTF8.self)
            let message = body.range(of: "<Message>(.*?)</Message>", options: .regularExpression).map { String(body[$0]) } ?? ""
            throw R2Error.http(http.statusCode, message.replacingOccurrences(of: "<Message>", with: "").replacingOccurrences(of: "</Message>", with: ""))
        }
        return http
    }

    /// バケットに届くか（認証情報・バケット名・権限）を、何も書き込まずに確認する。
    static func headBucket(_ config: R2Config) async throws {
        let request = signedRequest(config, method: "HEAD", path: "/\(config.bucket)")
        let (data, response) = try await URLSession.shared.data(for: request)
        _ = try check(data, response)
    }

    /// ファイルを PUT する。戻り値は ETag。ファイルはメモリに読み込まず、ディスクから流して送る。
    static func put(_ file: URL, key: String, config: R2Config, progress: @escaping @Sendable (Double) -> Void) async throws -> String {
        let size = (try FileManager.default.attributesOfItem(atPath: file.path)[.size] as? Int64) ?? 0
        guard size <= limit else { throw R2Error.tooLarge }
        let request = signedRequest(config, method: "PUT", path: "/\(config.bucket)/\(key)", contentType: contentType(for: file))
        let (data, response) = try await URLSession.shared.upload(for: request, fromFile: file, delegate: UploadProgress(progress))
        let http = try check(data, response)
        return http.value(forHTTPHeaderField: "ETag") ?? ""
    }
}

// MARK: - 状態

struct LocalFile: Identifiable, Hashable {
    let url: URL
    let size: Int64
    let modified: Date
    var id: URL { url }
}

@MainActor final class R2Uploader: ObservableObject {
    @Published var accountID = UserDefaults.standard.string(forKey: "r2.accountID") ?? "bb5d0b6f48672b797d38d756df71ec9f" {
        didSet { UserDefaults.standard.set(accountID, forKey: "r2.accountID") }
    }
    @Published var bucket = UserDefaults.standard.string(forKey: "r2.bucket") ?? "claudefrale" {
        didSet { UserDefaults.standard.set(bucket, forKey: "r2.bucket") }
    }
    @Published var prefix = UserDefaults.standard.string(forKey: "r2.prefix") ?? "video-saver/" {
        didSet { UserDefaults.standard.set(prefix, forKey: "r2.prefix") }
    }

    @Published var accessKeyInput = ""
    @Published var secretInput = ""
    @Published var hasCredentials = false
    @Published var files: [LocalFile] = []
    @Published var selected = Set<URL>()
    @Published var busy = false
    @Published var progress = 0.0
    @Published var current = ""
    @Published var status = "接続設定を入れて、「接続を確認」を押してください"
    @Published var log = ""

    init() { refreshCredentialState() }

    func refreshCredentialState() {
        hasCredentials = Keychain.get("accessKey") != nil && Keychain.get("secretKey") != nil
    }

    func saveCredentials() {
        let id = accessKeyInput.trimmingCharacters(in: .whitespacesAndNewlines)
        let secret = secretInput.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !id.isEmpty, !secret.isEmpty else { status = "アクセスキー ID とシークレットの両方を入力してください"; return }
        if Keychain.set(id, for: "accessKey") && Keychain.set(secret, for: "secretKey") {
            accessKeyInput = ""; secretInput = ""
            refreshCredentialState()
            status = "認証情報をキーチェーンに保存しました"
        } else {
            status = "キーチェーンに保存できませんでした"
        }
    }

    func clearCredentials() {
        Keychain.set("", for: "accessKey"); Keychain.set("", for: "secretKey")
        refreshCredentialState()
        status = "保存していた認証情報を削除しました"
    }

    private func config() -> R2Config? {
        let account = accountID.trimmingCharacters(in: .whitespacesAndNewlines)
        let name = bucket.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !account.isEmpty, !name.isEmpty else { status = "アカウント ID とバケット名を入力してください"; return nil }
        guard let id = Keychain.get("accessKey"), let secret = Keychain.get("secretKey") else {
            status = "認証情報が未設定です。アクセスキー ID とシークレットを入力して保存してください"; return nil
        }
        return R2Config(accountID: account, bucket: name, accessKey: id, secretKey: secret)
    }

    func testConnection() {
        guard !busy, let config = config() else { return }
        busy = true
        status = "接続を確認しています…"
        Task {
            defer { busy = false }
            do {
                try await R2Client.headBucket(config)
                status = "接続できました（バケット「\(config.bucket)」にアクセスできます）"
            } catch {
                status = "接続できませんでした: \(error.localizedDescription)"
            }
        }
    }

    /// 保存先フォルダの動画・音声（mp4 / m4a / ts）を新しい順に並べる。
    func refreshFiles(in folders: [URL]) {
        var found: [LocalFile] = []
        var seen = Set<URL>()
        for folder in folders {
            let items = (try? FileManager.default.contentsOfDirectory(
                at: folder, includingPropertiesForKeys: [.fileSizeKey, .contentModificationDateKey], options: [.skipsHiddenFiles])) ?? []
            for url in items where ["mp4", "m4a", "ts"].contains(url.pathExtension.lowercased()) && seen.insert(url).inserted {
                let values = try? url.resourceValues(forKeys: [.fileSizeKey, .contentModificationDateKey])
                found.append(LocalFile(url: url, size: Int64(values?.fileSize ?? 0), modified: values?.contentModificationDate ?? .distantPast))
            }
        }
        files = Array(found.sorted { $0.modified > $1.modified }.prefix(200))
        selected = selected.filter { url in files.contains { $0.url == url } }
    }

    func addFiles() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        if panel.runModal() == .OK {
            for url in panel.urls where !files.contains(where: { $0.url == url }) {
                let values = try? url.resourceValues(forKeys: [.fileSizeKey, .contentModificationDateKey])
                files.insert(LocalFile(url: url, size: Int64(values?.fileSize ?? 0), modified: values?.contentModificationDate ?? Date()), at: 0)
                selected.insert(url)
            }
        }
    }

    /// 保存先のキー。「接頭辞 + 年/月/ファイル名」（年月はファイルの更新日）。
    func key(for file: LocalFile) -> String {
        var p = prefix.trimmingCharacters(in: CharacterSet(charactersIn: "/ ").union(.whitespaces))
        if !p.isEmpty { p += "/" }
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy/MM"
        return "\(p)\(f.string(from: file.modified))/\(file.url.lastPathComponent)"
    }

    func upload() {
        guard !busy, let config = config() else { return }
        let targets = files.filter { selected.contains($0.url) }
        guard !targets.isEmpty else { status = "アップロードするファイルを選んでください"; return }
        busy = true
        log = ""
        Task {
            defer { busy = false; progress = 0; current = "" }
            var done = 0
            for (index, file) in targets.enumerated() {
                let key = key(for: file)
                current = "\(index + 1)/\(targets.count) \(file.url.lastPathComponent)"
                status = "アップロードしています… \(current)"
                progress = 0
                do {
                    _ = try await R2Client.put(file.url, key: key, config: config) { value in
                        Task { @MainActor in self.progress = value }
                    }
                    done += 1
                    log += "完了: \(key)\n"
                } catch {
                    log += "失敗: \(file.url.lastPathComponent) — \(error.localizedDescription)\n"
                }
            }
            status = done == targets.count ? "\(done) 件のアップロードが完了しました" : "\(done)/\(targets.count) 件が完了しました。失敗したものは下の詳細を確認してください"
        }
    }
}

// MARK: - 画面

struct R2UploadView: View {
    @StateObject private var model = R2Uploader()
    let folders: [URL]

    private static let sizeFormatter: ByteCountFormatter = {
        let f = ByteCountFormatter(); f.countStyle = .file; return f
    }()
    private static let dateFormatter: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd HH:mm"; return f
    }()

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 14) {
                Image(systemName: "icloud.and.arrow.up.fill").font(.system(size: 40)).foregroundStyle(.orange)
                VStack(alignment: .leading, spacing: 4) {
                    Text("R2 にアップロード").font(.largeTitle.bold())
                    Text("保存した動画・音声を Cloudflare R2 のバケットへ。").foregroundStyle(.secondary)
                }
            }

            GroupBox {
                VStack(alignment: .leading, spacing: 8) {
                    Text("接続設定").font(.headline)
                    HStack {
                        TextField("アカウント ID", text: $model.accountID).textFieldStyle(.roundedBorder)
                        TextField("バケット名", text: $model.bucket).textFieldStyle(.roundedBorder).frame(width: 180)
                        TextField("保存先フォルダ", text: $model.prefix).textFieldStyle(.roundedBorder).frame(width: 160)
                    }
                    HStack {
                        TextField("アクセスキー ID", text: $model.accessKeyInput).textFieldStyle(.roundedBorder)
                        SecureField("シークレット アクセスキー", text: $model.secretInput).textFieldStyle(.roundedBorder)
                        Button("保存", action: model.saveCredentials)
                    }
                    HStack {
                        Label(model.hasCredentials ? "認証情報: キーチェーンに保存済み" : "認証情報: 未設定",
                              systemImage: model.hasCredentials ? "checkmark.seal.fill" : "exclamationmark.triangle")
                            .font(.caption).foregroundStyle(model.hasCredentials ? .green : .orange)
                        if model.hasCredentials { Button("削除", action: model.clearCredentials).controlSize(.small) }
                        Spacer()
                        Button("接続を確認", action: model.testConnection).disabled(model.busy)
                    }
                }.padding(6)
            }

            GroupBox {
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Text("アップロードするファイル").font(.headline)
                        Spacer()
                        Button("一覧を更新") { model.refreshFiles(in: folders) }
                        Button("ファイルを追加…", action: model.addFiles)
                    }
                    List(model.files, id: \.url, selection: $model.selected) { file in
                        HStack {
                            Image(systemName: file.url.pathExtension.lowercased() == "m4a" ? "waveform" : "film")
                            Text(file.url.lastPathComponent).lineLimit(1).truncationMode(.middle)
                            Spacer()
                            Text(Self.sizeFormatter.string(fromByteCount: file.size)).foregroundStyle(.secondary).font(.caption)
                            Text(Self.dateFormatter.string(from: file.modified)).foregroundStyle(.secondary).font(.caption)
                        }
                    }
                    .frame(minHeight: 140)
                    .overlay { if model.files.isEmpty { Text("保存先に動画・音声がありません").foregroundStyle(.secondary) } }
                    HStack {
                        Button { model.upload() } label: { Label("選んだファイルを R2 へ", systemImage: "arrow.up.circle") }
                            .buttonStyle(.borderedProminent).disabled(model.busy || model.selected.isEmpty)
                        if model.busy { ProgressView(value: model.progress).frame(width: 160) }
                        Spacer()
                        Text("\(model.selected.count) 件を選択中").font(.caption).foregroundStyle(.secondary)
                    }
                }.padding(6)
            }

            Text(model.status).font(.callout).textSelection(.enabled)
            if !model.log.isEmpty {
                ScrollView {
                    Text(model.log).font(.system(size: 11, design: .monospaced)).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading).padding(8)
                }
                .frame(maxHeight: 90).background(.quaternary.opacity(0.3)).clipShape(RoundedRectangle(cornerRadius: 8))
            }
        }
        .padding(28)
        .onAppear { model.refreshFiles(in: folders) }
    }
}
