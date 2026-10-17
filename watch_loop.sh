#!/usr/bin/env bash
# 会话内巡检循环：每 INTERVAL 秒跑一次 refresh.sh。
# 用途：在 launchd 定时任务因 macOS TCC 无法读取 ~/Desktop 时，保证看板仍持续自更新。
# 由 DSH 后台作业托管；进程结束即停止。
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INTERVAL="${1:-1800}"
mkdir -p "$ROOT/logs"
echo "[$(date '+%F %T')] watch_loop 启动，间隔 ${INTERVAL}s" >> "$ROOT/logs/watch_loop.log"
while true; do
  bash "$ROOT/refresh.sh" >/dev/null 2>&1 || true
  sleep "$INTERVAL"
done
