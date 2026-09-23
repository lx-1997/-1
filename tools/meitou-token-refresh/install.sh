#!/bin/bash
# 安装/升级 Mac 端美投 token 自动续期：
#   1. 复制脚本到 ~/.meitou-refresh/bin
#   2. 安装 playwright-core（私有 npm cache，避开 ~/.npm 权限问题）
#   3. 生成 config.json（已存在则不覆盖）
#   4. 安装并加载两个 launchd 任务（每 6h 续期 / 每 30min 巡检）
#
# 需要写 ~/.meitou-refresh 与 ~/Library/LaunchAgents（生产改动，谨慎执行）。
set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNTIME_HOME="${MEITOU_REFRESH_HOME:-$HOME/.meitou-refresh}"
NODE_BIN="$(command -v node || echo /opt/homebrew/bin/node)"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"

echo "==> 安装目录: $RUNTIME_HOME"
mkdir -p "$RUNTIME_HOME"/{bin,logs,state}
cp -f "$SRC_DIR"/refresh.mjs "$SRC_DIR"/onboard.mjs "$SRC_DIR"/monitor.mjs "$SRC_DIR"/apply-token.mjs "$SRC_DIR"/set-refresh.mjs "$RUNTIME_HOME/bin/"
rm -rf "$RUNTIME_HOME/bin/lib"
cp -R "$SRC_DIR/lib" "$RUNTIME_HOME/bin/lib"
cp -f "$SRC_DIR/run.sh" "$RUNTIME_HOME/bin/run.sh"
chmod +x "$RUNTIME_HOME/bin/run.sh"

if [ ! -f "$RUNTIME_HOME/package.json" ]; then
  cat > "$RUNTIME_HOME/package.json" <<'JSON'
{
  "name": "meitou-refresh-runtime",
  "private": true,
  "type": "module",
  "dependencies": { "playwright-core": "^1.50.0" }
}
JSON
fi

echo "==> 安装 playwright-core"
(cd "$RUNTIME_HOME" && npm install --no-audit --no-fund --cache "$RUNTIME_HOME/.npm-cache" >/dev/null)

if [ ! -f "$RUNTIME_HOME/config.json" ]; then
  cp -f "$SRC_DIR/config.example.json" "$RUNTIME_HOME/config.json"
  echo "==> 已生成 $RUNTIME_HOME/config.json（按需填告警渠道）"
else
  echo "==> 保留已有 config.json"
fi

echo "==> 安装 launchd 任务"
mkdir -p "$LAUNCH_AGENTS"
for label in com.meitou.token-refresh com.meitou.mirror-monitor; do
  sed -e "s|__HOME__|$HOME|g" -e "s|__NODE__|$NODE_BIN|g" \
      "$SRC_DIR/plists/$label.plist" > "$LAUNCH_AGENTS/$label.plist"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$LAUNCH_AGENTS/$label.plist"
  echo "    loaded $label"
done

echo
echo "完成。下一步："
echo "  1) 一次性登录（Apple 登录 + 双重认证）：$RUNTIME_HOME/bin/run.sh onboard"
echo "  2) 手动巡检：$RUNTIME_HOME/bin/run.sh refresh --force"
echo "  3) 查看任务：launchctl list | grep meitou"
echo "  4) 日志：$RUNTIME_HOME/logs/"
