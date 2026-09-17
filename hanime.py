#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hanime.tv 支持模块
==================

为什么单独一个文件
------------------
hanime.tv 和 RedTube / PornHub 是**两种不同的站点形态**：

    RedTube / PornHub        服务端渲染 HTML，按页抓；yt-dlp 能解析直链
    hanime.tv                Astro 前端 + JSON API；yt-dlp **不支持**；
                             取流要过一道 WASM 签名的握手

而且 hanime 的 API 变更很频繁（v8 → v11，`search.htv-services.com` 已 NXDOMAIN）。
所以这里把 hanime 的一切都隔离进来，**目录接口挂了就整体降级**，不牵连另外两个站。

链路概览
--------
    目录（无需认证、无需代理）
        GET https://guest.freeanimehentai.net/api/v11/search_hvs
        → 一次性返回全量索引（约 3400 条 / 4MB），带结构化 tags
        → 搜索、标签过滤、排序、分页全部在本地做

    取流（需要签名）
        POST https://auth.hanime.tv/api/v11/handshake
        ← 头：x-signature-version: web2 / x-signature / x-time
        → 响应头 x-token（AES-256-GCM 封装的 JSON）→ sources[] → HLS 地址

    签名
        hanime 把签名算法打在一个 Emscripten 模块里（vendor.<hash>.min.js）。
        本项目**不逆向、也不分发**它：由 hanime_signer.js 在 Node 里给那个模块
        喂一个假浏览器环境，直接问它要签名。

关于广告
--------
握手响应里带 `is_preroll_enabled` / `preroll_urls`（实测指向 TrafficJunky 等）。
本模块**只取 `sources`，完全忽略这些字段**，所以预播广告不会出现。

关于账号
--------
游客只能拿到 ≤720p。1080p 那一档返回的是 `kind: "promotion"` 且 `src` 为空的
推广占位。本项目**不做账号功能**，因此 1080p 不可用。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import queue
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import httpx

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

HERE = Path(__file__).resolve().parent

CATALOG_URL = "https://guest.freeanimehentai.net/api/v11/search_hvs"
HANDSHAKE_URL = "https://auth.hanime.tv/api/v11/handshake"
HOME_URL = "https://hanime.tv/"
VIDEO_BASE = "https://hanime.tv/videos/hentai/"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

VENDOR_RE = re.compile(r'src="(https://hanime-cdn\.com/js/vendor\.[^"]+\.js)"')

# 目录缓存时长（参考实现取 30~60 分钟）
CATALOG_TTL = int(os.environ.get("ADSKIPER_HANIME_CATALOG_TTL", "1800"))
# 流地址缓存时长。实测同一 slug 多次握手返回的地址是稳定的，
# 但服务端 token 有寿命，保守取 90 分钟。
STREAM_TTL = int(os.environ.get("ADSKIPER_HANIME_STREAM_TTL", "5400"))

# hanime 那套 "insecure" 消息封装：AES-256-GCM，
# 密钥 = SHA-256(固定串)，附加认证数据 = 另一个固定串。
MSG_KEY = hashlib.sha256(b"htv-insecure-handshake-v1").digest()
MSG_AAD = b"htv-insecure-v1"


# --------------------------------------------------------------------------- #
# 消息封装（纯 Python，无第三方加密依赖以外的要求）
# --------------------------------------------------------------------------- #

def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64u_decode(text: str) -> bytes:
    s = str(text).replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s)


def seal_message(obj: dict[str, Any]) -> str:
    """把 dict 封成 hanime 的 token。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    iv = os.urandom(12)
    sealed = AESGCM(MSG_KEY).encrypt(iv, json.dumps(obj).encode("utf-8"), MSG_AAD)
    data, tag = sealed[:-16], sealed[-16:]
    envelope = {
        "v": 1,
        "alg": "AES-256-GCM",
        "iv": _b64u(iv),
        "tag": _b64u(tag),
        "data": _b64u(data),
    }
    return _b64u(json.dumps(envelope).encode("utf-8"))


def open_message(token: str) -> dict[str, Any]:
    """解开 hanime 的 token（握手响应的 x-token 头也是这个格式）。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    envelope = json.loads(_b64u_decode(token).decode("utf-8"))
    ciphertext = _b64u_decode(envelope["data"]) + _b64u_decode(envelope["tag"])
    plain = AESGCM(MSG_KEY).decrypt(_b64u_decode(envelope["iv"]), ciphertext, MSG_AAD)
    return json.loads(plain.decode("utf-8"))


