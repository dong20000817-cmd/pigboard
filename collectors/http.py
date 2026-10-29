# -*- coding: utf-8 -*-
"""最小 HTTP 工具：仅用标准库，带重试与超时，避免为看板引入额外依赖。

关于代理（macOS 特有坑）
------------------------
`urllib.request.urlopen` 用的是默认 opener，而 macOS 上默认 opener 会通过
`_scproxy` 读取「系统代理」设置。本机装过 Clash / Surge 这类代理软件时，
只要代理进程状态不对（节点挂掉、规则错配），**所有采集会一起失败**，
而且报错是 `SSL: UNEXPECTED_EOF_WHILE_READING` 这种看不出根因的样子。

本看板的数据源全部是国内站点，直连最稳，所以这里默认**绕过系统代理**；
万一某天直连不通（例如人在境外），会自动再用系统代理兜底重试一次。
需要强制走代理时：`PIGBOARD_USE_PROXY=1 python3 pipeline.py`。
"""

import gzip
import io
import json
import os
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

socket.setdefaulttimeout(20)  # SSL 握手也受此约束

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

_DIRECT = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=_CTX)
)
_via_proxy = None


def _forced_proxy():
    return os.environ.get("PIGBOARD_USE_PROXY") == "1"


def _proxy_opener():
    """走系统代理的 opener（懒加载；没有配置代理时等价于直连）。"""
    global _via_proxy
    if _via_proxy is None:
        _via_proxy = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_CTX))
    return _via_proxy


def _has_system_proxy():
    try:
        return bool(urllib.request.getproxies())
    except Exception:  # noqa: BLE001 - 读系统代理失败就当没有
        return False


def _fetch(req, opener, timeout):
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        return resp.status, raw


def request(url, data=None, headers=None, timeout=25, retries=3, backoff=1.5):
    """发起 HTTP 请求，返回 (status, bytes)。失败按 backoff 重试。"""
    hdrs = {
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Connection": "close",
    }
    if headers:
        hdrs.update(headers)

    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")

    req = urllib.request.Request(url, data=body, headers=hdrs)
    primary = _proxy_opener() if _forced_proxy() else _DIRECT
    fallback = _proxy_opener() if not _forced_proxy() else _DIRECT

    last_err = None
    for attempt in range(retries):
        try:
            return _fetch(req, primary, timeout)
        except urllib.error.HTTPError as e:
            raw = b""
            try:
                raw = e.read()
            except Exception:
                pass
            # 4xx（除 429）通常重试无意义，直接返回让上层判断
            if e.code not in (429, 500, 502, 503, 504):
                return e.code, raw
            last_err = e
        except Exception as e:  # noqa: BLE001 - 网络层异常统一重试
            last_err = e
        if attempt < retries - 1:
            time.sleep(backoff ** attempt)

    # 直连全挂时，若系统配了代理，再用代理兜底一次（反之亦然）
    if _has_system_proxy() or _forced_proxy():
        try:
            return _fetch(req, fallback, timeout)
        except Exception as e:  # noqa: BLE001
            last_err = f"{last_err} / 代理兜底: {e}"

    raise RuntimeError(f"请求失败 {url}: {last_err}")


def get_json(url, headers=None, timeout=25, retries=3):
    status, raw = request(url, headers=headers, timeout=timeout, retries=retries)
    if status != 200:
        raise RuntimeError(f"HTTP {status} for {url}")
    return json.loads(raw.decode("utf-8", "replace"))


def post_json(url, data, headers=None, timeout=25, retries=3):
    status, raw = request(url, data=data, headers=headers, timeout=timeout, retries=retries)
    if status != 200:
        raise RuntimeError(f"HTTP {status} for {url}")
    return json.loads(raw.decode("utf-8", "replace"))
