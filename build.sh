#!/bin/zsh
set -eu
cd "${0:A:h}"
# 署名は毎回同じローカル証明書で行う（画面収録の許可を作り直し後も保つため）。
# 無ければ ad-hoc 署名になり、作り直すたびに許可が外れる。キーチェーンアクセスの証明書アシスタントで、種類「コード署名」の自己署名証明書をこの名前で作る。
SIGN="VideoSaver Local Signing"
security find-certificate -c "$SIGN" >/dev/null 2>&1 || SIGN="-"
APP="$PWD/Video Saver.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Helpers" .build-cache
swiftc -parse-as-library -target "$(uname -m)-apple-macos14.0" -module-cache-path "$PWD/.build-cache" VideoSaver.swift MP4Plan.swift AudioRecorder.swift LiveRecorder.swift -o "$APP/Contents/MacOS/VideoSaver"
cp yt-dlp "$APP/Contents/Helpers/yt-dlp"
chmod +x "$APP/Contents/Helpers/yt-dlp"
# ffmpeg / ffprobe は tools/ に置けばアプリに同梱する（無ければ Homebrew 等のものを使う）。
for t in ffmpeg ffprobe; do
    [[ -x "tools/$t" ]] && cp "tools/$t" "$APP/Contents/Helpers/$t"
done
# YouTube の取得に JS ランタイムが必要。yt-dlp は node 22 以上を対応版とみなす（20 以下は "unsupported"）。
# NODE_BIN で指定でき、無ければ fnm の入っている版と PATH の node から 22 以上のものを選ぶ。
# 外部へのリンクだと署名が壊れるので、アプリ内へコピーする。
pick_node() {
    local c v
    [[ -n "${NODE_BIN:-}" ]] && { print -r -- "$NODE_BIN"; return; }
    for c in "$HOME"/.local/share/fnm/node-versions/v*/installation/bin/node(N) "$(command -v node 2>/dev/null)"; do
        [[ -x "$c" ]] || continue
        v=$("$c" --version 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/')
        [[ "$v" =~ '^[0-9]+$' && "$v" -ge 22 ]] && { print -r -- "$c"; return; }
    done
}
NODE_SRC="$(pick_node)"
if [[ -n "$NODE_SRC" ]]; then
    cp "$NODE_SRC" "$APP/Contents/Helpers/node.new"
    mv -f "$APP/Contents/Helpers/node.new" "$APP/Contents/Helpers/node"
    codesign --force --sign "$SIGN" "$APP/Contents/Helpers/node"
else
    echo "警告: node 22 以上が見つかりません。YouTube の取得に支障が出る場合があります（NODE_BIN で指定できます）。" >&2
fi
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>VideoSaver</string>
<key>CFBundleIdentifier</key><string>local.videosaver.app</string>
<key>CFBundleName</key><string>Video Saver</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>0.3.1</string>
<key>LSMinimumSystemVersion</key><string>14.0</string>
<key>NSHighResolutionCapable</key><true/>
<key>NSScreenCaptureUsageDescription</key><string>再生中の音声を録音するために使用します。映像は保存しません。</string>
</dict></plist>
PLIST
codesign --force --sign "$SIGN" "$APP"
