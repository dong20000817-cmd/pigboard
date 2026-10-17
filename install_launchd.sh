#!/usr/bin/env bash
# 一次性安装：把「定时巡检」与「常驻看板服务」注册为 macOS 用户级 launchd 任务。
#
#   bash install_launchd.sh              安装（巡检 + 看板服务，仅本机访问）
#   bash install_launchd.sh lan          安装（巡检 + 看板服务，开放给同一 Wi-Fi 的 iPhone）
#   bash install_launchd.sh uninstall    全部卸载
#
# ⚠ macOS TCC 限制：launchd 任务无法读取 ~/Desktop。安装后若 logs/launchd.err.log
#   出现 "Operation not permitted"，请把项目移出 Desktop（如 ~/PigBoard），
#   或在「系统设置 → 隐私与安全性 → 完全磁盘访问权限」中加入 /bin/bash 与 python3。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LA="$HOME/Library/LaunchAgents"
REFRESH_LABEL="com.pigboard.refresh"
SERVE_LABEL="com.pigboard.serve"
MODE="${1:-install}"

chmod +x "$ROOT/refresh.sh" "$ROOT/serve_daemon.sh" "$ROOT/watch_loop.sh" 2>/dev/null || true

bootout() { launchctl bootout "gui/$(id -u)/$1" 2>/dev/null || true; }

if [ "$MODE" = "uninstall" ]; then
  bootout "$REFRESH_LABEL"; bootout "$SERVE_LABEL"
  rm -f "$LA/$REFRESH_LABEL.plist" "$LA/$SERVE_LABEL.plist"
  echo "已卸载：${REFRESH_LABEL}、${SERVE_LABEL}"
  exit 0
fi

mkdir -p "$LA" "$ROOT/logs"

install_one() {
  local label="$1" src="$ROOT/$1.plist" dst="$LA/$1.plist"
  sed "s|__ROOT__|$ROOT|g" "$src" > "$dst"
  bootout "$label"
  if ! launchctl bootstrap "gui/$(id -u)" "$dst" 2>/dev/null; then
    launchctl load -w "$dst"
  fi
  echo "  已安装：${label}"
}

echo "安装目录：$LA"
install_one "$REFRESH_LABEL"

if [ "$MODE" = "lan" ]; then
  python3 - "$ROOT" "$LA/$SERVE_LABEL.plist" <<'PY'
import sys, plistlib
root, dst = sys.argv[1], sys.argv[2]
src = root + "/com.pigboard.serve.plist"
d = plistlib.load(open(src, "rb"))
d["ProgramArguments"] = ["/bin/bash", root + "/serve_daemon.sh"]
d["WorkingDirectory"] = root
d.setdefault("EnvironmentVariables", {})["SERVE_MODE"] = "lan"
d["EnvironmentVariables"]["SERVE_PORT"] = "8787"
d["StandardOutPath"] = root + "/logs/serve.out.log"
d["StandardErrorPath"] = root + "/logs/serve.err.log"
plistlib.dump(d, open(dst, "wb"))
print("  看板服务已设为局域网模式（iPhone 可访问）")
PY
  bootout "$SERVE_LABEL"
  launchctl bootstrap "gui/$(id -u)" "$LA/$SERVE_LABEL.plist" 2>/dev/null || launchctl load -w "$LA/$SERVE_LABEL.plist"
  echo "  已安装：${SERVE_LABEL}（lan 模式）"
else
  install_one "$SERVE_LABEL"
fi

echo
echo "巡检时刻：07:45 / 09:30 / 12:30 / 15:30 / 17:30 / 21:30（只在数据变化时改写看板）"
echo "手动立刻跑一次： bash ${ROOT}/refresh.sh"
echo "查看状态：      launchctl list | grep pigboard"
echo "巡检日志：      tail -f ${ROOT}/logs/refresh.log"
echo "服务日志：      tail -f ${ROOT}/logs/serve.err.log"
echo "卸载：          bash ${ROOT}/install_launchd.sh uninstall"
