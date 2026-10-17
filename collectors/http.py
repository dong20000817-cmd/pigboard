# -*- coding: utf-8 -*-
"""最小 HTTP 工具：仅用标准库，带重试与超时，避免为看板引入额外依赖。"""

import gzip
import io
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


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

    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                return resp.status, raw
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
