import SwiftUI
import AVFoundation
import ScreenCaptureKit

/*
 音声のみの録音（2026-11-17）

 Mac で再生中の音声（システム全体、または選んだアプリ）を ScreenCaptureKit で取得し、
 AAC の .m4a として保存します。映像は取得しません。
 QuickTime の画面収録と同じく、再生された音を録るだけで、配信の保護や暗号化には触れません。
 初回は「システム設定 > プライバシーとセキュリティ > 画面収録とシステムオーディオ録音」の許可が必要です。
 録音した音声は私的利用の範囲で使ってください。配信元の利用規約も確認してください。
 */

struct RecordableApp: Identifiable, Hashable {
    let id: String   // bundle identifier
    let name: String
}

enum AudioRecorderError: LocalizedError {
    case noDisplay, appGone, noAudio, writer(String)
    var errorDescription: String? {
        switch self {
        case .noDisplay: return "ディスプレイが見つかりません"
        case .appGone: return "選んだアプリが見つかりません。アプリを起動して一覧を更新してください"
        case .noAudio: return "音声が録音されませんでした。再生中の音があるか確認してください"
        case .writer(let message): return "ファイルを作成できません: \(message)"
        }
    }
}

/// サンプルバッファを AAC の m4a へ書き出す。append はキャプチャ用キューから呼ばれる。
final class AudioFileWriter {
    let url: URL
    private let writer: AVAssetWriter
    private let input: AVAssetWriterInput
    private let lock = NSLock()
    private var sessionStarted = false

    init(url: URL, title: String = "", artist: String = "") throws {
        self.url = url
        writer = try AVAssetWriter(outputURL: url, fileType: .m4a)
        func item(_ id: AVMetadataIdentifier, _ value: String) -> AVMutableMetadataItem {
            let m = AVMutableMetadataItem()
            m.identifier = id
            m.dataType = kCMMetadataBaseDataType_UTF8 as String
            m.value = value as NSString
            return m
        }
        var tags: [AVMetadataItem] = []
        if !title.isEmpty { tags.append(item(.iTunesMetadataSongName, title)) }
        if !artist.isEmpty { tags.append(item(.iTunesMetadataArtist, artist)) }
        if !tags.isEmpty { writer.metadata = tags }
        input = AVAssetWriterInput(mediaType: .audio, outputSettings: [
            AVFormatIDKey: kAudioFormatMPEG4AAC,
            AVSampleRateKey: 48_000,
            AVNumberOfChannelsKey: 2,
            AVEncoderBitRateKey: 192_000,
        ])
        input.expectsMediaDataInRealTime = true
        guard writer.canAdd(input) else { throw AudioRecorderError.writer("音声トラックを追加できません") }
        writer.add(input)
        guard writer.startWriting() else {
            throw AudioRecorderError.writer(writer.error?.localizedDescription ?? "不明なエラー")
        }
    }

    func append(_ buffer: CMSampleBuffer) {
        lock.lock(); defer { lock.unlock() }
        guard writer.status == .writing, buffer.isValid, CMSampleBufferGetNumSamples(buffer) > 0 else { return }
        if !sessionStarted {
            writer.startSession(atSourceTime: CMSampleBufferGetPresentationTimeStamp(buffer))
            sessionStarted = true
        }
        if input.isReadyForMoreMediaData { input.append(buffer) }
    }

    /// 書き出しを終えて閉じる。音声が一つも来なかった場合はファイルを消してエラーを返す。
    private func closeInput() -> Bool {
        lock.lock(); defer { lock.unlock() }
        if writer.status == .writing { input.markAsFinished() }
        return sessionStarted
    }

    func finish() async -> Error? {
        let started = closeInput()
        guard started else {
            writer.cancelWriting()
            try? FileManager.default.removeItem(at: url)
            return AudioRecorderError.noAudio
        }
        await writer.finishWriting()
        return writer.status == .failed ? writer.error : nil
    }

    func discard() {
        lock.lock(); defer { lock.unlock() }
        if writer.status == .writing { writer.cancelWriting() }
        try? FileManager.default.removeItem(at: url)
    }
}

final class AudioCapture: NSObject, SCStreamOutput, SCStreamDelegate {
    private let writer: AudioFileWriter
    private let onError: (Error) -> Void

    init(writer: AudioFileWriter, onError: @escaping (Error) -> Void) {
        self.writer = writer
        self.onError = onError
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio else { return }
        writer.append(sampleBuffer)
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) { onError(error) }
}

@MainActor final class AudioRecorder: ObservableObject {
    @Published var apps: [RecordableApp] = []
    @Published var selectedApp = ""   // 空文字 = システム全体の音声
    @Published var recording = false
    @Published var selfAudio = false   // true = このアプリ内ブラウザの音を録音中
    @Published var elapsed: TimeInterval = 0
    @Published var status = "録音する音声を選んで「録音開始」を押してください"
    @Published var lastFile: URL?

