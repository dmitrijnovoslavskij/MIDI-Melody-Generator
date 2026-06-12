#!/bin/bash
# build_mac.sh
# Run this on a macOS machine to build MIDI Gen.app
# Requires Python 3.8+ (brew install python3)

set -e
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "===== Building MIDI Gen.app ====="

# Install PyInstaller if needed
python3 -c "import PyInstaller" 2>/dev/null || {
    echo "Installing PyInstaller..."
    python3 -m pip install pyinstaller -q
}

# Build
pyinstaller \
    --onefile \
    --windowed \
    --name "MIDI Gen" \
    --icon "$HERE/assets/icon.icns" \
    --add-data "$HERE/assets:assets" \
    "$HERE/launcher.py"

# Result is in dist/MIDI Gen.app
if [ -d "$HERE/dist/MIDI Gen.app" ]; then
    echo ""
    echo "============================================"
    echo " Build successful!"
    echo " Output: dist/MIDI Gen.app"
    echo "============================================"

    # Опционально: подписать если есть Apple Developer сертификат
    # codesign --deep --force --verify --verbose \
    #     --sign "Developer ID Application: YOUR NAME (TEAMID)" \
    #     "$HERE/dist/MIDI Gen.app"
else
    echo "[ERROR] Build failed — check output above"
    exit 1
fi