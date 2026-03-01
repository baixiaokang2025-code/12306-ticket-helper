#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_NAME="12306余票助手"

VERSION_RAW="${1:-v1.1.0}"
if [[ "$VERSION_RAW" =~ ^v ]]; then
  VERSION_TAG="$VERSION_RAW"
else
  VERSION_TAG="v$VERSION_RAW"
fi
ZIP_NAME="12306-ticket-helper-macos-${VERSION_TAG}.zip"

cd "$PROJECT_DIR"

if [ ! -d ".venv-build" ]; then
  python3 -m venv .venv-build
fi

source .venv-build/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt -r requirements-build.txt

pyinstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "$APP_NAME" \
  main.py

APP_PATH="$PROJECT_DIR/dist/${APP_NAME}.app"
TARGET_PATH="$PROJECT_DIR/${APP_NAME}.app"
ZIP_PATH="$PROJECT_DIR/$ZIP_NAME"
if [ -d "$APP_PATH" ]; then
  rm -rf "$TARGET_PATH"
  cp -R "$APP_PATH" "$TARGET_PATH"
fi

rm -f "$ZIP_PATH"
ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$ZIP_PATH"

echo "构建完成：$APP_PATH"
echo "已复制到：$TARGET_PATH"
echo "已打包：$ZIP_PATH"
