import SwiftUI
import WebKit
import AppKit

/*
 Instagram 保存（2026-11-20）

 アプリ内ブラウザで Instagram を開き（ログインは一度だけ。WKWebView の既定データストアに残る）、
 表示中の投稿 / ストーリーを R2 へ保存する。
 - 投稿 (/p/<code>/): ページから画像・動画 URL とキャプションを取り、
   instagram/media/<code>/NN.jpg, NN.mp4, NN_cover.jpg, meta.json に置く。
   リール (/reel/<code>/) は /p/<code>/ として開き直してから保存する。
 - ストーリー (/stories/<user>/<mediaid>/): 読み込まれた DASH の映像と音声を同梱の ffmpeg で結合し、
   instagram/stories/<user>/<YYYY>/<MM>/<YYYYMMDD_HHMMSS>_<mediaid>.mp4 に置く（UTC）。
 保存後、Notion 登録用の JSON を ~/Documents/tadamoeka_instagram_backup/app_saved/ に書く。
 R2 の認証情報は R2 タブで入れたもの（キーチェーン）を使う。保存が認められている範囲でお使いください。
 */

// MARK: - ブラウザ

@MainActor final class InstagramBrowser: ObservableObject {
    let webView: WKWebView
    @Published var currentURL: URL?
    @Published var canGoBack = false
    @Published var canGoForward = false
    private var observers: [NSKeyValueObservation] = []

    init() {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        config.mediaTypesRequiringUserActionForPlayback = []
        webView = WKWebView(frame: .zero, configuration: config)
        webView.customUserAgent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
        webView.allowsBackForwardNavigationGestures = true
        observers = [
            webView.observe(\.url) { [weak self] _, _ in MainActor.assumeIsolated { self?.sync() } },
            webView.observe(\.canGoBack) { [weak self] _, _ in MainActor.assumeIsolated { self?.sync() } },
            webView.observe(\.canGoForward) { [weak self] _, _ in MainActor.assumeIsolated { self?.sync() } },
        ]
        load("https://www.instagram.com/tadamoeka_ta/")
    }

    private func sync() {
        currentURL = webView.url
        canGoBack = webView.canGoBack
        canGoForward = webView.canGoForward
    }

    func load(_ text: String) {
        let input = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: input), ["http", "https"].contains(url.scheme?.lowercased() ?? "") else { return }
        webView.load(URLRequest(url: url))
    }
}

// MARK: - 保存処理

struct IGPostData: Decodable {
    let code: String
    let cap: String
    let urls: [String]
    let th: [Bool]
    let vu: [String]
    let date: String
}

struct IGStoryEntry: Decodable {
    let url: String
    let efg: String
    let asset: String
}

enum IGError: LocalizedError {
    case msg(String)
    var errorDescription: String? { if case .msg(let m) = self { return m }; return nil }
}

@MainActor final class InstagramSaver: ObservableObject {
    @Published var bucket = UserDefaults.standard.string(forKey: "ig.bucket") ?? "tadamoeka" {
        didSet { UserDefaults.standard.set(bucket, forKey: "ig.bucket") }
    }
    @Published var account = UserDefaults.standard.string(forKey: "ig.account") ?? "tadamoeka_ta" {
        didSet { UserDefaults.standard.set(account, forKey: "ig.account") }
    }
    @Published var busy = false
    @Published var status = "Instagram にログインして、投稿かストーリーを開いてください"
    @Published var log = ""

    static let manifestDir = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Documents/tadamoeka_instagram_backup/app_saved")

    private func append(_ line: String) {
        log += line + "\n"
        status = line
    }

    private func config() throws -> R2Config {
        let account = UserDefaults.standard.string(forKey: "r2.accountID") ?? "bb5d0b6f48672b797d38d756df71ec9f"
        let name = bucket.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { throw IGError.msg("バケット名を入力してください") }
        guard let id = Keychain.get("accessKey"), let secret = Keychain.get("secretKey") else {
            throw IGError.msg("R2 の認証情報が未設定です。R2 タブでアクセスキーを保存してください")
        }
        return R2Config(accountID: account, bucket: name, accessKey: id, secretKey: secret)
    }

