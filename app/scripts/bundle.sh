#!/usr/bin/env bash
# Build Morphine.app (menu bar app + the bundled `morphine` CLI) into ./dist.
#   app/scripts/bundle.sh            # build
#   app/scripts/bundle.sh --install  # build, copy to /Applications, link CLI into ~/.local/bin
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PKG="$ROOT/app/MorphineBar"
DIST="$ROOT/dist"
APP="$DIST/Morphine.app"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/morphine/__init__.py")"

echo "→ building MorphineBar $VERSION"
swift build -c release --package-path "$PKG"
BIN="$(swift build -c release --package-path "$PKG" --show-bin-path)/MorphineBar"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/morphine"
cp "$BIN" "$APP/Contents/MacOS/MorphineBar"

# Bundle the CLI so the app works without a separate install.
cp -R "$ROOT/bin" "$APP/Contents/Resources/morphine/"
rsync -a --exclude '__pycache__' "$ROOT/morphine" "$APP/Contents/Resources/morphine/"
[ -f "$ROOT/app/Resources/AppIcon.icns" ] && cp "$ROOT/app/Resources/AppIcon.icns" "$APP/Contents/Resources/"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Morphine</string>
  <key>CFBundleDisplayName</key><string>Morphine</string>
  <key>CFBundleIdentifier</key><string>dev.morphine.bar</string>
  <key>CFBundleExecutable</key><string>MorphineBar</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHumanReadableCopyright</key><string>MIT License</string>
</dict>
</plist>
PLIST

# Ad-hoc signature so Gatekeeper/launch services accept the local build.
codesign --force --deep --sign - "$APP" >/dev/null
echo "✓ $APP"

if [[ "${1:-}" == "--install" ]]; then
  rm -rf /Applications/Morphine.app
  cp -R "$APP" /Applications/
  mkdir -p "$HOME/.local/bin"
  ln -sf /Applications/Morphine.app/Contents/Resources/morphine/bin/morphine "$HOME/.local/bin/morphine"
  echo "✓ installed /Applications/Morphine.app"
  echo "✓ CLI linked at ~/.local/bin/morphine"
  case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) echo "! ~/.local/bin is not on your PATH, so 'morphine' won't be found. Run:"
       echo "    echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc && source ~/.zshrc" ;;
  esac
  open /Applications/Morphine.app
fi