    private var stream: SCStream?
    private var capture: AudioCapture?
    private var writer: AudioFileWriter?
    private var timer: Timer?
    private var startedAt = Date()
    private var working = false
    private var sleepActivity: NSObjectProtocol?

    func refreshApps() {
        Task {
            do {
                let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
                let me = Bundle.main.bundleIdentifier
                var seen = Set<String>()
                apps = content.applications
                    .filter { !$0.applicationName.isEmpty && !$0.bundleIdentifier.isEmpty && $0.bundleIdentifier != me }
                    .filter { seen.insert($0.bundleIdentifier).inserted }
                    .map { RecordableApp(id: $0.bundleIdentifier, name: $0.applicationName) }
                    .sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
                if !selectedApp.isEmpty && !apps.contains(where: { $0.id == selectedApp }) { selectedApp = "" }
            } catch {
                status = permissionMessage(error)
            }
        }
    }

    /// selfAudio が true のときは、このアプリ内ブラウザが出している音だけを録る（他のアプリの音は入らない）。
    func start(folder: URL, title: String, artist: String = "", selfAudio: Bool = false) {
        guard !recording, !working else { return }
        working = true
        Task {
            defer { working = false }
            await begin(folder: folder, title: title, artist: artist, selfAudio: selfAudio)
        }
    }

    func stop() {
        guard recording, !working else { return }
        working = true
        Task {
            defer { working = false }
            await end(reason: nil)
        }
    }

    private func begin(folder: URL, title rawTitle: String, artist: String, selfAudio: Bool) async {
        var file: AudioFileWriter?
        do {
            if !CGPreflightScreenCaptureAccess() { _ = CGRequestScreenCaptureAccess() }
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
            guard let display = content.displays.first else { throw AudioRecorderError.noDisplay }
            let filter: SCContentFilter
            if selfAudio {
                guard let me = content.applications.first(where: { $0.bundleIdentifier == Bundle.main.bundleIdentifier }) else {
                    throw AudioRecorderError.appGone
                }
                filter = SCContentFilter(display: display, including: [me], exceptingWindows: [])
            } else if selectedApp.isEmpty {
                filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
            } else if let app = content.applications.first(where: { $0.bundleIdentifier == selectedApp }) {
                filter = SCContentFilter(display: display, including: [app], exceptingWindows: [])
            } else {
                throw AudioRecorderError.appGone
            }

            let config = SCStreamConfiguration()
            config.capturesAudio = true
            config.excludesCurrentProcessAudio = !selfAudio
            config.sampleRate = 48_000
            config.channelCount = 2
            // 映像は使わない。最小サイズ・低フレームレートにして負荷を抑える。
            config.width = 2
            config.height = 2
            config.minimumFrameInterval = CMTime(value: 1, timescale: 1)

            let title = Self.cleanTitle(rawTitle)
            let writer = try AudioFileWriter(url: Self.uniqueURL(in: folder, title: title), title: title,
                                             artist: Self.cleanTitle(artist))
            file = writer
            let capture = AudioCapture(writer: writer) { [weak self] error in
                Task { @MainActor in
                    guard let self, self.recording, !self.working else { return }
                    self.working = true
                    await self.end(reason: error)
                    self.working = false
                }
            }
            let stream = SCStream(filter: filter, configuration: config, delegate: capture)
            try stream.addStreamOutput(capture, type: .audio, sampleHandlerQueue: DispatchQueue(label: "video-saver.audio-capture"))
            try await stream.startCapture()

            self.writer = writer
            self.capture = capture
            self.stream = stream
            lastFile = writer.url
            startedAt = Date()
            elapsed = 0
            recording = true
            self.selfAudio = selfAudio
            status = "録音中…（録音中は画面を自動でスリープさせません）"
            // 画面が消えると ScreenCaptureKit の取得が止まるため、録音中だけスリープを防ぐ。
            sleepActivity = ProcessInfo.processInfo.beginActivity(
                options: [.userInitiated, .idleDisplaySleepDisabled, .idleSystemSleepDisabled],
                reason: "音声を録音中")
            timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
                MainActor.assumeIsolated { self?.tick() }
            }
        } catch {
            file?.discard()
            status = permissionMessage(error)
        }
    }

    private func tick() { elapsed = Date().timeIntervalSince(startedAt) }

    private func end(reason: Error?) async {
        timer?.invalidate(); timer = nil
        if let activity = sleepActivity { ProcessInfo.processInfo.endActivity(activity); sleepActivity = nil }
        let stream = self.stream, writer = self.writer
        self.stream = nil; self.capture = nil; self.writer = nil
        recording = false
        selfAudio = false
        if reason == nil { try? await stream?.stopCapture() }
        let error = await writer?.finish()
        if let error {
            status = "録音を終了しました（\(error.localizedDescription)）"
        } else if let url = writer?.url {
            status = "保存しました: \(url.lastPathComponent)"
            lastFile = url
        }
        if let reason, error == nil { status += "（録音が中断されました: \(reason.localizedDescription)）" }
    }

    private func permissionMessage(_ error: Error) -> String {
        if error is AudioRecorderError { return error.localizedDescription }
        let ns = error as NSError
        if ns.domain == SCStreamErrorDomain, ns.code == SCStreamError.userDeclined.rawValue {
            return "画面収録の許可が必要です。システム設定 > プライバシーとセキュリティ > 画面収録とシステムオーディオ録音で Video Saver を許可し、アプリを再起動してください"
        }
        return "録音を開始できません: \(error.localizedDescription)"
    }

    /// 同名ファイルは上書きせず、日時入りの名前に連番を付ける。
    static func cleanTitle(_ raw: String) -> String {
        // 改行・タブなどの制御文字は取り除き、ファイル名に使えない / : は - に置き換える。
        let stripped = stripControl(raw)
        let cleaned = String(stripped.map { "/:\\".contains($0) ? "-" : $0 })
            .trimmingCharacters(in: .whitespaces)
        return String(cleaned.prefix(100))
    }

    /// 改行・タブ・その他の制御文字、行区切りなどを取り除く。
    static func stripControl(_ raw: String) -> String {
        let bad = CharacterSet.controlCharacters.union(.newlines)
            .union(CharacterSet(charactersIn: "\u{200B}\u{200C}\u{200D}\u{2060}\u{FEFF}"))
        return String(String.UnicodeScalarView(raw.unicodeScalars.filter { !bad.contains($0) }))
    }

    static func uniqueURL(in folder: URL, title: String = "") -> URL {
        // タイトルがあればそのまま名前にする。無いときだけ、区別のため日時を付ける。
        var base = title
        if base.isEmpty {
            let formatter = DateFormatter()
            formatter.locale = Locale(identifier: "en_US_POSIX")
            formatter.dateFormat = "yyyy-MM-dd HH-mm-ss"
            base = "録音 \(formatter.string(from: Date()))"
        }
        var url = folder.appendingPathComponent("\(base).m4a")
        var n = 2
        // 同名があるときだけ「 (2)」「 (3)」…を付ける（上書きしない）。
        while FileManager.default.fileExists(atPath: url.path) {
            url = folder.appendingPathComponent("\(base) (\(n)).m4a")
            n += 1
        }
        return url
    }
}

