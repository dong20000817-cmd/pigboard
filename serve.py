# -*- coding: utf-8 -*-
"""本地私有部署：静态服务 + 一键更新接口（供本机与 iPhone 访问）。

  /                      -> web/index.html （看板）
  /manifest.webmanifest  -> PWA 清单
  /icons/...             -> PWA 图标
  /data/...              -> 快照 / 心跳 / 变更留痕 / 登记库 / 历史 CSV
                         （**仅此目录对外开放**，项目源码一律 404）
  /api/status            -> GET  当前更新状态（JSON）
  /api/refresh           -> POST 立即跑一次巡检（异步，立即返回）

访问模式
--------
  python3 serve.py                 仅本机 127.0.0.1（最安全）
  python3 serve.py --lan           监听 0.0.0.0，iPhone 在同一 Wi-Fi 下可访问
  python3 serve.py --lan --port 8787

一键更新接口的安全边界
----------------------
--lan 模式下同一局域网内任何设备都能触发一次巡检，因此：
  * 必须带 X-PigBoard-Refresh: 1 请求头；跨站页面发这类请求会先触发 CORS 预检，
    本服务不回应预检，浏览器会直接拦下，杜绝「打开别的网页被偷偷触发」；
  * 若带 Origin 头，必须与本站 Host 一致；
  * 两次触发之间至少间隔 RATE_LIMIT 秒；
  * 真正的采集在 refresh.sh 里跑，与定时任务共用同一把文件锁，不会并发写数据。

注意：iOS Safari 要求 HTTPS 才允许 Service Worker，因此经 http://<局域网IP> 访问时
无法离线缓存；「添加到主屏幕」仍可全屏运行，但需 Mac 可达。
"""

import argparse
import json
import os
import socket
import socketserver
import subprocess
import sys
import threading
import time
import http.server
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote, urlsplit

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
CST = timezone(timedelta(hours=8))

RATE_LIMIT = 20          # 两次手动更新的最小间隔（秒）

_state = {
    "proc": None,
    "started_at": None,
    "finished_at": None,
    "rc": None,
    "last_start": 0.0,
}
_state_lock = threading.Lock()


def _iso():
    return datetime.now(CST).replace(microsecond=0).isoformat()


def _load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _pipeline_busy():
    """探测 refresh.sh / pipeline.py 是否正持有数据写锁。"""
    try:
        import fcntl
    except ImportError:
        return False
    path = os.path.join(ROOT, "data", ".pipeline.lock")
    try:
        f = open(path, "a+")
    except OSError:
        return False
    try:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(f, fcntl.LOCK_UN)
        return False
    finally:
        f.close()


