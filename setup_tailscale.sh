#!/usr/bin/env bash
# 把本机看板接入 Tailscale 私有组网，并打印 iPhone 可用的访问地址。
#
#   bash setup_tailscale.sh           检查并打印访问地址（默认，安全、不阻塞）
#   bash setup_tailscale.sh https     额外尝试开启 HTTPS（需先在管理后台启用证书）
#   bash setup_tailscale.sh status    查看状态
#   bash setup_tailscale.sh off       取消 HTTPS 发布
#
# 重要：本脚本的所有 tailscale 调用都带硬超时，绝不会无限期卡住。
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${SERVE_PORT:-8787}"
ACTION="${1:-show}"
SERVE_TIMEOUT="${SERVE_TIMEOUT:-25}"

# --- 定位 CLI ---------------------------------------------------------------
TS=""
for cand in \
  "/Applications/Tailscale.app/Contents/MacOS/Tailscale" \
  "/usr/local/bin/tailscale" \
  "$(command -v tailscale 2>/dev/null)"
do
  [ -n "$cand" ] && [ -x "$cand" ] && { TS="$cand"; break; }
done

if [ -z "$TS" ]; then
  cat <<'EOF'
❌ 未找到 Tailscale。

安装（安装包已下载好）：
   双击  ~/Downloads/Tailscale-1.104.1-macos.pkg
   装完后从「应用程序」启动并登录，再重跑本脚本。
EOF
  exit 1
fi

# --- 带超时执行（macOS 没有 timeout 命令，自己实现）--------------------------
run_to() {   # run_to <秒> <命令...>
  local limit="$1"; shift
  "$@" & local pid=$!
  local n=0
  while kill -0 "$pid" 2>/dev/null; do
    if [ "$n" -ge "$((limit * 10))" ]; then
      kill -9 "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
      return 124
    fi
    sleep 0.1; n=$((n + 1))
  done
  wait "$pid"; return $?
}

echo "Tailscale CLI : $TS"
echo "看板端口      : $PORT"
echo

# --- 连接状态 ---------------------------------------------------------------
if ! run_to 20 "$TS" status >/tmp/ts_status.txt 2>&1; then
  echo "⚠ 无法读取 Tailscale 状态。请打开 Tailscale 应用确认已登录且处于连接状态。"
  exit 1
fi

SELF_JSON="$(run_to 20 "$TS" status --json 2>/dev/null)"
DNSNAME="$(printf '%s' "$SELF_JSON" | python3 -c "
import json,sys
try: print((json.load(sys.stdin).get('Self') or {}).get('DNSName','').rstrip('.'))
except Exception: print('')")"
TSIP="$(run_to 20 "$TS" ip -4 2>/dev/null | head -1)"

if [ -z "$TSIP" ]; then
  echo "⚠ Tailscale 似乎未连接。请打开 Tailscale 应用登录后重试。"
  exit 1
fi

case "$ACTION" in
  status)
    cat /tmp/ts_status.txt
    echo "--- serve 状态 ---"
    run_to 20 "$TS" serve status 2>&1 | head -20
    exit 0
    ;;
  off)
    run_to 20 "$TS" serve --https=443 off >/dev/null 2>&1 || run_to 20 "$TS" serve off >/dev/null 2>&1
    echo "已取消 HTTPS 发布。"
    exit 0
    ;;
esac

# --- 确认看板服务在跑 -------------------------------------------------------
CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "http://127.0.0.1:${PORT}/" 2>/dev/null)"
if [ "$CODE" != "200" ]; then
  echo "⚠ 本机看板未在 ${PORT} 端口响应（HTTP ${CODE:-无}）。"
  echo "  先启动服务： python3 ${ROOT}/serve.py --lan --port ${PORT}"
  echo "  或安装常驻服务： bash ${ROOT}/install_launchd.sh lan"
  exit 1
fi
echo "✅ 本机看板正常（HTTP 200）"
echo

# --- 主路径：直接打印可用地址（不依赖 HTTPS）--------------------------------
echo "========================================================"
echo " iPhone 访问地址"
echo "========================================================"
if [ -n "$DNSNAME" ]; then
  echo "  ① http://${DNSNAME}:${PORT}/"
