#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hanime.tv 取流链路 —— 独立验证脚本

用途
----
在把 hanime.tv 支持接入主程序之前，先用这个脚本确认整条链路能跑通：

    1. 通过代理抓 hanime.tv 首页，正则出 vendor.<hash>.min.js 的地址
    2. 下载那份 vendor（**不随项目分发**，只落到本地缓存）
    3. 起 Node 签名助手，拿一组 {signature, time}
    4. 用 AES-256-GCM 封装握手 token
    5. POST https://auth.hanime.tv/api/v11/handshake
    6. 解密响应头 x-token，取出 sources（HLS 地址）

跑通即说明"游客 ≤720p"这条路可行。

运行
----
    python tools/hanime_probe.py                  # 用一个内置 slug
    python tools/hanime_probe.py <slug>           # 指定 slug

依赖
----
    pip install httpx cryptography
    以及本机有 node（签名助手要用）
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SIGNER_JS = ROOT / "hanime_signer.js"
CACHE_DIR = HERE / "hanime_cache"
VENDOR_FILE = CACHE_DIR / "vendor.js"

PROXY = os.environ.get("ADSKIPER_PROXY", "http://127.0.0.1:7890").strip()

# 启动签名助手用的 Node 可执行文件。**必须和 hanime.py 读同一个变量**：
# Node 若是用 nvm / Homebrew 装的，而本脚本又从 GUI / launchd 之类的地方启动，
# 那个进程的 PATH 里往往没有 node，这时用
# ADSKIPER_NODE_BIN=/opt/homebrew/bin/node 指过去即可。
# 探针是出问题时第一个要跑的工具，这里若不认这个变量，就会出现
# 「主程序正常、探针报找不到 node」的反向结论 —— 和它的用途正好相反。
NODE_BIN = os.environ.get("ADSKIPER_NODE_BIN", "node").strip() or "node"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

HOME_URL = "https://hanime.tv/"
VENDOR_RE = re.compile(r'src="(https://hanime-cdn\.com/js/vendor\.[^"]+\.js)"')
HANDSHAKE_URL = "https://auth.hanime.tv/api/v11/handshake"

# hanime 那套"insecure"消息封装：AES-256-GCM，密钥是固定串的 SHA-256，
# 附加认证数据（AAD）是另一个固定串。名字取自站点自己的 app_init bundle
# （encryptInsecureMessage / decryptInsecureMessage）。
MSG_KEY = hashlib.sha256(b"htv-insecure-handshake-v1").digest()
MSG_AAD = b"htv-insecure-v1"

DEFAULT_SLUG = "yabai-fukushuu-yami-site-2"


# --------------------------------------------------------------------------- #
# 消息封装
# --------------------------------------------------------------------------- #

def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64u_decode(text: str) -> bytes:
    s = str(text).replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s)


def seal_message(obj: dict) -> str:
    """把 dict 封成 hanime 的 token：base64url(JSON({v,alg,iv,tag,data}))。"""
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


def open_message(token: str) -> dict:
    """解开 hanime 的 token（握手响应的 x-token 头也是这个格式）。"""
    envelope = json.loads(_b64u_decode(token).decode("utf-8"))
    ciphertext = _b64u_decode(envelope["data"]) + _b64u_decode(envelope["tag"])
    plain = AESGCM(MSG_KEY).decrypt(_b64u_decode(envelope["iv"]), ciphertext, MSG_AAD)
    return json.loads(plain.decode("utf-8"))


# --------------------------------------------------------------------------- #
# vendor 下载
# --------------------------------------------------------------------------- #

