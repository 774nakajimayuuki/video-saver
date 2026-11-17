import SwiftUI
import AppKit

@MainActor final class Saver: ObservableObject {
    @Published var url = ""
    @Published var folder = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask)[0]
    @Published var quality = "1080"
    @Published var busy = false
    @Published var status = "動画の URL を入力してください"
    @Published var log = ""
    private var process: Process?
    private var cancelled = false

    func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.directoryURL = folder
        if panel.runModal() == .OK, let selected = panel.url { folder = selected }
    }

    func start() {
        guard !busy else { return }
        let input = url.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let parsed = URL(string: input), ["https", "http"].contains(parsed.scheme?.lowercased() ?? ""),
              let host = parsed.host, !host.isEmpty else {
            status = "http または https の動画 URL を入力してください"; return
        }
        if host.lowercased() == "web.nhk" || host.lowercased().hasSuffix(".web.nhk") {
            status = "NHK ONE の保存には対応していません。"
            return
        }
        let executable = Bundle.main.bundleURL.appendingPathComponent("Contents/Helpers/yt-dlp")
        guard FileManager.default.isExecutableFile(atPath: executable.path) else {
            status = "保存エンジンが見つかりません。アプリを再ビルドしてください。"; return
        }
        guard let ffmpeg = ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"].first(where: { FileManager.default.isExecutableFile(atPath: $0) }),
              FileManager.default.isExecutableFile(atPath: URL(fileURLWithPath: ffmpeg).deletingLastPathComponent().appendingPathComponent("ffprobe").path) else {
            status = "MP4 保存に必要な ffmpeg / ffprobe が見つかりません。"; return
        }
        let destination = folder
        let staging = destination.appendingPathComponent(".video-saver-" + UUID().uuidString)
        cancelled = false
        log = ""
        busy = true
        status = "動画を確認しています…"
        var args = ["--ignore-config", "--no-playlist", "--newline", "--progress", "--no-colors", "--no-overwrites",
                    "--ffmpeg-location", URL(fileURLWithPath: ffmpeg).deletingLastPathComponent().path,
                    "--format", "bv*[height<=?\(quality)][vcodec^=avc1]+ba[acodec^=mp4a]/b[height<=?\(quality)][vcodec^=avc1][acodec^=mp4a]/bv*[height<=?\(quality)]+ba/b[height<=?\(quality)]",
                    "--merge-output-format", "mkv", "--paths", staging.path,
                    "--output", "%(title).180B [%(id)s].%(ext)s", "--print", "after_move:__SOURCE__%(filepath)s"]
        for runtime in ["/opt/homebrew/bin/deno", "/usr/local/bin/deno", "/opt/homebrew/bin/node", "/usr/local/bin/node", Bundle.main.bundleURL.appendingPathComponent("Contents/Helpers/node").path] {
            if FileManager.default.isExecutableFile(atPath: runtime) {
                args += ["--js-runtimes", "\(URL(fileURLWithPath: runtime).lastPathComponent):\(runtime)"]
                break
            }
        }
        args += ["--", input]
        Task {
            defer {
                try? FileManager.default.removeItem(at: staging)
                process = nil
                busy = false
            }
            do {
                try FileManager.default.createDirectory(at: staging, withIntermediateDirectories: true)
                let download = try await run(executable.path, args)
                guard let sourceLine = download.components(separatedBy: "\n").last(where: { $0.hasPrefix("__SOURCE__") }) else {
                    throw failure("保存した動画が見つかりません。")
                }
                let source = URL(fileURLWithPath: String(sourceLine.dropFirst("__SOURCE__".count)))
                let ffprobe = URL(fileURLWithPath: ffmpeg).deletingLastPathComponent().appendingPathComponent("ffprobe").path
                status = "MP4 との互換性を確認しています…"
                let metadata = try await run(ffprobe, ["-v", "error", "-show_streams", "-of", "json", source.path], showLog: false)
                let plan = try MP4Plan(metadata: Data(metadata.utf8))
                status = plan.copiesVideo && plan.copiesAudio ? "画質を変えずに MP4 にまとめています…" : "QuickTime 用の MP4 に変換しています…"
                append(status)
                let converted = staging.appendingPathComponent("export-" + UUID().uuidString + ".mp4")
                _ = try await run(ffmpeg, plan.arguments(source: source.path, output: converted.path))
                let exported = try await run(ffprobe, ["-v", "error", "-show_streams", "-of", "json", converted.path], showLog: false)
                let verified = try MP4Plan(metadata: Data(exported.utf8))
                guard verified.copiesVideo && verified.copiesAudio else { throw failure("MP4 の互換性を確認できませんでした。") }
                if cancelled { throw CancellationError() }
                let name = source.deletingPathExtension().lastPathComponent
                var target = destination.appendingPathComponent(name + ".mp4")
                var suffix = 2
                while FileManager.default.fileExists(atPath: target.path) {
                    target = destination.appendingPathComponent("\(name) (\(suffix)).mp4")
                    suffix += 1
                }
                try FileManager.default.moveItem(at: converted, to: target)
                append("保存先: " + target.path)
                status = "MP4 の保存が完了しました"
            } catch {
                status = cancelled ? "停止しました" : "保存できませんでした。下の詳細をご確認ください。"
                if !cancelled { append(error.localizedDescription) }
            }
        }
    }

    private func failure(_ message: String) -> NSError {
        NSError(domain: "VideoSaver", code: 1, userInfo: [NSLocalizedDescriptionKey: message])
    }

    private func run(_ executable: String, _ args: [String], showLog: Bool = true) async throws -> String {
        if cancelled { throw CancellationError() }
        let job = Process()
        job.executableURL = URL(fileURLWithPath: executable)
        job.arguments = args
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
        job.environment = env
        let pipe = Pipe()
        job.standardOutput = pipe
        job.standardError = pipe
        process = job
        try job.run()
        let result = await Task.detached { [weak self] () -> (Int32, String) in
            let reader = pipe.fileHandleForReading
            var pending = Data()
            var output = ""
            while true {
                let data = reader.availableData
                if data.isEmpty { break }
                pending.append(data)
                while let newline = pending.firstIndex(of: 10) {
                    let line = String(decoding: pending[..<newline], as: UTF8.self)
                    pending.removeSubrange(...newline)
                    output += line + "\n"
                    if showLog { await self?.append(line) }
                }
            }
            if !pending.isEmpty {
                let line = String(decoding: pending, as: UTF8.self)
                output += line
                if showLog { await self?.append(line) }
            }
            try? reader.close()
            job.waitUntilExit()
            return (job.terminationStatus, output)
        }.value
        process = nil
        if cancelled { throw CancellationError() }
        guard result.0 == 0 else { throw failure("処理に失敗しました (\(result.0))。" + String(result.1.suffix(1500))) }
        return result.1
    }

    private func append(_ line: String) {
        log += line + "\n"
        if log.count > 24000 { log = String(log.suffix(18000)) }
        if busy && line.hasPrefix("[download]") { status = line.replacingOccurrences(of: "[download]", with: "保存中") }
    }
    func cancel() { cancelled = true; process?.interrupt() }
}

