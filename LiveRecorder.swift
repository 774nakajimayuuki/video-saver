import SwiftUI
import WebKit
import AppKit

/*
 ライブ録画（2026-11-17）

 アプリ内ブラウザでライブを流し見し、「ここから録画」を押した時点から保存する。
 録画は表示しているページの URL を yt-dlp に渡して行う（画面キャプチャではない）。
 そのため画質は配信そのままで、ブラウザのウィンドウを隠しても、画面が消えても録れる。
 停止すると yt-dlp を止め、既存の保存処理と同じく MP4（H.264 / AAC）または m4a（音声のみ）にまとめる。
 DRM で保護された配信は対象外。保存が認められているライブにお使いください。
 */

// MARK: - アプリ内ブラウザ

@MainActor final class LiveBrowser: ObservableObject {
    let webView: WKWebView
    @Published var currentURL: URL?
    @Published var pageTitle = ""
    @Published var canGoBack = false
    @Published var canGoForward = false
    @Published var loading = false
    private var observers: [NSKeyValueObservation] = []

    init() {
        let config = WKWebViewConfiguration()
        config.mediaTypesRequiringUserActionForPlayback = []
        config.preferences.isElementFullscreenEnabled = true
        webView = WKWebView(frame: .zero, configuration: config)
        webView.customUserAgent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
        webView.allowsBackForwardNavigationGestures = true
        observers = [
            webView.observe(\.url) { [weak self] _, _ in MainActor.assumeIsolated { self?.sync() } },
            webView.observe(\.title) { [weak self] _, _ in MainActor.assumeIsolated { self?.sync() } },
            webView.observe(\.canGoBack) { [weak self] _, _ in MainActor.assumeIsolated { self?.sync() } },
            webView.observe(\.canGoForward) { [weak self] _, _ in MainActor.assumeIsolated { self?.sync() } },
            webView.observe(\.isLoading) { [weak self] _, _ in MainActor.assumeIsolated { self?.sync() } },
        ]
        load("https://www.youtube.com/")
    }

    private func sync() {
        currentURL = webView.url
        pageTitle = webView.title ?? ""
        canGoBack = webView.canGoBack
        canGoForward = webView.canGoForward
        loading = webView.isLoading
    }

    /// URL ならそのまま開き、ドメインらしければ https を補い、それ以外は YouTube で検索する。
    func load(_ text: String) {
        let input = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !input.isEmpty else { return }
        if let url = URL(string: input), ["http", "https"].contains(url.scheme?.lowercased() ?? ""), url.host != nil {
            webView.load(URLRequest(url: url)); return
        }
        if !input.contains(" "), input.contains("."), let url = URL(string: "https://" + input), url.host != nil {
            webView.load(URLRequest(url: url)); return
        }
        var components = URLComponents(string: "https://www.youtube.com/results")!
        components.queryItems = [URLQueryItem(name: "search_query", value: input)]
        if let url = components.url { webView.load(URLRequest(url: url)) }
    }
}

struct LiveWebView: NSViewRepresentable {
    let webView: WKWebView
    func makeNSView(context: Context) -> WKWebView { webView }
    func updateNSView(_ nsView: WKWebView, context: Context) {}
}

// MARK: - 録画

@MainActor final class LiveRecorder: ObservableObject {
    static let shared = LiveRecorder()

    enum Mode: String, CaseIterable, Identifiable {
        case video = "動画", audio = "音声のみ", capture = "再生音を録音"
        var id: String { rawValue }
    }

    struct Tools: Sendable {
        let ytdlp: String
        let ffmpeg: String
        let ffprobe: String
    }