    func saveCurrent(_ browser: InstagramBrowser) {
        guard !busy else { return }
        guard let url = browser.currentURL else { status = "ページが開かれていません"; return }
        let parts = url.path.split(separator: "/").map(String.init)
        busy = true
        log = ""
        Task {
            defer { busy = false }
            do {
                let cfg = try config()
                if let i = parts.firstIndex(of: "stories"), parts.count > i + 2 {
                    try await saveStory(browser, user: parts[i + 1], mediaID: parts[i + 2], cfg: cfg)
                } else if let i = parts.firstIndex(of: "p"), parts.count > i + 1 {
                    try await savePost(browser, code: parts[i + 1], account: parts.first == "p" ? account : parts[0], cfg: cfg)
                } else if let i = parts.firstIndex(of: "reel"), parts.count > i + 1 {
                    browser.load("https://www.instagram.com/p/\(parts[i + 1])/")
                    status = "リールは /p/ で開き直しました。読み込みが終わってから、もう一度「保存」を押してください"
                } else {
                    status = "投稿 (/p/…) かストーリー (/stories/…) のページを開いてから押してください"
                }
            } catch {
                append("失敗: \(error.localizedDescription)")
            }
        }
    }

    // MARK: 投稿

    private func savePost(_ browser: InstagramBrowser, code: String, account: String, cfg: R2Config) async throws {
        append("投稿 \(code) を読み取っています…（スライドを順に送ります）")
        let result = try await browser.webView.callAsyncJavaScript(Self.postScript, arguments: [:], in: nil, contentWorld: .page)
        guard let json = result as? String, let data = json.data(using: .utf8) else { throw IGError.msg("ページから情報を取れませんでした") }
        let post = try JSONDecoder().decode(IGPostData.self, from: data)
        guard !post.urls.isEmpty else { throw IGError.msg("画像が見つかりません。読み込み完了後にもう一度試してください") }

        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("ig-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tmp) }
        let base = "instagram/media/\(post.code)"
        let firstVideo = post.th.firstIndex(of: true)
        var videoSlides: [Int] = []

        for (i, u) in post.urls.enumerated() {
            let n = String(format: "%02d", i + 1)
            let isVideoSlide = i < post.th.count && post.th[i]
            let name = isVideoSlide ? "\(n)_cover.jpg" : "\(n).jpg"
            if isVideoSlide { videoSlides.append(i + 1) }
            try await downloadAndPut(u, name: name, key: "\(base)/\(name)", tmp: tmp, cfg: cfg)
        }
        var videoSaved = false
        if let v = post.vu.first, let idx = firstVideo {
            let name = String(format: "%02d.mp4", idx + 1)
            try await downloadAndPut(v, name: name, key: "\(base)/\(name)", tmp: tmp, cfg: cfg)
            videoSaved = true
        }
        let meta: [String: Any] = [
            "code": post.code, "account": account,
            "url": "https://www.instagram.com/\(account)/p/\(post.code)/",
            "date": post.date, "caption": post.cap, "images": post.urls.count,
            "video_slides": videoSlides, "video_saved": videoSaved,
        ]
        let metaData = try JSONSerialization.data(withJSONObject: meta, options: [.prettyPrinted, .withoutEscapingSlashes])
        let metaFile = tmp.appendingPathComponent("meta.json")
        try metaData.write(to: metaFile)
        _ = try await R2Client.put(metaFile, key: "\(base)/meta.json", config: cfg) { _ in }
        try writeManifest(name: post.code, data: metaData)
        append("完了: \(post.code)（画像 \(post.urls.count) 枚\(videoSaved ? "・動画あり" : "")）。Notion 登録は Claude に「app_saved を登録して」と頼んでください")
    }

    private func downloadAndPut(_ urlString: String, name: String, key: String, tmp: URL, cfg: R2Config) async throws {
        guard let url = URL(string: urlString) else { throw IGError.msg("URL が不正です: \(name)") }
        var req = URLRequest(url: url)
        req.setValue("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.0 Safari/605.1.15", forHTTPHeaderField: "User-Agent")
        append("保存中: \(name)")
        let (file, resp) = try await URLSession.shared.download(for: req)
        guard (resp as? HTTPURLResponse)?.statusCode == 200 else { throw IGError.msg("ダウンロードに失敗しました: \(name)") }
        let dest = tmp.appendingPathComponent(name)
        try? FileManager.default.removeItem(at: dest)
        try FileManager.default.moveItem(at: file, to: dest)
        _ = try await R2Client.put(dest, key: key, config: cfg) { _ in }
    }

    // MARK: ストーリー

    private func saveStory(_ browser: InstagramBrowser, user: String, mediaID: String, cfg: R2Config) async throws {
        append("ストーリー \(mediaID) の動画を探しています…")
        let result = try await browser.webView.callAsyncJavaScript(Self.storyScript, arguments: [:], in: nil, contentWorld: .page)
        guard let json = result as? String, let data = json.data(using: .utf8) else { throw IGError.msg("ページから情報を取れませんでした") }
        let entries = try JSONDecoder().decode([IGStoryEntry].self, from: data)
        // 先に読み込まれた asset を、いま開いているストーリーとみなす（他のストーリーは先読みされる）。
        var order: [String] = []
        for e in entries where !order.contains(e.asset) { order.append(e.asset) }
        guard let asset = order.first else {
            throw IGError.msg("動画が見つかりません。ログイン済みか確認し、ストーリーを開き直して数秒待ってからもう一度押してください")
        }
        let group = entries.filter { $0.asset == asset }
        guard let v = group.first(where: { $0.efg.contains("dash_vp9") || ($0.efg.contains("video") && !$0.efg.contains("audio")) }),
              let a = group.first(where: { $0.efg.contains("audio") }) else {
            throw IGError.msg("映像か音声のどちらかが見つかりません。少し再生してからもう一度押してください")
        }
        guard let id = UInt64(mediaID) else { throw IGError.msg("ストーリー ID が数字ではありません") }
        let ms = (id >> 23) + 1_314_220_021_721
        let date = Date(timeIntervalSince1970: Double(ms) / 1000)
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX"); f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy/MM"; let ym = f.string(from: date)
        f.dateFormat = "yyyyMMdd_HHmmss"; let stamp = f.string(from: date)
        f.dateFormat = "yyyy-MM-dd"; let day = f.string(from: date)
        let fileName = "\(stamp)_\(mediaID).mp4"
        let key = "instagram/stories/\(user)/\(ym)/\(fileName)"

        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("ig-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tmp) }
        for (u, name) in [(v.url, "v.mp4"), (a.url, "a.mp4")] {
            guard let url = URL(string: u) else { throw IGError.msg("URL が不正です") }
            append("ダウンロード中: \(name == "v.mp4" ? "映像" : "音声")")
            let (file, resp) = try await URLSession.shared.download(for: URLRequest(url: url))
            guard (resp as? HTTPURLResponse)?.statusCode == 200 else { throw IGError.msg("ダウンロードに失敗しました（期限切れの可能性。開き直してください）") }
            try FileManager.default.moveItem(at: file, to: tmp.appendingPathComponent(name))
        }
        let ffmpeg = Bundle.main.bundleURL.appendingPathComponent("Contents/Helpers/ffmpeg")
        guard FileManager.default.isExecutableFile(atPath: ffmpeg.path) else { throw IGError.msg("同梱の ffmpeg が見つかりません") }
        let out = tmp.appendingPathComponent(fileName)
        append("映像と音声を結合しています…")
        let status = try await Self.runFFmpeg(ffmpeg, ["-y", "-i", tmp.appendingPathComponent("v.mp4").path,
                                                        "-i", tmp.appendingPathComponent("a.mp4").path,
                                                        "-c", "copy", "-movflags", "+faststart", out.path])
        guard status == 0 else { throw IGError.msg("ffmpeg での結合に失敗しました (\(status))") }
        append("R2 へアップロード中…")
        _ = try await R2Client.put(out, key: key, config: cfg) { _ in }
        let meta: [String: Any] = [
            "type": "story", "account": user, "date": day, "key": key,
            "url": "https://www.instagram.com/stories/\(user)/\(mediaID)/",
            "public_url": "https://media.tadamoeka.workers.dev/\(key)",
        ]
        try writeManifest(name: "story_\(mediaID)",
                          data: try JSONSerialization.data(withJSONObject: meta, options: [.prettyPrinted, .withoutEscapingSlashes]))
        append("完了: \(key)。Notion 登録は Claude に「app_saved を登録して」と頼んでください")
    }

    nonisolated static func runFFmpeg(_ exe: URL, _ args: [String]) async throws -> Int32 {
        try await withCheckedThrowingContinuation { cont in
            let p = Process()
            p.executableURL = exe
            p.arguments = args
            p.standardOutput = FileHandle.nullDevice
            p.standardError = FileHandle.nullDevice
            p.terminationHandler = { cont.resume(returning: $0.terminationStatus) }
            do { try p.run() } catch { cont.resume(throwing: error) }
        }
    }

    private func writeManifest(name: String, data: Data) throws {
        try FileManager.default.createDirectory(at: Self.manifestDir, withIntermediateDirectories: true)
        try data.write(to: Self.manifestDir.appendingPathComponent("\(name).json"))
    }

    // MARK: ページ内スクリプト

    /// 投稿ページから {code, cap, urls, th, vu, date} を JSON 文字列で返す（fetch_post_r2.js と同じ取り方）。
    static let postScript = #"""
    const sleep=ms=>new Promise(r=>setTimeout(r,ms));
    const BS=String.fromCharCode(92);
    const art=document.querySelector('article')||document.querySelector('main');
    const isMain=i=>/cdninstagram|fbcdn/.test(i.currentSrc||i.src)&&i.naturalWidth>300&&!i.closest('a[href*="/p/"],a[href*="/reel/"]');
    for(let w=0;w<20;w++){ if([...art.querySelectorAll('img')].some(isMain)) break; await sleep(500); }
    await sleep(900);
    const key=u=>u.split('?')[0];
    const seen=[];
    for(let k=0;k<20;k++){
     [...art.querySelectorAll('img')].filter(isMain).map(i=>i.currentSrc||i.src).forEach(u=>{if(!seen.find(s=>key(s)===key(u)))seen.push(u)});
     const nb=art.querySelector('button[aria-label="次へ"],button[aria-label="Next"]'); if(!nb)break; nb.click(); await sleep(700);
    }
    const m=location.pathname.match(/\/(p|reel)s?\/([^/]+)/); const code=m[2];
    const A='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
    let id=0n; for(const c of code) id=id*64n+BigInt(A.indexOf(c));
    const date=new Date(Number((id>>23n)+1314220021721n)+9*3600e3).toISOString().slice(0,10);
    const t=await (await fetch('/'+m[1]+'/'+code+'/',{credentials:'omit'})).text();
    const d=(t.match(/<meta property="og:description" content="([^"]*)"/)||[])[1]||'';
    const ta=document.createElement('textarea'); ta.innerHTML=d;
    const cm=ta.value.match(/[A-Za-z0-9_.]+: "([\s\S]*)"\.?\s*$/);
    const e=await (await fetch('/p/'+code+'/embed/captioned/',{credentials:'omit'})).text();
    let urls=seen.slice();
    if(!urls.length){ const em=(e.match(/class="EmbeddedMediaImage"[^>]*src="([^"]+)"/)||[])[1]; if(em){ta.innerHTML=em; urls=[ta.value];} }
    const vu=[]; let i=-1;
    while((i=e.indexOf('video_url',i+1))>=0){
     const seg=e.slice(i+14,i+3000); const j=seg.indexOf(BS+'"');
     const u=seg.slice(0,j).split(BS+BS+BS+'/').join('/').split(BS+BS+'u0026').join('&').split(BS+'u0026').join('&').split(BS+BS+'u0025').join('%');
     if(u.startsWith('https')&&!vu.some(x=>key(x)===key(u))) vu.push(u);
    }
    const th=urls.map(u=>{try{return atob(decodeURIComponent((u.match(/[?&]efg=([^&]+)/)||[])[1]||'')).includes('video')}catch(x){return false}});
    return JSON.stringify({code, cap: cm?cm[1].replace(new RegExp('['+String.fromCharCode(0x2060,0x200b)+']','g'),''):'', urls, th, vu, date});
    """#

    /// 読み込み済みの mp4（DASH）リソースを、読み込み順に {url, efg, asset} で返す。bytestart / byteend は外す。
    static let storyScript = #"""
    await new Promise(r=>setTimeout(r,1500));
    const out=[];
    for(const e of performance.getEntriesByType('resource')){
     if(!/cdninstagram|fbcdn/.test(e.name)||!/\.mp4/.test(e.name)) continue;
     let u; try{u=new URL(e.name)}catch(x){continue}
     u.searchParams.delete('bytestart'); u.searchParams.delete('byteend');
     const efg=u.searchParams.get('efg')||''; let dec='';
     try{dec=atob(decodeURIComponent(efg))}catch(x){}
     const asset=(dec.match(/"xpv_asset_id":(\d+)/)||[])[1]||(dec.match(/xpv_asset_id\D+(\d+)/)||[])[1]||'';
     if(!asset) continue;
     if(out.some(o=>o.url===u.toString())) continue;
     out.push({url:u.toString(), efg:dec, asset});
    }
    return JSON.stringify(out);
    """#
}

// MARK: - 画面

struct InstagramSaveView: View {
    @ObservedObject var browser: InstagramBrowser
    @StateObject private var saver = InstagramSaver()
    @State private var address = ""

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                Button { browser.webView.goBack() } label: { Image(systemName: "chevron.left") }.disabled(!browser.canGoBack)
                Button { browser.webView.goForward() } label: { Image(systemName: "chevron.right") }.disabled(!browser.canGoForward)
                Button { browser.webView.reload() } label: { Image(systemName: "arrow.clockwise") }
                TextField("URL", text: $address).textFieldStyle(.roundedBorder)
                    .onSubmit { browser.load(address) }
                    .onChange(of: browser.currentURL) { _, new in address = new?.absoluteString ?? "" }
                Button("保存") { saver.saveCurrent(browser) }
                    .buttonStyle(.borderedProminent).disabled(saver.busy)
            }
            .padding(8)
            LiveWebView(webView: browser.webView)
            Divider()
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Text("バケット").font(.caption)
                    TextField("tadamoeka", text: $saver.bucket).textFieldStyle(.roundedBorder).frame(width: 140)
                    Text("アカウント").font(.caption)
                    TextField("tadamoeka_ta", text: $saver.account).textFieldStyle(.roundedBorder).frame(width: 140)
                    if saver.busy { ProgressView().controlSize(.small) }
                    Spacer()
                }
                Text(saver.status).font(.callout).lineLimit(2)
                ScrollView { Text(saver.log).font(.system(.caption, design: .monospaced)).frame(maxWidth: .infinity, alignment: .leading) }
                    .frame(height: 60)
            }
            .padding(8)
        }
    }
}
