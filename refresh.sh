#!/usr/bin/env bash
# 每 30 分钟巡检一次数据；只有数据/官方发布发生变化时才会改写看板。
#
# 由 launchd 调用（见 com.pigboard.refresh.plist），也可手动执行。
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT" || exit 1
mkdir -p logs

# 优先使用 DSH 捆绑 Python（含 openpyxl，用于解析官方 XLSX），否则回退系统 python3
PY=""
for cand in \
  "/Users/dqy/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3" \
  "$(command -v python3 2>/dev/null)"
do
  if [ -n "$cand" ] && [ -x "$cand" ]; then PY="$cand"; break; fi
done
[ -n "$PY" ] || { echo "未找到可用 python3" >&2; exit 1; }

STAMP="$(date '+%Y-%m-%d %H:%M:%S')"
OUT="$("$PY" pipeline.py 2>&1)"
RC=$?
echo "[$STAMP] rc=$RC" >> logs/refresh.log
echo "$OUT" >> logs/refresh.log

# 日志滚动：保留最近 2000 行
if [ "$(wc -l < logs/refresh.log)" -gt 2000 ]; then
  tail -n 1200 logs/refresh.log > logs/refresh.log.tmp && mv logs/refresh.log.tmp logs/refresh.log
fi
exit $RC