fi
echo "  ② http://${TSIP}:${PORT}/"
echo
echo "  iPhone 操作："
echo "    1. 打开 Tailscale App，确认已连接（显示 Connected）"
echo "    2. Safari 打开上面任一地址"
echo "    3. 分享 → 添加到主屏幕 → 桌面出现「生猪看板」图标"
echo
echo "  当前 tailnet 设备："
sed 's/^/    /' /tmp/ts_status.txt
echo

# --- 可选：HTTPS（需要先启用证书，未启用时会明确失败而非卡死）---------------
want_https=0
[ "$ACTION" = "https" ] && want_https=1

if [ "$want_https" -eq 1 ]; then
  echo "--------------------------------------------------------"
  echo " 尝试开启 HTTPS（tailscale serve）"
  echo "--------------------------------------------------------"

  # 1) 先判断 tailnet 是否真的下发了证书域名
  CERTDOMAINS="$(printf '%s' "$SELF_JSON" | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    print(','.join(d.get('CertDomains') or []))
except Exception: print('')")"

  if [ -z "$CERTDOMAINS" ]; then
    cat <<EOF
❌ tailnet 尚未启用 HTTPS 证书（CertDomains 为空）。

   请在浏览器中开启一次：
     1. 打开  https://login.tailscale.com/admin/dns
     2. 页面下方找到「HTTPS Certificates」→ 点击 Enable
     3. 回到终端重跑： bash ${ROOT}/setup_tailscale.sh https

   ⚠ 不开启也完全不影响使用 —— 上面的 http:// 地址已经可以直接用。
EOF
  else
    echo "tailnet 已启用证书域名：${CERTDOMAINS}"
    echo "正在向 Let's Encrypt 申请证书（可能因网络环境失败）……"
    run_to 30 "$TS" cert "$DNSNAME" >/tmp/ts_cert.txt 2>&1
    CERT_RC=$?
    if [ "$CERT_RC" -eq 0 ]; then
      echo "✅ 证书获取成功"
      if run_to "$SERVE_TIMEOUT" "$TS" serve --bg "$PORT" >/tmp/ts_serve.txt 2>&1; then
        echo "✅ HTTPS 已发布： https://${DNSNAME}/"
      else
        echo "⚠ tailscale serve 未成功："
        sed 's/^/    /' /tmp/ts_serve.txt
      fi
    else
      ERRTXT="$(cat /tmp/ts_cert.txt 2>/dev/null)"
      if [ "$CERT_RC" -eq 124 ] || [ -z "$ERRTXT" ]; then
        ERRTXT="申请超时：连接 Let's Encrypt ACME 接口 30 秒无响应（TLS handshake timeout）"
      fi
      echo "❌ 证书申请失败（退出码 ${CERT_RC}）："
      echo "$ERRTXT" | sed 's/^/     /' | head -3
      echo
      if echo "$ERRTXT" | grep -qiE "handshake timeout|acme|Get \"https|i/o timeout|connection reset|timeout|超时"; then
        cat <<EOF
   原因：本机网络无法稳定访问 Let's Encrypt 的 ACME 接口
        （acme-v02.api.letsencrypt.org），TLS 握手被中断。
        这不是配置错误，Tailscale 也只支持 Let's Encrypt，因此本机无法绕过。

   影响：仅「Service Worker 离线缓存」不可用（iOS 要求 HTTPS）。
        看板的 http:// 地址、数据采集、自动更新全部不受影响。

   三条可行出路（按推荐排序）：
     A. 改用云端托管（GitHub Pages / Cloudflare Pages）——
        天生 HTTPS，手机端永不依赖 Mac，同时解决「电脑关机」问题。
        见 docs/云端部署_GitHub-Pages.md
     B. 在网络条件更好的环境（或让 Let's Encrypt 可达）重试本命令。
     C. 接受 http:// 访问：Mac 开机时随时可看，只是没有离线缓存。
EOF
      else
        echo "   （未识别的失败原因，请把上面的错误信息发给我）"
      fi
    fi
  fi
  echo
fi

cat <<EOF
其他命令：
  查看状态      bash ${ROOT}/setup_tailscale.sh status
  尝试 HTTPS    bash ${ROOT}/setup_tailscale.sh https
  取消 HTTPS    bash ${ROOT}/setup_tailscale.sh off
EOF
