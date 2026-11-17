import Foundation
import Combine

@MainActor final class NHKConnection: ObservableObject {
    @Published private(set) var busy = false
    @Published private(set) var prepared = false
    @Published private(set) var attempted = false
    @Published private(set) var status = ""

    private var support: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("local.videosaver.app")
    }

    init() {
        attempted = FileManager.default.fileExists(atPath: support.appendingPathComponent("own-session-license-attempt-v1.json").path)
        if attempted {
            let data = try? Data(contentsOf: support.appendingPathComponent("own-session-license-result.json"))
            let result = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
            status = result?["phase"] as? String == "authenticated"
                ? "視聴認証テストは成功済みです。番組の MP4 保存は未対応です。"
                : "今回の認証テストは実行済みです。再送はしません。"
        }
    }

    func check(login: NHKLogin, sendOnce: Bool = false) {
        guard !busy else { return }
        guard !sendOnce || !attempted else { return }
        busy = true
        status = sendOnce ? "NHK の視聴認証を確認しています…" : "接続の準備を確認しています…"
        Task {
            defer {
                busy = false
                attempted = FileManager.default.fileExists(atPath: support.appendingPathComponent("own-session-license-attempt-v1.json").path)
            }
            do {
                guard let token = await login.currentAccessToken() else {
                    prepared = false
                    status = "ログイン画面で、もう一度ログインしてください。"
                    return
                }
                let input = try JSONSerialization.data(withJSONObject: ["token": token])
                let helpers = Bundle.main.bundleURL.appendingPathComponent("Contents/Helpers")
                let result: Data = try await Task.detached {
                    let process = Process()
                    process.executableURL = helpers.appendingPathComponent("node")
                    process.arguments = [helpers.appendingPathComponent("../Resources/NHKTrial/nhk-connection.mjs").standardizedFileURL.path, sendOnce ? "send-once" : "prepare"]
                    process.environment = ["PATH": "/usr/bin:/bin", "HOME": NSHomeDirectory(), "TMPDIR": NSTemporaryDirectory()]
                    let source = Pipe()
                    let output = Pipe()
                    process.standardInput = source
                    process.standardOutput = output
                    process.standardError = FileHandle.nullDevice
                    try process.run()
                    try source.fileHandleForWriting.write(contentsOf: input)
                    try source.fileHandleForWriting.close()
                    let result = try output.fileHandleForReading.readToEnd() ?? Data()
                    process.waitUntilExit()
                    return result
                }.value
                guard let metadata = try JSONSerialization.jsonObject(with: result) as? [String: Any],
                      let phase = metadata["phase"] as? String else { throw CheckError.invalidResult }
                try FileManager.default.createDirectory(at: support, withIntermediateDirectories: true)
                // The broker exports only counts/status; credentials and CDM payloads never reach this file.
                // A blocked second invocation must not replace the original outcome.
                if metadata["error_code"] as? String != "attempt_already_recorded" {
                    try result.write(to: support.appendingPathComponent(sendOnce ? "own-session-license-result.json" : "own-session-preparation-result.json"), options: .atomic)
                }
                switch phase {
                case "ready":
                    prepared = true
                    status = "接続の準備を確認しました（外部送信なし）。"
                case "authenticated":
                    // 認証確認はここで完了します。番組取得・復号・MP4保存への接続は未実装です。
                    // 実行済みの記録を消したり、新しい要求を自動送信したりしません。
                    // 将来の保存担当は、認証結果とは別に「保存計画」と「書き出せる素材」を
                    // 受け取る構成が必要です。この結果は件数・状態だけで、素材ではありません。
                    // ホストはこの確認の終了時にセッションを閉じます。保存処理からこの結果を
                    // 読み直しても、以前のセッションを再利用できるわけではありません。
                    // 担当する処理と入出力の案はVideoSaver.swiftのNHK判定コメントを参照。
                    status = "NHK の視聴認証に成功しました。番組の MP4 保存は未対応です。"
                default:
                    let error = metadata["error_code"] as? String
                    status = error == "attempt_already_recorded"
                        ? "今回の認証テストは実行済みです。再送はしていません。"
                        : "接続を確認できませんでした。結果は確認用の記録に保存しました。"
                    if !sendOnce { prepared = false }
                }
            } catch {
                status = "確認処理を実行できませんでした。アプリの構成を確認してください。"
                if !sendOnce { prepared = false }
            }
        }
    }

    private enum CheckError: Error { case invalidResult }
}
