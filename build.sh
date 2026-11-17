#!/bin/zsh
set -eu
cd "${0:A:h}"
# 署名は毎回同じローカル証明書で行う（画面収録の許可を作り直し後も保つため）。
# 無ければ ad-hoc 署名になり、作り直すたびに許可が外れる。キーチェーンアクセスの証明書アシスタントで、種類「コード署名」の自己署名証明書をこの名前で作る。
SIGN="VideoSaver Local Signing"
security find-certificate -c "$SIGN" >/dev/null 2>&1 || SIGN="-"
APP="$PWD/Video Saver.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Helpers" .build-cache
swiftc -parse-as-library -target "$(uname -m)-apple-macos14.0" -module-cache-path "$PWD/.build-cache" VideoSaver.swift MP4Plan.swift AudioRecorder.swift -o "$APP/Contents/MacOS/VideoSaver"
cp yt-dlp "$APP/Contents/Helpers/yt-dlp"
chmod +x "$APP/Contents/Helpers/yt-dlp"
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
