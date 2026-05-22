#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/modules/tradingagents-runtime"
PYTHON_BIN="${TRADINGAGENTS_BOOTSTRAP_PYTHON:-python3.12}"

case "${1:-}" in
  -h|--help)
    echo "Usage: npm run tradingagents:install"
    echo "       bash scripts/install-tradingagents-runtime.sh [--check]"
    exit 0
    ;;
  --check)
    "$RUNTIME_DIR/.venv/bin/python" -m pip show tradingagents
    exit 0
    ;;
esac

mkdir -p "$RUNTIME_DIR"
"$PYTHON_BIN" -m venv "$RUNTIME_DIR/.venv"
PIP_DISABLE_PIP_VERSION_CHECK=1 "$RUNTIME_DIR/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
PIP_DISABLE_PIP_VERSION_CHECK=1 "$RUNTIME_DIR/.venv/bin/python" -m pip install -r "$RUNTIME_DIR/requirements.txt"

echo "TradingAgents runtime ready: $RUNTIME_DIR/.venv/bin/python"