def fetch_vendor(client: httpx.Client, force: bool = False) -> Path:
    """抓 hanime 首页 → 找到 vendor.<hash>.js → 下载到本地缓存。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    print("[1/5] 抓 hanime.tv 首页找 vendor 地址 …")
    home = client.get(HOME_URL, headers={"User-Agent": UA, "Accept": "text/html"})
    home.raise_for_status()
    m = VENDOR_RE.search(home.text)
    if not m:
        raise RuntimeError("首页里没找到 vendor.js 的地址（站点可能改版了）")
    vendor_url = m.group(1)
    print(f"      vendor = {vendor_url}")

    if VENDOR_FILE.exists() and not force:
        meta = CACHE_DIR / "vendor.url"
        if meta.exists() and meta.read_text(encoding="utf-8").strip() == vendor_url:
            print(f"      缓存命中（{VENDOR_FILE.stat().st_size / 1024:.0f} KB）")
            return VENDOR_FILE

    print("[2/5] 下载 vendor（不随项目分发，仅本地缓存）…")
    r = client.get(vendor_url, headers={"User-Agent": UA, "Referer": HOME_URL})
    r.raise_for_status()
    VENDOR_FILE.write_bytes(r.content)
    (CACHE_DIR / "vendor.url").write_text(vendor_url, encoding="utf-8")
    print(f"      已保存 {VENDOR_FILE.stat().st_size / 1024:.0f} KB → {VENDOR_FILE}")
    return VENDOR_FILE


# --------------------------------------------------------------------------- #
# 签名助手
# --------------------------------------------------------------------------- #

class Signer:
    """Node 签名助手的常驻子进程包装。"""

    def __init__(self, vendor_path: Path) -> None:
        self.proc = subprocess.Popen(
            [NODE_BIN, str(SIGNER_JS), str(vendor_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.lines: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

    def _pump_stdout(self) -> None:
        assert self.proc.stdout
        for line in self.proc.stdout:
            self.lines.put(line.rstrip("\n"))

    def _pump_stderr(self) -> None:
        assert self.proc.stderr
        for line in self.proc.stderr:
            print("      [node stderr]", line.rstrip()[:220])

    def read(self, timeout: float) -> dict | None:
        try:
            return json.loads(self.lines.get(timeout=timeout))
        except queue.Empty:
            return None
        except json.JSONDecodeError:
            return None

    def wait_ready(self, timeout: float = 25.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = self.read(timeout=max(0.2, deadline - time.time()))
            if not msg:
                if self.proc.poll() is not None:
                    return False
                continue
            if msg.get("event") == "ready":
                return True
            if not msg.get("ok"):
                print(f"      签名助手报错: {msg.get('error')}")
                return False
        return False

    def sign(self, timeout: float = 12.0) -> tuple[str, str] | None:
        assert self.proc.stdin
        self.proc.stdin.write(json.dumps({"cmd": "sign"}) + "\n")
        self.proc.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = self.read(timeout=max(0.2, deadline - time.time()))
            if not msg:
                continue
            if msg.get("ok"):
                return msg["signature"], msg["time"]
            print(f"      sign 失败: {msg.get('error')}")
            return None
        return None

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.write(json.dumps({"cmd": "exit"}) + "\n")
                self.proc.stdin.flush()
            self.proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            self.proc.kill()


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def print_connect_hint(proxy: str | None) -> None:
    """连不上时，按两种出口方案分别给出下一步。

    原始报错只有一句 "Connection refused"，看不出是代理配置的问题 ——
    而这恰恰是主程序出问题时最需要工具帮上忙的地方。
    """
    if proxy:
        print(f"      代理 {proxy} 连不上。先确认代理客户端在跑；")
        print("      若用的是系统级 VPN（Shadowrocket / Surge），应改直连：")
        print("        ADSKIPER_PROXY= python tools/hanime_probe.py")
    else:
        print("      当前是直连模式。检查网络，或确认 VPN / 代理已连上；")
        print("      也可以显式指定出口：")
        print("        ADSKIPER_PROXY=http://127.0.0.1:7890 python tools/hanime_probe.py")


def print_node_missing_hint() -> None:
    """Popen 找不到 node 时给一句人话，而不是裸 traceback。"""
    print(f"\n✗ 找不到 {NODE_BIN} —— hanime.tv 取流需要 Node.js 18+。")
    print("      先确认它在 PATH 里（node --version 能打印版本）；")
    print("      若 node 是 nvm / Homebrew 装的，用绝对路径指过去：")
    print("        ADSKIPER_NODE_BIN=/opt/homebrew/bin/node python tools/hanime_probe.py")


def main() -> int:
    slug = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SLUG
    proxy = PROXY or None
    print(f"代理 = {proxy or '(直连)'}    slug = {slug}\n")

    with httpx.Client(proxy=proxy, timeout=60.0, follow_redirects=True, trust_env=False) as client:
        try:
            vendor = fetch_vendor(client)
        except Exception as e:  # noqa: BLE001
            print(f"\n✗ 取 vendor 失败: {type(e).__name__}: {e}")
            if isinstance(e, httpx.TransportError):
                print_connect_hint(proxy)
            return 1

        print("[3/5] 启动签名助手并取一组签名 …")
        try:
            signer = Signer(vendor)
        except FileNotFoundError:
            # hanime.py 那边把 Popen 的 FileNotFoundError 包成了 SignerError，
            # 不然异常会一路逃逸成裸 500。这里保持一致，别吐 traceback。
            print_node_missing_hint()
            return 1
        try:
            if not signer.wait_ready():
                print("✗ 签名助手启动失败")
                return 1
            for i in range(3):
                got = signer.sign()
                if got:
                    sig, t = got
                    print(f"      #{i + 1} signature={sig[:28]}… time={t} len={len(sig)}")
                    break
                time.sleep(0.5)
            else:
                print("✗ 三次都没拿到签名")
                return 1

            print("\n[4/5] 封装 token 并 POST 握手 …")
            token = seal_message({
                "timestamp_unix": int(time.time()),
                "directive": "htv_player_handshake",
                "slug": slug,
            })
            print(f"      token={token[:44]}… len={len(token)}")

            headers = {
                "User-Agent": UA,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Referer": "https://hanime.tv/",
                "Origin": "https://hanime.tv",
                "x-signature-version": "web2",
                "x-signature": sig,
                "x-time": str(t),
            }
            r = client.post(HANDSHAKE_URL, headers=headers, json={"token": token})
            print(f"      HTTP {r.status_code}   x-token={'有' if r.headers.get('x-token') else '无'}")
            if r.status_code != 200:
                print(f"      响应: {r.text[:300]}")
                return 1

            xt = r.headers.get("x-token")
            if not xt:
                print("      ✗ 响应里没有 x-token 头")
                return 1

            print("\n[5/5] 解密 x-token 取流地址 …")
            payload = open_message(xt)
            print(f"      解开后的键: {list(payload)}")
            sources = payload.get("sources") or []
            print(f"      sources: {len(sources)} 个")
            for s in sources:
                url = s.get("url") or s.get("src") or ""
                print(f"        height={s.get('height') or s.get('label')}  {url[:120]}")

            extra = {k: v for k, v in payload.items() if k != "sources"}
            if extra:
                print(f"      其它字段: {json.dumps(extra, ensure_ascii=False)[:300]}")

            if not sources:
                print("\n⚠ 握手成功但 sources 为空 —— 账号功能已排除，可能是该条目受限或 API 又变了")
                return 2

            print("\n✓ 整条链路跑通：签名 → 握手 → 解密 → 拿到 HLS 地址")
            return 0
        finally:
            signer.close()


if __name__ == "__main__":
    sys.exit(main())
