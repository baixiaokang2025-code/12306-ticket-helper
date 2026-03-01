#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_DIR"

if [ ! -d ".venv-build" ]; then
  python3 -m venv .venv-build
fi

source .venv-build/bin/activate
pip install -r requirements.txt -r requirements-build.txt

pyinstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "12306余票助手" \
  main.py

APP_PATH="$PROJECT_DIR/dist/12306余票助手.app"
TARGET_PATH="$PROJECT_DIR/12306余票助手.app"
if [ -d "$APP_PATH" ]; then
  rm -rf "$TARGET_PATH"
  cp -R "$APP_PATH" "$TARGET_PATH"
fi

echo "构建完成：$APP_PATH"
echo "已复制到：$TARGET_PATH"