struct AudioRecorderView: View {
    @ObservedObject var recorder: AudioRecorder
    let folder: URL
    let disabled: Bool
    // 入力中の文字列は画面側で持つ（録音の状態更新で貼り付けや変換中の文字が消えないように）
    @State private var title = ""

    var body: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 8) {
                TextField("タイトル（番組名など。省略可）", text: $title)
                    .textFieldStyle(.roundedBorder).disabled(recorder.recording)
                    .onChange(of: title) { _, new in
                        let cleaned = AudioRecorder.stripControl(new)
                        if cleaned != new { title = cleaned }
                    }
                HStack {
                    Text("音声を録音").font(.headline)
                    Spacer()
                    Picker("録音する音", selection: $recorder.selectedApp) {
                        Text("Mac 全体の音").tag("")
                        ForEach(recorder.apps) { Text($0.name).tag($0.id) }
                    }.frame(width: 300).disabled(recorder.recording)
                    Button { recorder.refreshApps() } label: { Image(systemName: "arrow.clockwise") }
                        .help("アプリ一覧を更新").disabled(recorder.recording)
                }
                HStack {
                    if recorder.recording {
                        Button(action: recorder.stop) { Label("録音停止", systemImage: "stop.circle.fill") }
                            .buttonStyle(.borderedProminent).tint(.red)
                        Text(Self.format(recorder.elapsed)).monospacedDigit()
                    } else {
                        Button(action: { recorder.start(folder: folder, title: title) }) { Label("録音開始", systemImage: "record.circle") }
                            .buttonStyle(.borderedProminent).disabled(disabled)
                    }
                    Spacer()
                    if !recorder.recording, let file = recorder.lastFile, FileManager.default.fileExists(atPath: file.path) {
                        Button("Finder で表示") { NSWorkspace.shared.activateFileViewerSelecting([file]) }
                    }
                }
                Text(recorder.status).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                Text("ブラウザーなどで再生中の音を m4a（AAC）で保存します。Radiko を録るときは、再生しているブラウザーを選ぶと他の音が混ざりません。")
                    .font(.caption).foregroundStyle(.secondary)
            }.padding(6)
        }.onAppear { recorder.refreshApps() }
    }

    static func format(_ t: TimeInterval) -> String {
        let s = Int(t)
        return String(format: "%d:%02d:%02d", s / 3600, (s % 3600) / 60, s % 60)
    }
}