    @Published var mode: Mode = .video
    @Published var quality = "1080"
    @Published var folder = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask)[0]
    @Published var recording = false
    @Published var finishing = false
    @Published var elapsed: TimeInterval = 0
    @Published var status = "ライブを開き、録りたい場面で「ここから録画」を押してください"
    @Published var detail = ""
    @Published var log = ""
    @Published var lastFile: URL?

    private var process: Process?
    private var staging: URL?
    private var destination = FileManager.default.temporaryDirectory
    private var activeMode: Mode = .video
    private var title = ""
    private var startedAt = Date()
    private var timer: Timer?
    private var activity: NSObjectProtocol?
    private var pending = ""
    private var terminating = false

    func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.directoryURL = folder
        if panel.runModal() == .OK, let selected = panel.url { folder = selected }
    }

    /// 録画できるページか（YouTube はトップや検索結果ではなく、動画・ライブのページのみ）。
    static func isRecordable(_ url: URL?) -> Bool {
        guard let url, ["http", "https"].contains(url.scheme?.lowercased() ?? ""), let host = url.host?.lowercased() else { return false }
        let isYouTube = host == "youtube.com" || host.hasSuffix(".youtube.com") || host == "youtu.be"
        guard isYouTube else { return true }
        if host == "youtu.be" { return url.path.count > 1 }
        if url.path == "/watch" {
            return URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.contains { $0.name == "v" && !($0.value ?? "").isEmpty } ?? false
        }
        return url.path.hasPrefix("/live/") || url.path.hasPrefix("/shorts/")
    }

    static func findTools() -> (Tools?, String?) {
        let ytdlp = Bundle.main.bundleURL.appendingPathComponent("Contents/Helpers/yt-dlp").path
        guard FileManager.default.isExecutableFile(atPath: ytdlp) else {
            return (nil, "保存エンジン（yt-dlp）が見つかりません。yt-dlp をこのフォルダに置いてアプリを再ビルドしてください。")
        }
        guard let ffmpeg = [Bundle.main.bundleURL.appendingPathComponent("Contents/Helpers/ffmpeg").path, "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"].first(where: { FileManager.default.isExecutableFile(atPath: $0) }) else {
            return (nil, "ffmpeg / ffprobe が見つかりません。brew install ffmpeg を実行してください。")
        }
        let ffprobe = URL(fileURLWithPath: ffmpeg).deletingLastPathComponent().appendingPathComponent("ffprobe").path
        guard FileManager.default.isExecutableFile(atPath: ffprobe) else {
            return (nil, "ffmpeg / ffprobe が見つかりません。brew install ffmpeg を実行してください。")
        }
        return (Tools(ytdlp: ytdlp, ffmpeg: ffmpeg, ffprobe: ffprobe), nil)
    }

    static func arguments(url: URL, staging: URL, mode: Mode, quality: String, ffmpegDir: String) -> [String] {
        let format = mode == .video
            ? "b[height<=?\(quality)][vcodec^=avc1]/b[height<=?\(quality)]/bv*[height<=?\(quality)]+ba/b"
            : "ba/b"
        var args = ["--ignore-config", "--no-playlist", "--newline", "--progress", "--no-colors",
                    "--no-part", "--hls-use-mpegts", "--ffmpeg-location", ffmpegDir,
                    "--format", format, "--paths", staging.path, "--output", "live.%(ext)s",
                    "--print", "before_dl:__TITLE__%(title).150B"]
        for runtime in ["/opt/homebrew/bin/deno", "/usr/local/bin/deno", "/opt/homebrew/bin/node", "/usr/local/bin/node",
                        Bundle.main.bundleURL.appendingPathComponent("Contents/Helpers/node").path] {
            if FileManager.default.isExecutableFile(atPath: runtime) {
                args += ["--js-runtimes", "\(URL(fileURLWithPath: runtime).lastPathComponent):\(runtime)"]
                break
            }
        }
        args += ["--", url.absoluteString]
        return args
    }

    func start(url: URL?) {
        guard !recording, !finishing, mode != .capture else { return }
        guard Self.isRecordable(url), let url else { status = "動画またはライブのページを開いてください"; return }
        if let host = url.host?.lowercased(), host == "radiko.jp" || host.hasSuffix(".radiko.jp") {
            // ラジコは yt-dlp では番組を特定できず失敗するため、再生音を録る方式を案内する。
            status = "ラジコは、下のモードを「再生音を録音」にして、再生しながら録音してください（「動画」「音声のみ」は YouTube などの動画用です）"
            return
        }
        let (found, problem) = Self.findTools()
        guard let tools = found else { status = problem ?? "録画に必要な部品が見つかりません。"; return }

        let staging = folder.appendingPathComponent(".video-saver-live-" + UUID().uuidString)
        do { try FileManager.default.createDirectory(at: staging, withIntermediateDirectories: true) }
        catch { status = "保存先に作業フォルダを作れません: \(error.localizedDescription)"; return }

        let job = Process()
        job.executableURL = URL(fileURLWithPath: tools.ytdlp)
        job.arguments = Self.arguments(url: url, staging: staging, mode: mode, quality: quality,
                                       ffmpegDir: URL(fileURLWithPath: tools.ffmpeg).deletingLastPathComponent().path)
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
        env["SSL_CERT_FILE"] = "/etc/ssl/cert.pem"   // 同梱の静的 ffmpeg が HTTPS の証明書を見つけられるように
        job.environment = env
        let pipe = Pipe()
        job.standardOutput = pipe
        job.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if data.isEmpty { handle.readabilityHandler = nil; return }
            let text = String(decoding: data, as: UTF8.self)
            Task { @MainActor in LiveRecorder.shared.consume(text) }
        }
        job.terminationHandler = { _ in
            Task { @MainActor in await LiveRecorder.shared.finalize(tools: tools) }
        }

        log = ""; detail = ""; pending = ""; title = ""
        self.staging = staging
        destination = folder
        activeMode = mode
        do { try job.run() }
        catch {
            try? FileManager.default.removeItem(at: staging)
            status = "録画を開始できません: \(error.localizedDescription)"; return
        }
        process = job
        recording = true
        startedAt = Date()
        elapsed = 0
        status = activeMode == .video ? "録画中…（ここまでの内容が保存されます）" : "録音中…（ここまでの内容が保存されます）"
        activity = ProcessInfo.processInfo.beginActivity(options: [.userInitiated, .idleSystemSleepDisabled], reason: "ライブを録画中")
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.tick() }
        }
    }

    func stop() {
        guard recording, let job = process, job.isRunning else { return }
        status = "停止して保存しています…"
        job.interrupt()   // SIGINT: yt-dlp が書きかけのデータを閉じてから終了する
    }

    private func tick() { elapsed = Date().timeIntervalSince(startedAt) }

    private func consume(_ text: String) {
        pending += text
        let lines = pending.split(omittingEmptySubsequences: false, whereSeparator: { $0 == "\n" || $0 == "\r" })
        pending = String(lines.last ?? "")
        for line in lines.dropLast() {
            let line = String(line)
            if line.hasPrefix("__TITLE__") { title = String(line.dropFirst("__TITLE__".count)); continue }
            if line.isEmpty { continue }
            if line.hasPrefix("[download]") || line.hasPrefix("size=") || line.hasPrefix("frame=") { detail = line }
            log += line + "\n"
            if log.count > 24000 { log = String(log.suffix(18000)) }
        }
    }

    private func finalize(tools: Tools) async {
        guard recording, !terminating else { return }
        timer?.invalidate(); timer = nil
        if let activity { ProcessInfo.processInfo.endActivity(activity); self.activity = nil }
        process = nil
        recording = false
        finishing = true
        status = "保存しています…"
        let staging = self.staging
        let destination = self.destination
        let audio = activeMode == .audio
        defer {
            if let staging { try? FileManager.default.removeItem(at: staging) }
            self.staging = nil
            finishing = false
        }
        do {
            guard let staging, let source = Self.pickSource(in: staging) else {
                throw Self.failure("録画されたデータがありません。\n" + String(log.suffix(800)))
            }
            let output = staging.appendingPathComponent("export-" + UUID().uuidString + (audio ? ".m4a" : ".mp4"))
            let probe = try await Self.run(tools.ffprobe, ["-v", "error", "-show_streams", "-of", "json", source.path])
            let name = Self.cleanName(title)
            var args: [String]
            if audio {
                let streams = try JSONDecoder().decode(MP4Plan.Metadata.self, from: Data(probe.utf8)).streams
                guard let track = streams.first(where: { $0.codec_type == "audio" }) else { throw Self.failure("音声トラックがありません。") }
                args = ["-hide_banner", "-nostdin", "-nostats", "-loglevel", "warning", "-n", "-i", source.path,
                        "-vn", "-map", "0:a:0", "-sn", "-dn"]
                args += track.codec_name == "aac" ? ["-c:a", "copy"] : ["-c:a", "aac", "-b:a", "192k"]
                args += ["-movflags", "+faststart"]
                if !title.isEmpty { args += ["-metadata", "title=\(AudioRecorder.stripControl(title))"] }
                args += ["-f", "ipod", output.path]
            } else {
                let plan = try MP4Plan(metadata: Data(probe.utf8))
                args = plan.arguments(source: source.path, output: output.path)
            }
            _ = try await Self.run(tools.ffmpeg, args)
            let target = Self.uniqueURL(in: destination, base: name, ext: audio ? "m4a" : "mp4")
            try FileManager.default.moveItem(at: output, to: target)
            lastFile = target
            status = "保存しました: \(target.lastPathComponent)"
        } catch {
            status = "保存できませんでした: \(error.localizedDescription)"
        }
    }

    /// アプリ終了時: 録画中なら止めて、変換せずに途中までのデータを保存先へ残す。
    func emergencySave() {
        guard recording, let job = process, let staging else { return }
        terminating = true
        job.interrupt()
        let deadline = Date().addingTimeInterval(5)
        while job.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.1) }
        if job.isRunning { job.terminate() }
        if let source = Self.pickSource(in: staging) {
            let ext = source.pathExtension.isEmpty ? "ts" : source.pathExtension
            let target = Self.uniqueURL(in: destination, base: Self.cleanName(title), ext: ext)
            try? FileManager.default.moveItem(at: source, to: target)
        }
        try? FileManager.default.removeItem(at: staging)
    }

    // MARK: ヘルパー

    nonisolated static func pickSource(in folder: URL) -> URL? {
        let items = (try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: [.fileSizeKey])) ?? []
        return items
            .filter { $0.lastPathComponent.hasPrefix("live.") && !["part", "ytdl", "json"].contains($0.pathExtension) }
            .map { ($0, (try? $0.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0) }
            .filter { $0.1 > 0 }
            .max { $0.1 < $1.1 }?.0
    }

    static func cleanName(_ title: String) -> String {
        let cleaned = AudioRecorder.cleanTitle(title)
        if !cleaned.isEmpty { return cleaned }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd HH-mm-ss"
        return "ライブ \(formatter.string(from: Date()))"
    }

    /// 同名があるときだけ「 (2)」「 (3)」…を付ける（上書きしない）。
    static func uniqueURL(in folder: URL, base: String, ext: String) -> URL {
        var url = folder.appendingPathComponent("\(base).\(ext)")
        var n = 2
        while FileManager.default.fileExists(atPath: url.path) {
            url = folder.appendingPathComponent("\(base) (\(n)).\(ext)")
            n += 1
        }
        return url
    }

    nonisolated static func failure(_ message: String) -> NSError {
        NSError(domain: "VideoSaver", code: 1, userInfo: [NSLocalizedDescriptionKey: message])
    }

    nonisolated static func run(_ executable: String, _ args: [String]) async throws -> String {
        try await Task.detached {
            let job = Process()
            job.executableURL = URL(fileURLWithPath: executable)
            job.arguments = args
            var env = ProcessInfo.processInfo.environment
            env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
            env["SSL_CERT_FILE"] = "/etc/ssl/cert.pem"
            job.environment = env
            let pipe = Pipe()
            job.standardOutput = pipe
            job.standardError = pipe
            try job.run()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            job.waitUntilExit()
            let output = String(decoding: data, as: UTF8.self)
            guard job.terminationStatus == 0 else {
                throw failure("処理に失敗しました (\(job.terminationStatus))。" + String(output.suffix(1500)))
            }
            return output
        }.value
    }
}

