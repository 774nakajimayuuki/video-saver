import AppKit
import Combine
import SwiftUI
import WebKit

@MainActor final class NHKLogin: NSObject, ObservableObject, WKNavigationDelegate, WKUIDelegate, WKHTTPCookieStoreObserver {
    // A persistent, app-owned store. Never import another application's browser profile.
    private let store = WKWebsiteDataStore(forIdentifier: UUID(uuidString: "A7B4F8C3-021B-49C7-A3D0-4970313D88F5")!)
    private let home = URL(string: "https://www.web.nhk/")!
    private var configured = false
    private var readGeneration = 0
    @Published var isPresented = false
    @Published private(set) var state: NHKLoginState = .signedOut
    @Published private(set) var webView: WKWebView?
    @Published private(set) var pageHost = "www.web.nhk"
    @Published private(set) var message = "NHK ONE のページでログインしてください。"
    @Published private(set) var loading = false
    @Published private(set) var clearing = false

    override init() {
        super.init()
        store.httpCookieStore.add(self)
        refreshStatus()
    }

    func prepare() {
        guard !configured else { refreshStatus(); return }
        configured = true
        loading = true
        // This window is for login only. Do not start media or licence requests.
        let rules = """
        [
          {"trigger":{"url-filter":"^https?://licen[cs]e[.]hsk[.]st[.]nhk/"},"action":{"type":"block"}},
          {"trigger":{"url-filter":".*","resource-type":["media"]},"action":{"type":"block"}}
        ]
        """
        WKContentRuleListStore.default().compileContentRuleList(forIdentifier: "VideoSaver.NHK.LoginOnly.v1", encodedContentRuleList: rules) { [weak self] list, error in
            guard let self else { return }
            guard let list, error == nil else {
                self.configured = false
                self.loading = false
                self.message = "ログイン画面を準備できませんでした。もう一度開いてください。"
                return
            }
            let config = WKWebViewConfiguration()
            config.websiteDataStore = self.store
            config.mediaTypesRequiringUserActionForPlayback = .all
            config.userContentController.add(list)
            let view = WKWebView(frame: .zero, configuration: config)
            view.navigationDelegate = self
            view.uiDelegate = self
            view.allowsBackForwardNavigationGestures = true
            self.webView = view
            view.load(URLRequest(url: self.home))
        }
    }

    func refreshStatus() {
        guard !clearing else { return }
        readGeneration += 1
        let generation = readGeneration
        store.httpCookieStore.getAllCookies { [weak self] cookies in
            guard let self, !self.clearing, self.readGeneration == generation else { return }
            // Only a status and expiration date leave this callback. No token logging/export.
            self.state = NHKLoginState.inspect(cookies)
        }
    }

    func clearLogin() {
        guard !clearing else { return }
        clearing = true
        readGeneration += 1
        state = .signedOut
        webView?.stopLoading()
        webView?.loadHTMLString("", baseURL: nil)
        store.removeData(ofTypes: WKWebsiteDataStore.allWebsiteDataTypes(), modifiedSince: .distantPast) { [weak self] in
            guard let self else { return }
            self.clearing = false
            self.message = "このアプリのログイン情報を消去しました。"
            self.webView?.load(URLRequest(url: self.home))
            self.refreshStatus()
        }
    }

    func cookiesDidChange(in cookieStore: WKHTTPCookieStore) { refreshStatus() }

    func currentAccessToken() async -> String? {
        guard !clearing else { return nil }
        let generation = readGeneration
        let cookies = await store.httpCookieStore.allCookies()
        guard !clearing, generation == readGeneration else { return nil }
        return NHKLoginState.accessToken(cookies)
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        let localSubframe = navigationAction.targetFrame?.isMainFrame == false &&
            ["about", "blob", "data"].contains(url.scheme ?? "")
        guard url.scheme == "https" || url.absoluteString == "about:blank" || localSubframe else {
            message = "この画面では HTTPS のページを開いてください。"
            decisionHandler(.cancel)
            return
        }
        if url.host == "licence.hsk.st.nhk" || url.host == "license.hsk.st.nhk" {
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        // Keep new-window login links in the same app-owned session.
        if navigationAction.targetFrame == nil { webView.load(navigationAction.request) }
        return nil
    }

    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) { loading = true }
    func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
        pageHost = webView.url?.host ?? "www.web.nhk"
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        loading = false
        pageHost = webView.url?.host ?? "www.web.nhk"
        message = "NHK ONE のページでログインしてください。"
        refreshStatus()
    }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { failed(error) }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { failed(error) }
    private func failed(_ error: Error) {
        if (error as NSError).code == NSURLErrorCancelled { return }
        loading = false
        // Error descriptions may contain URLs with authentication parameters.
        message = "ページを開けませんでした。ネット接続を確認して再読み込みしてください。"
    }
}

private struct NHKWebView: NSViewRepresentable {
    let webView: WKWebView
    func makeNSView(context: Context) -> WKWebView { webView }
    func updateNSView(_ nsView: WKWebView, context: Context) {}
}

struct NHKLoginView: View {
    @ObservedObject var login: NHKLogin
    private let timer = Timer.publish(every: 30, on: .main, in: .common).autoconnect()
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("NHK ONE にログイン").font(.title2.bold())
                    Text("ログイン情報は、このアプリ専用に保存されます。").font(.callout).foregroundStyle(.secondary)
                }
                Spacer()
                Button("完了") { login.refreshStatus(); login.isPresented = false }
            }
            HStack {
                Button { login.webView?.goBack() } label: { Image(systemName: "chevron.left") }
                    .help("前のページ")
                Button { login.webView?.reload() } label: { Image(systemName: "arrow.clockwise") }
                    .help("再読み込み")
                Label(login.pageHost, systemImage: "globe").font(.callout)
                Spacer()
                if login.loading { ProgressView().controlSize(.small) }
                Text(login.state.title).font(.callout)
            }.disabled(login.clearing)
            if let webView = login.webView {
                NHKWebView(webView: webView).clipShape(RoundedRectangle(cornerRadius: 8))
            } else {
                Spacer()
                Text("ログイン画面を準備しています…").frame(maxWidth: .infinity)
                Spacer()
            }
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text(login.message)
                    Text("この画面はログイン専用です。番組の再生・保存にはまだ対応していません。")
                }.font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("このアプリのログインを消去", role: .destructive) { login.clearLogin() }
                    .disabled(login.clearing)
            }
        }.padding(20).frame(minWidth: 880, minHeight: 660)
            .onAppear { login.prepare() }
            .onReceive(timer) { _ in login.refreshStatus() }
    }
}
