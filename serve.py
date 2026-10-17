# -*- coding: utf-8 -*-
"""本地私有部署：单进程静态服务（供本机与 iPhone 访问）。

  /                      -> web/index.html （看板）
  /manifest.webmanifest  -> PWA 清单
  /icons/...             -> PWA 图标
  /data/...              -> 快照 / 心跳 / 变更留痕 / 登记库 / 历史 CSV
                         （**仅此目录对外开放**，项目源码一律 404）
  /data/history/...      -> 历史 CSV（可直接下载）

访问模式
--------
  python3 serve.py                 仅本机 127.0.0.1（最安全）
  python3 serve.py --lan           监听 0.0.0.0，iPhone 在同一 Wi-Fi 下可访问
  python3 serve.py --lan --port 8787

安全说明
--------
--lan 会把看板暴露给**同一局域网**内的设备（家庭/办公 Wi-Fi）。看板本身是只读静态页，
不含任何写接口，但请注意：
  * 不要在公共 Wi-Fi 下使用 --lan；
  * 需要跨网络访问时，优先用 Tailscale 之类的私有组网，而不是端口转发到公网。

注意：iOS Safari 要求 HTTPS 才允许 Service Worker，因此经 http://<局域网IP> 访问时
无法离线缓存；「添加到主屏幕」仍可全屏运行，但需 Mac 可达。
"""

import argparse
import functools
import http.server
import os
import socket
import socketserver
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")


def lan_ip():
    """取本机在局域网中的 IPv4（不产生实际流量）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.1.1", 1))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    # 只允许暴露这两棵树，项目源码（pipeline.py / config.py / *.plist 等）一律不可访问
    WEB_TREE = WEB
    DATA_TREE = os.path.join(ROOT, "data")

    def _safe(self, base, rel):
        """在 base 下安全解析 rel，越界或不存在返回 None。"""
        if not rel:
            return None
        cand = os.path.normpath(os.path.join(base, rel))
        if cand != base and not cand.startswith(base + os.sep):
            return None
        return cand if os.path.isfile(cand) else None

    def translate_path(self, path):
        from urllib.parse import unquote

        clean = unquote(path.split("?", 1)[0].split("#", 1)[0])
        if clean in ("/", "/index.html"):
            return os.path.join(WEB, "index.html")

        rel = clean.lstrip("/")

        # /data/xxx  ->  <项目>/data/xxx
        if rel == "data" or rel.startswith("data/"):
            hit = self._safe(self.DATA_TREE, rel[len("data"):].lstrip("/"))
            if hit:
                return hit
            return os.path.join(ROOT, "__denied__")

        # 其余一律在 web/ 下解析（manifest、icons 等 PWA 资源）
        hit = self._safe(self.WEB_TREE, rel)
        return hit or os.path.join(ROOT, "__denied__")

    def guess_type(self, path):
        if str(path).endswith(".webmanifest"):
            return "application/manifest+json"
        return super().guess_type(path)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def log_message(self, fmt, *args):
        # 静默部署：只在出现 4xx/5xx 时记录
        if str(args[1] if len(args) > 1 else "").startswith(("4", "5")):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default=None, help="显式指定监听地址，覆盖 --lan")
    ap.add_argument("--lan", action="store_true", help="监听 0.0.0.0，供 iPhone 在同 Wi-Fi 下访问")
    args = ap.parse_args()

    host = args.host or ("0.0.0.0" if args.lan else "127.0.0.1")

    if not os.path.exists(os.path.join(WEB, "index.html")):
        raise SystemExit("web/index.html 不存在，请先运行：python3 pipeline.py")

    with Server((host, args.port), Handler) as httpd:
        print("生猪市场看板已私有部署")
        print(f"  本机访问：   http://127.0.0.1:{args.port}/")
        if host == "0.0.0.0":
            ip = lan_ip()
            if ip:
                print(f"  iPhone 访问： http://{ip}:{args.port}/   （需与 Mac 在同一 Wi-Fi）")
                print(f"  提示：在 iPhone Safari 打开上面地址 → 分享 → 添加到主屏幕，即可全屏使用")
            else:
                print("  iPhone 访问： http://<本机局域网IP>:%d/  （未能自动探测到 IP）" % args.port)
            print("  ⚠ 当前已对同一局域网开放，请勿在公共 Wi-Fi 下运行")
        else:
            print("  仅本机可访问（如需 iPhone 访问请加 --lan）")
        print(f"  服务目录：   {ROOT}")
        print("  Ctrl+C 停止")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止")


if __name__ == "__main__":
    main()