// MARK: - ページタイトルから録音のタイトルを作る

enum PageTitle {
    struct Info { let title: String; let artist: String }

    /// ラジコ: 「2026/11/17/火 18:00-19:00 | 番組名 | 局名 | radiko」→ タイトル「番組名 2026-11-17」、アーティスト「局名」。
    /// それ以外のページはページタイトルをそのまま使う（YouTube の「 - YouTube」は除く）。
    static func parse(_ raw: String) -> Info {
        let parts = raw.components(separatedBy: " | ").map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
        if parts.count >= 4, parts.last == "radiko", let date = isoDate(from: parts[0]) {
            let program = parts[1..<(parts.count - 2)].joined(separator: " | ")
            if !program.isEmpty { return Info(title: "\(program) \(date)", artist: parts[parts.count - 2]) }
        }
        var title = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if title.hasSuffix(" - YouTube") { title = String(title.dropLast(" - YouTube".count)) }
        return Info(title: title, artist: "")
    }

    /// 「2026/11/17/火 18:00-19:00」→「2026-11-17」
    static func isoDate(from text: String) -> String? {
        guard let m = text.range(of: #"(\d{4})/(\d{1,2})/(\d{1,2})"#, options: .regularExpression) else { return nil }
        let nums = text[m].split(separator: "/").compactMap { Int($0) }
        guard nums.count == 3 else { return nil }
        return String(format: "%04d-%02d-%02d", nums[0], nums[1], nums[2])
    }
}

// MARK: - 画面

struct LiveRecordView: View {
    @ObservedObject var browser: LiveBrowser
    @ObservedObject var audio: AudioRecorder
    @ObservedObject private var rec = LiveRecorder.shared
    // 入力中の文字列は画面側で持つ（ページ遷移の更新で貼り付けや変換中の文字が消えないように）
    @State private var address = ""
    @State private var title = ""
    @State private var autoTitle = ""

