#!/bin/bash
# Builds ArbitrageOS.app (Dock icon + menu bar item), installs it in
# ~/Applications, pins it to the Dock and opens it. Safe to run again.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
APP="$HOME/Applications/ArbitrageOS.app"
BUILD="$ROOT/build"
echo "Building ArbitrageOS.app from $ROOT"

if ! command -v swiftc >/dev/null 2>&1; then
  echo "The Swift compiler is missing. Run: xcode-select --install   then run this again."
  read -r -p "Press Return to close."; exit 1
fi

mkdir -p "$BUILD" "$HOME/Applications"
swiftc -O -o "$BUILD/ArbitrageOS" "$ROOT/macapp/main.swift" \
  -framework Cocoa -framework WebKit

# stop a running copy before replacing it
osascript -e 'if application id "com.arbitrageos.app" is running then tell application id "com.arbitrageos.app" to quit' >/dev/null 2>&1 || true
sleep 1
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BUILD/ArbitrageOS" "$APP/Contents/MacOS/ArbitrageOS"
cp "$ROOT/macapp/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>ArbitrageOS</string>
  <key>CFBundleDisplayName</key><string>ArbitrageOS</string>
  <key>CFBundleIdentifier</key><string>com.arbitrageos.app</string>
  <key>CFBundleExecutable</key><string>ArbitrageOS</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.business</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
  <key>ArbOSRoot</key><string>$ROOT</string>
</dict>
</plist>
EOF
codesign --force --sign - "$APP" >/dev/null 2>&1 || true
touch "$APP"

# pin to the Dock once
if ! defaults read com.apple.dock persistent-apps 2>/dev/null | grep -q "ArbitrageOS.app"; then
  defaults write com.apple.dock persistent-apps -array-add \
    "<dict><key>tile-data</key><dict><key>file-data</key><dict><key>_CFURLString</key><string>file://$APP/</string><key>_CFURLStringType</key><integer>15</integer></dict></dict></dict>"
  killall Dock
  sleep 1
fi

# if the plain launcher left a server running, the app reuses it
open "$APP"
echo
echo "Done: ArbitrageOS is in your Dock and menu bar (the tag icon)."
echo "You can close this window."
