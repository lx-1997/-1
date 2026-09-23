#!/bin/bash
# 统一入口（launchd 与手工都用它）
#   run.sh refresh [--force]   巡检并按需续期
#   run.sh onboard             一次性登录向导（有头 Chrome）
#   run.sh monitor             巡检镜像站状态并告警
set -uo pipefail

RUNTIME_HOME="${MEITOU_REFRESH_HOME:-$HOME/.meitou-refresh}"
NODE_BIN="${MEITOU_NODE_BIN:-$(command -v node || echo /opt/homebrew/bin/node)}"
export MEITOU_REFRESH_HOME="$RUNTIME_HOME"

mode="${1:-refresh}"
shift || true
cd "$RUNTIME_HOME" || { echo "缺少运行目录 $RUNTIME_HOME，请先跑 install.sh"; exit 78; }

case "$mode" in
  refresh) exec "$NODE_BIN" "$RUNTIME_HOME/bin/refresh.mjs" "$@" ;;
  onboard) exec "$NODE_BIN" "$RUNTIME_HOME/bin/onboard.mjs" "$@" ;;
  monitor) exec "$NODE_BIN" "$RUNTIME_HOME/bin/monitor.mjs" "$@" ;;
  apply-token) exec "$NODE_BIN" "$RUNTIME_HOME/bin/apply-token.mjs" "$@" ;;
  set-refresh) exec "$NODE_BIN" "$RUNTIME_HOME/bin/set-refresh.mjs" "$@" ;;
  *) echo "用法: run.sh refresh|onboard|monitor|apply-token|set-refresh"; exit 64 ;;
esac
