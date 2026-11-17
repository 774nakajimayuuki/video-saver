#!/bin/zsh
set -eu
cd "${0:A:h}"
APP="$PWD/Video Saver.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Helpers" .build-cache
swiftc -parse-as-library -target "$(uname -m)-apple-macos14.0" -module-cache-path "$PWD/.build-cache" VideoSaver.swift MP4Plan.swift NHKLogin.swift NHKLoginState.swift NHKConnection.swift -o "$APP/Contents/MacOS/VideoSaver"
mkdir -p "$APP/Contents/Resources/NHKTrial"
cp experiments/nhk-auth-trial/nhk-init-data.bin "$APP/Contents/Resources/NHKTrial/init-data.bin"
cp nhk-connection.mjs "$APP/Contents/Resources/NHKTrial/nhk-connection.mjs"
/usr/bin/xcrun clang++ -std=c++17 -Wall -Wextra -Werror -O1 -target "$(uname -m)-apple-macos14.0" \
    -I experiments/minimal-cdm-trial/include experiments/minimal-cdm-trial/license_probe.cpp -o "$APP/Contents/Helpers/nhk-license-probe"
codesign --force --sign - "$APP/Contents/Helpers/nhk-license-probe"
cp yt-dlp "$APP/Contents/Helpers/yt-dlp"
chmod +x "$APP/Contents/Helpers/yt-dlp"
# Optional local trial component. Never discover or import another app at runtime.
if [[ -d "$PWD/local-components/WidevineCdm" ]]; then
    mkdir -p "$APP/Contents/Resources"
    ditto "$PWD/local-components/WidevineCdm" "$APP/Contents/Resources/WidevineCdm"
fi
if command -v node >/dev/null 2>&1; then
    # Keep the bundle self-contained; an external symlink invalidates strict signing.
    cp "$(command -v node)" "$APP/Contents/Helpers/node.new"
    mv -f "$APP/Contents/Helpers/node.new" "$APP/Contents/Helpers/node"
    codesign --force --sign - "$APP/Contents/Helpers/node"
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
</dict></plist>
PLIST
codesign --force --sign - "$APP"
