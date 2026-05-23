#!/usr/bin/env bash
# video-pipeline: локальная студия без Telegram
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f pyproject.toml ]]; then
  echo "ERROR: запусти из корня video-pipeline." >&2
  exit 1
fi

if [[ ! -d .venv ]]; then
  echo "ERROR: venv не найден. pip install -e '.[dev]'" >&2
  exit 1
fi

if [[ -f .env ]] && ! grep -q '^TELEGRAM_ENABLED=' .env 2>/dev/null; then
  echo 'TELEGRAM_ENABLED=false' >> .env
fi

export TELEGRAM_ENABLED=false
export PATH="${HOME}/.local/bin:${PATH}"

echo "==> video-pipeline studio (без Telegram)"
echo "    API: http://127.0.0.1:8765"
echo "    UI:  cd web && npm run dev  -> http://localhost:3000"
echo ""

exec .venv/bin/python3 -m app.main