def _run_collector():
    """在后台跑 refresh.sh（与 launchd 定时任务完全同一条路径）。"""
    log_path = os.path.join(ROOT, "logs", "refresh_button.log")
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        out = open(log_path, "a", encoding="utf-8")
    except OSError:
        out = subprocess.DEVNULL
    proc = subprocess.Popen(
        ["/bin/bash", os.path.join(ROOT, "refresh.sh")],
        cwd=ROOT,
        stdout=out,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    with _state_lock:
        _state["proc"] = proc
        _state["started_at"] = _iso()
        _state["finished_at"] = None
        _state["rc"] = None
    threading.Thread(target=_wait_collector, args=(proc,), daemon=True).start()


def _wait_collector(proc):
    rc = proc.wait()
    with _state_lock:
        _state["rc"] = rc
        _state["finished_at"] = _iso()
        _state["proc"] = None


def _running():
    with _state_lock:
        proc = _state["proc"]
    return proc is not None and proc.poll() is None


def _start_refresh():
    """返回 (HTTP 状态码, 响应体)。"""
    now = time.time()
    with _state_lock:
        if _state["proc"] is not None and _state["proc"].poll() is None:
            return 409, {"ok": False, "reason": "running", "message": "已经有一次更新在跑，等它结束。"}
        if now - _state["last_start"] < RATE_LIMIT:
            wait = int(RATE_LIMIT - (now - _state["last_start"])) + 1
            return 429, {"ok": False, "reason": "rate_limited", "message": f"刚更新过，{wait} 秒后可再点。"}
        _state["last_start"] = now

    # pipeline 自己有一把锁；被定时任务占着时直接告诉用户，不要空跑一次
    if _pipeline_busy():
        return 409, {"ok": False, "reason": "busy", "message": "定时巡检正在进行，请等它跑完。"}

    _run_collector()
    return 202, {"ok": True, "message": "已开始更新", "started_at": _state["started_at"]}


def _status():
    hb = _load_json(os.path.join(ROOT, "data", "heartbeat.json"), {}) or {}
    with _state_lock:
        started = _state["started_at"]
        finished = _state["finished_at"]
        rc = _state["rc"]
    running = _running()
    elapsed = None
    if running and started:
        try:
            elapsed = int((datetime.now(CST) - datetime.fromisoformat(started)).total_seconds())
        except ValueError:
            elapsed = None
    return {
        "ok": True,
        "server": "pigboard",
        "now": _iso(),
        "running": running,
        "busy": running or _pipeline_busy(),
        "started_at": started,
        "finished_at": finished,
        "rc": rc,
        "elapsed_s": elapsed,
        "last_check": hb.get("last_check"),
        "data_changed": hb.get("data_changed"),
        "last_data_change": hb.get("last_data_change"),
        "schedule": hb.get("schedule"),
        "auto_update": hb.get("auto_update"),
        "history": hb.get("history"),
        "source_errors": hb.get("source_errors"),
        "snapshot_generated_at": hb.get("snapshot_generated_at"),
    }


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    # 只允许暴露这两棵树，项目源码（pipeline.py / config.py / *.plist 等）一律不可访问
    WEB_TREE = WEB
    DATA_TREE = os.path.join(ROOT, "data")

    # ---------------- 安全与响应工具 ----------------

    def _safe(self, base, rel):
        """在 base 下安全解析 rel，越界或不存在返回 None。"""
        if not rel:
            return None
        cand = os.path.normpath(os.path.join(base, rel))
        if cand != base and not cand.startswith(base + os.sep):
            return None
        return cand if os.path.isfile(cand) else None

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _same_origin_ok(self):
        """拒绝跨站触发：必须带自定义头；带 Origin 时还要与本站一致。"""
        if self.headers.get("X-PigBoard-Refresh") != "1":
            return False
        origin = self.headers.get("Origin")
        if not origin:
            return True
        try:
            return urlsplit(origin).netloc == (self.headers.get("Host") or "")
        except ValueError:
            return False

    # ---------------- 路由 ----------------

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/status":
            self._json(200, _status())
            return
        if path == "/api/refresh":
            self._json(405, {"ok": False, "message": "请用 POST 触发更新。"})
            return
        super().do_GET()

    def do_POST(self):
        path = urlsplit(self.path).path
        if path != "/api/refresh":
            self._json(404, {"ok": False, "message": "not found"})
            return
        # 读完请求体，避免连接被重置
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(min(length, 1 << 16))
        except (ValueError, OSError):
            pass
        if not self._same_origin_ok():
            self._json(403, {"ok": False, "message": "forbidden"})
            return
        try:
            code, payload = _start_refresh()
        except Exception as e:  # noqa: BLE001 - 接口异常不能把服务带崩
            code, payload = 500, {"ok": False, "message": f"{type(e).__name__}: {e}"}
        self._json(code, payload)

    def do_OPTIONS(self):
        # 不回应 CORS 预检：跨站页面无法带自定义头调用本接口
        self.send_response(204)
        self.send_header("Allow", "GET, POST")
        self.end_headers()

    def translate_path(self, path):
        clean = unquote(urlsplit(path).path)
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
        code = str(args[1]) if len(args) > 1 else ""
        if code.startswith(("4", "5")) and "/api/" not in str(args[0]):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


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
                print("  提示：在 iPhone Safari 打开上面地址 → 分享 → 添加到主屏幕，即可全屏使用")
            else:
                print("  iPhone 访问： http://<本机局域网IP>:%d/  （未能自动探测到 IP）" % args.port)
            print("  ⚠ 当前已对同一局域网开放，请勿在公共 Wi-Fi 下运行")
        else:
            print("  仅本机可访问（如需 iPhone 访问请加 --lan）")
        print(f"  一键更新：   POST http://127.0.0.1:{args.port}/api/refresh")
        print(f"  服务目录：   {ROOT}")
        print("  Ctrl+C 停止")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止")


if __name__ == "__main__":
    main()
