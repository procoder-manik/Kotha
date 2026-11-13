#!/usr/bin/env bash
# Build Kotha for macOS on a Mac:   ./build_mac.sh
# Output: output/Kotha-Mac-AppleSilicon.dmg (M1/M2/M3/M4) or output/Kotha-Mac-Intel.dmg
set -euo pipefail
cd "$(dirname "$0")"

case "$(uname -m)" in arm64) ARCH="AppleSilicon" ;; *) ARCH="Intel" ;; esac
python3 -m venv .venv-mac
source .venv-mac/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller

rm -rf build dist
python -m PyInstaller --noconfirm --clean kotha_mac.spec

# Ad-hoc sign so Apple Silicon will run it (no paid Apple Developer account needed)
codesign --force --deep --sign - dist/Kotha.app

mkdir -p output dmg_staging
rm -rf dmg_staging/*
cp -R dist/Kotha.app dmg_staging/
ln -s /Applications dmg_staging/Applications
hdiutil create -volname "Kotha" -srcfolder dmg_staging -ov -format UDZO "output/Kotha-Mac-${ARCH}.dmg"
rm -rf dmg_staging

echo
echo "Done: output/Kotha-Mac-${ARCH}.dmg"
echo "Open the .dmg and drag Kotha to Applications. First launch: right-click Kotha > Open."
