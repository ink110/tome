#!/usr/bin/env bash
# 兼容旧调用：转交给 transcribe.py（自动选择本地 / 在线 provider）。
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$DIR/transcribe.py" "$@"