    private var capturing: Bool { rec.mode == .capture }
    private var busy: Bool { rec.recording || rec.finishing || audio.recording }
    private var info: PageTitle.Info { PageTitle.parse(browser.pageTitle) }
    private var recordLabel: String { rec.mode == .video ? "ここから録画" : "ここから録音" }

    /// ページが変わったら、書き換えていない限りタイトル欄を自動で更新する。
    private func refreshTitle() {
        let new = info.title
        if title.isEmpty || title == autoTitle { title = new }
        autoTitle = new
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                Button { browser.webView.goBack() } label: { Image(systemName: "chevron.left") }
                    .disabled(!browser.canGoBack).help("戻る")
                Button { browser.webView.goForward() } label: { Image(systemName: "chevron.right") }
                    .disabled(!browser.canGoForward).help("進む")
                Button { browser.webView.reload() } label: { Image(systemName: "arrow.clockwise") }
                    .help("再読み込み")
                Divider().frame(height: 16)
                Button { browser.load("https://radiko.jp/") } label: { Label("ラジコ", systemImage: "radio") }
                    .help("ラジコを開く")
                Button { browser.load("https://www.youtube.com/") } label: { Label("YouTube", systemImage: "play.rectangle") }
                    .help("YouTube を開く")
                Divider().frame(height: 16)
                TextField("URL または検索語（ラジコは https://radiko.jp/）", text: $address)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { browser.load(address) }
                if browser.loading { ProgressView().controlSize(.small) }
            }.padding(8)
            LiveWebView(webView: browser.webView)
            Divider()
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 12) {
                    Picker("", selection: $rec.mode) {
                        ForEach(LiveRecorder.Mode.allCases) { Text($0.rawValue).tag($0) }
                    }.pickerStyle(.segmented).frame(width: 330).disabled(busy)
                    if rec.mode == .video {
                        Picker("画質の上限", selection: $rec.quality) {
                            Text("720p").tag("720")
                            Text("1080p").tag("1080")
                            Text("4K").tag("2160")
                        }.frame(width: 210).disabled(busy)
                    }
                    Button("保存先を選ぶ…", action: rec.chooseFolder).disabled(busy)
                    Text(rec.folder.path).font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                    Spacer()
                }
                if capturing {
                    HStack {
                        Text("タイトル").font(.callout)
                        TextField("ページの番組名と日付から自動入力（書き換えできます）", text: $title)
                            .textFieldStyle(.roundedBorder).disabled(audio.recording)
                        if !info.artist.isEmpty { Text(info.artist).font(.caption).foregroundStyle(.secondary) }
                    }
                }
                HStack(spacing: 12) {
                    if capturing {
                        if audio.recording && audio.selfAudio {
                            Button(action: audio.stop) { Label("録音を終了", systemImage: "stop.circle.fill") }
                                .buttonStyle(.borderedProminent).tint(.red)
                            Text(AudioRecorderView.format(audio.elapsed)).monospacedDigit()
                        } else {
                            Button { audio.start(folder: rec.folder, title: title, artist: info.artist, selfAudio: true) } label: {
                                Label(recordLabel, systemImage: "record.circle")
                            }
                            .buttonStyle(.borderedProminent).disabled(audio.recording || rec.finishing)
                        }
                    } else if rec.recording {
                        Button(action: rec.stop) { Label("録画を終了", systemImage: "stop.circle.fill") }
                            .buttonStyle(.borderedProminent).tint(.red)
                        Text(AudioRecorderView.format(rec.elapsed)).monospacedDigit()
                    } else {
                        Button { rec.start(url: browser.currentURL) } label: { Label(recordLabel, systemImage: "record.circle") }
                            .buttonStyle(.borderedProminent)
                            .disabled(rec.finishing || audio.recording || !LiveRecorder.isRecordable(browser.currentURL))
                        if rec.finishing { ProgressView().controlSize(.small) }
                    }
                    Spacer()
                    if !busy, let file = capturing ? audio.lastFile : rec.lastFile, FileManager.default.fileExists(atPath: file.path) {
                        Button("Finder で表示") { NSWorkspace.shared.activateFileViewerSelecting([file]) }
                    }
                }
                Text(capturing ? audio.status : rec.status).font(.callout).textSelection(.enabled)
                if capturing {
                    Text("このアプリ内ブラウザで再生している音だけを録ります（他のアプリの音は入りません）。Mac の音量を 0 にしても録れますが、ページ側でミュートにしないでください。")
                        .font(.caption).foregroundStyle(.secondary)
                } else if !rec.detail.isEmpty && rec.recording {
                    Text(rec.detail).font(.system(size: 11, design: .monospaced)).foregroundStyle(.secondary).lineLimit(1)
                }
            }.padding(12)
        }
        .onAppear {
            address = browser.currentURL?.absoluteString ?? address
            refreshTitle()
        }
        .onChange(of: browser.currentURL) { _, new in
            if let new { address = new.absoluteString }
        }
        .onChange(of: browser.pageTitle) { _, _ in refreshTitle() }
        .onChange(of: title) { _, new in
            let cleaned = AudioRecorder.stripControl(new)
            if cleaned != new { title = cleaned }
        }
    }
}