struct ContentView: View {
    @StateObject private var saver = Saver()
    @StateObject private var recorder = AudioRecorder()
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack(spacing: 14) {
                Image(systemName: "arrow.down.circle.fill").font(.system(size: 44)).foregroundStyle(.blue)
                VStack(alignment: .leading, spacing: 4) {
                    Text("Video Saver").font(.largeTitle.bold())
                    Text("動画を MP4 で、Mac のフォルダへ。").foregroundStyle(.secondary)
                }
            }
            TextField("https://www.youtube.com/watch?v=…", text: $saver.url)
                .textFieldStyle(.roundedBorder).disabled(saver.busy)
            HStack {
                Picker("画質の上限", selection: $saver.quality) {
                    Text("720p").tag("720")
                    Text("1080p").tag("1080")
                    Text("4K").tag("2160")
                }.frame(width: 210).disabled(saver.busy)
                Spacer()
                Button("保存先を選ぶ…", action: saver.chooseFolder).disabled(saver.busy)
            }
            Text(saver.folder.path).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
            AudioRecorderView(recorder: recorder, folder: saver.folder, disabled: false)
            HStack {
                Button(action: saver.start) { Label("動画を保存", systemImage: "arrow.down.to.line") }
                    .buttonStyle(.borderedProminent).disabled(saver.busy || saver.url.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                if saver.busy {
                    ProgressView().controlSize(.small)
                    Button("停止", action: saver.cancel)
                }
                Spacer()
                Button("保存フォルダを開く") { NSWorkspace.shared.open(saver.folder) }
            }
            Text(saver.status).font(.callout).textSelection(.enabled)
            ScrollView {
                Text(saver.log.isEmpty ? "処理の詳細はここに表示されます。" : saver.log)
                    .font(.system(size: 11, design: .monospaced)).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading).padding(12)
            }.background(.quaternary.opacity(0.3)).clipShape(RoundedRectangle(cornerRadius: 8))
            Text("保存が認められている動画にお使いください。MP4（H.264 / AAC）で保存します。必要な場合だけ変換します。")
                .font(.caption).foregroundStyle(.secondary)
        }.padding(28).frame(minWidth: 640, minHeight: 640)
            .onReceive(NotificationCenter.default.publisher(for: NSApplication.willTerminateNotification)) { _ in saver.cancel(); recorder.stop() }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

@main struct VideoSaverApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    var body: some Scene {
        WindowGroup { ContentView() }
    }
}