# --------------------------------------------------------------------------- #
# 签名助手（Node 子进程）
# --------------------------------------------------------------------------- #

class SignerError(RuntimeError):
    pass


class Signer:
    """hanime_signer.js 的常驻包装。

    懒启动：第一次要用的时候才起 Node。进程挂了会自动重启一次。
    线程安全：用锁串行化 stdin/stdout 的往返（Node 侧是行式协议，本来也不能并发）。
    """

    def __init__(self, vendor_path: Path, proxy: str | None = None) -> None:
        self.vendor_path = Path(vendor_path)
        self.proxy = proxy
        self._proc: subprocess.Popen | None = None
        self._lines: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._ready = False

    # -- 生命周期 ---------------------------------------------------------- #

    def _spawn(self) -> None:
        script = HERE / "hanime_signer.js"
        if not script.exists():
            raise SignerError(f"找不到签名助手：{script}")
        if not self.vendor_path.exists():
            raise SignerError(f"找不到 vendor 文件：{self.vendor_path}")

        self._proc = subprocess.Popen(
            ["node", str(script), str(self.vendor_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        self._lines = queue.Queue()
        threading.Thread(target=self._pump, args=(self._proc.stdout,), daemon=True).start()
        threading.Thread(target=self._drain_stderr, args=(self._proc.stderr,), daemon=True).start()
        self._ready = False

    def _pump(self, stream) -> None:
        try:
            for line in stream:
                self._lines.put(line.rstrip("\n"))
        except Exception:  # noqa: BLE001
            pass

    def _drain_stderr(self, stream) -> None:
        try:
            for _ in stream:
                pass
        except Exception:  # noqa: BLE001
            pass

    def _read(self, timeout: float) -> dict[str, Any] | None:
        try:
            return json.loads(self._lines.get(timeout=timeout))
        except (queue.Empty, json.JSONDecodeError):
            return None

    def _ensure_ready(self) -> None:
        if self._proc is None or self._proc.poll() is not None:
            self._spawn()

        if self._ready:
            return

        deadline = time.time() + 25.0
        while time.time() < deadline:
            msg = self._read(timeout=max(0.2, deadline - time.time()))
            if msg is None:
                if self._proc.poll() is not None:
                    raise SignerError("签名助手进程已退出")
                continue
            if msg.get("event") == "ready":
                self._ready = True
                return
            if not msg.get("ok"):
                raise SignerError(f"签名助手启动失败：{msg.get('error')}")
        raise SignerError("签名助手启动超时")

    def close(self) -> None:
        proc = self._proc
        if not proc:
            return
        try:
            if proc.stdin:
                proc.stdin.write(json.dumps({"cmd": "exit"}) + "\n")
                proc.stdin.flush()
            proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        finally:
            self._proc = None
            self._ready = False

    # -- 取签名 ------------------------------------------------------------ #

    def sign(self) -> tuple[str, str]:
        """返回 (signature, time)。失败抛 SignerError。"""
        with self._lock:
            try:
                self._ensure_ready()
            except SignerError:
                # 自重试一次：进程可能只是崩了
                self.close()
                self._ensure_ready()

            assert self._proc and self._proc.stdin
            self._proc.stdin.write(json.dumps({"cmd": "sign"}) + "\n")
            self._proc.stdin.flush()

            deadline = time.time() + 12.0
            while time.time() < deadline:
                msg = self._read(timeout=max(0.2, deadline - time.time()))
                if msg is None:
                    continue
                if msg.get("ok"):
                    return str(msg["signature"]), str(msg["time"])
                raise SignerError(f"取签名失败：{msg.get('error')}")
            raise SignerError("取签名超时")


# --------------------------------------------------------------------------- #
# vendor 文件（运行时抓取，不随项目分发）
# --------------------------------------------------------------------------- #

_vendor_lock = threading.Lock()
_vendor_url_cache: str | None = None


def vendor_cache_dir() -> Path:
    d = Path(os.environ.get("ADSKIPER_HANIME_CACHE", HERE / ".cache" / "hanime"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def fetch_vendor(client: httpx.Client, force: bool = False) -> tuple[Path, str]:
    """抓 hanime 首页 → 定位 vendor.<hash>.js → 下载到本地缓存。

    返回 (本地路径, vendor URL)。

    刻意**不把这份文件打包进项目**：
      * 它是 hanime.tv 的专有代码，不属于本项目也不属于任何 MIT 参考项目；
      * 运行时抓取还能在站点更新签名时自动跟上。
    """
    global _vendor_url_cache
    with _vendor_lock:
        cache_dir = vendor_cache_dir()
        target = cache_dir / "vendor.js"
        url_file = cache_dir / "vendor.url"

        home = client.get(HOME_URL, headers={"User-Agent": UA, "Accept": "text/html"})
        home.raise_for_status()
        m = VENDOR_RE.search(home.text)
        if not m:
            raise SignerError("hanime 首页里找不到 vendor.js 地址（站点可能改版）")
        vendor_url = m.group(1)

        if (
            not force
            and target.exists()
            and url_file.exists()
            and url_file.read_text(encoding="utf-8").strip() == vendor_url
        ):
            return target, vendor_url

        r = client.get(vendor_url, headers={"User-Agent": UA, "Referer": HOME_URL})
        r.raise_for_status()
        target.write_bytes(r.content)
        url_file.write_text(vendor_url, encoding="utf-8")
        _vendor_url_cache = vendor_url
        return target, vendor_url


# --------------------------------------------------------------------------- #
# 目录（全量索引 + 本地过滤）
# --------------------------------------------------------------------------- #

_catalog_lock = threading.Lock()
_catalog_cache: dict[str, Any] = {"ts": 0.0, "items": [], "by_slug": {}, "tags": {}}


def _fmt_views(n: Any) -> str:
    try:
        v = int(n)
    except (TypeError, ValueError):
        return ""
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.0f}K"
    return str(v)


def _normalize(entry: dict[str, Any]) -> dict[str, Any]:
    slug = str(entry.get("slug") or entry.get("id") or "")
    cover = entry.get("cover_url") or ""
    poster = entry.get("poster_url") or ""
    thumbs = [u for u in (cover, poster) if u]
    return {
        "id": slug,
        "title": entry.get("name") or slug,
        "url": VIDEO_BASE + slug,
        "duration": "",                      # 目录里没有时长字段
        "views": _fmt_views(entry.get("views")),
        "rating": "",
        "thumbnail": cover,
        "thumbnails": thumbs,
        "preview": "",
        "uploader": entry.get("brand") or "",
        "tags": list(entry.get("tags") or []),
        "released_unix": entry.get("released_at_unix") or 0,
    }


def load_catalog(client: httpx.Client, force: bool = False) -> dict[str, Any]:
    """拉全量目录并按 TTL 缓存。返回 {items, by_slug, tags}。"""
    with _catalog_lock:
        now = time.time()
        if not force and _catalog_cache["items"] and now - _catalog_cache["ts"] < CATALOG_TTL:
            return _catalog_cache

        r = client.get(
            CATALOG_URL,
            headers={"User-Agent": UA, "Accept": "application/json",
                     "Referer": HOME_URL, "Origin": "https://hanime.tv"},
        )
        r.raise_for_status()
        raw = r.json()
        data = raw.get("data") or []

        items = [_normalize(x) for x in data if x.get("slug")]
        by_slug = {x["id"]: x for x in items}

        tag_counts: dict[str, int] = {}
        for x in items:
            for t in x["tags"]:
                tag_counts[t] = tag_counts.get(t, 0) + 1

        _catalog_cache.update({
            "ts": now,
            "items": items,
            "by_slug": by_slug,
            "tags": tag_counts,
        })
        return _catalog_cache


def catalog_items() -> list[dict[str, Any]]:
    return _catalog_cache["items"]


def catalog_tags() -> dict[str, int]:
    return _catalog_cache["tags"]


def filter_catalog(
    q: str = "",
    tags: list[str] | None = None,
    page: int = 1,
    per_page: int = 36,
    sort: str = "",
) -> dict[str, Any]:
    """在本地做关键词 + 标签（AND）+ 分页。

    标签是**精确匹配**（hanime 的 tags 是结构化元数据，不是自由文本），
    这也正是这个站点比 tube 站强的地方：真的能做多标签 AND。
    """
    items = _catalog_cache["items"]
    q = (q or "").strip().lower()
    want = [t.strip().lower() for t in (tags or []) if t and t.strip()]

    out = items
    if want:
        out = [x for x in out if all(t in {y.lower() for y in x["tags"]} for t in want)]
    if q:
        terms = [t for t in re.split(r"[\s+]+", q) if t]
        out = [
            x for x in out
            if all(t in x["title"].lower() or t in x["id"].lower() for t in terms)
        ]

    if sort == "views":
        out = sorted(out, key=lambda x: _views_num(x), reverse=True)
    elif sort == "new":
        out = sorted(out, key=lambda x: x.get("released_unix") or 0, reverse=True)

    total = len(out)
    total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    return {
        "results": out[start:start + per_page],
        "total": total,
        "page": page,
        "total_pages": total_pages,
    }


def _views_num(x: dict[str, Any]) -> int:
    s = str(x.get("views") or "0")
    try:
        if s.endswith("M"):
            return int(float(s[:-1]) * 1_000_000)
        if s.endswith("K"):
            return int(float(s[:-1]) * 1_000)
        return int(s)
    except ValueError:
        return 0


# --------------------------------------------------------------------------- #
# 取流
# --------------------------------------------------------------------------- #

_stream_lock = threading.Lock()
_stream_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def resolve_streams(
    client: httpx.Client,
    signer: Signer,
    slug: str,
    force: bool = False,
) -> list[dict[str, Any]]:
    """握手拿 HLS 地址。返回 [{height,label,url,kind}]，按清晰度降序。

    只保留 `src` 非空的档位 —— 1080p 那个 `kind: "promotion"` 的推广占位会被丢掉。
    """
    with _stream_lock:
        now = time.time()
        if not force:
            hit = _stream_cache.get(slug)
            if hit and now - hit[0] < STREAM_TTL:
                return hit[1]

        signature, stime = signer.sign()
        token = seal_message({
            "timestamp_unix": int(time.time()),
            "directive": "htv_player_handshake",
            "slug": slug,
        })

        r = client.post(
            HANDSHAKE_URL,
            headers={
                "User-Agent": UA,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Referer": HOME_URL,
                "Origin": "https://hanime.tv",
                "x-signature-version": "web2",
                "x-signature": signature,
                "x-time": str(stime),
            },
            json={"token": token},
        )
        if r.status_code != 200:
            raise SignerError(f"握手失败 HTTP {r.status_code}: {r.text[:160]}")

        xt = r.headers.get("x-token")
        if not xt:
            raise SignerError("握手响应里没有 x-token")

        payload = open_message(xt)
        # 注意：payload 里的 is_preroll_enabled / preroll_urls 是服务端预播广告，
        # 本模块**故意不使用**，所以不会出现广告。
        out: list[dict[str, Any]] = []
        for s in payload.get("sources") or []:
            src = s.get("src") or s.get("url") or ""
            if not src:
                continue          # 1080p 推广占位就在这里被滤掉
            url = src if src.startswith("http") else "https://hanime.tv" + src
            out.append({
                "height": s.get("height") or 0,
                "label": s.get("label") or f"{s.get('height') or '?'}p",
                "url": url,
                "kind": s.get("kind") or "",
                "width": s.get("width") or 0,
            })
        out.sort(key=lambda x: x["height"], reverse=True)
        _stream_cache[slug] = (now, out)
        return out


def slug_from_url(url: str) -> str | None:
    """从 hanime 视频页 URL 里取 slug。"""
    m = re.search(r"hanime\.tv/videos/hentai/([A-Za-z0-9\-_]+)", url)
    return m.group(1) if m else None
