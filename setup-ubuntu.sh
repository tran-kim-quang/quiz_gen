#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Cần cài Python 3.10 trở lên." >&2
  exit 1
fi
if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
  echo "Python 3.10 trở lên là bắt buộc." >&2
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  echo "Cần cài Node.js 20 trở lên." >&2
  exit 1
fi
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
if (( NODE_MAJOR < 20 )); then
  echo "Node.js 20 trở lên là bắt buộc; hiện tại là $(node --version)." >&2
  exit 1
fi

if [[ ! -d "$REPO_DIR/.venv" ]]; then
  python3 -m venv "$REPO_DIR/.venv"
fi

"$REPO_DIR/.venv/bin/python" -m pip install --upgrade pip
"$REPO_DIR/.venv/bin/python" -m pip install -r "$REPO_DIR/requirements.txt"

(
  cd "$REPO_DIR"
  npm ci
)

echo "Cài đặt hoàn tất. Chạy giao diện: $REPO_DIR/.venv/bin/python $REPO_DIR/app.py"
