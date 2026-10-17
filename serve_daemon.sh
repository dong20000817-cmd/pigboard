#!/usr/bin/env bash
# 常驻看板服务（由 launchd 的 com.pigboard.serve 托管，KeepAlive 会自动重启）。
# 监听模式由 SERVE_MODE 控制：
#   SERVE_MODE=lan  -> 0.0.0.0，iPhone 可在同一 Wi-Fi 下访问
#   SERVE_MODE=local-> 127.0.0.1，仅本机（默认）
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT" || exit 1
mkdir -p logs

PY=""
for cand in \
  "/Users/dqy/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3" \
  "$(command -v python3 2>/dev/null)"
do
  if [ -n "$cand" ] && [ -x "$cand" ]; then PY="$cand"; break; fi
done
[ -n "$PY" ] || { echo "未找到可用 python3" >&2; exit 1; }

PORT="${SERVE_PORT:-8787}"
MODE="${SERVE_MODE:-local}"

# 看板不存在则先构建一次
[ -f web/index.html ] || "$PY" pipeline.py >> logs/serve.log 2>&1

if [ "$MODE" = "lan" ]; then
  exec "$PY" serve.py --lan --port "$PORT"
else
  exec "$PY" serve.py --port "$PORT"
fi
