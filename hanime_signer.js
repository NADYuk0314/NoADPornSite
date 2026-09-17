#!/usr/bin/env node
/**
 * hanime.tv 请求签名助手（Node 常驻子进程）
 *
 * 为什么需要它
 * ------------
 * hanime.tv 的取流接口 `POST https://auth.hanime.tv/api/v11/handshake` 要求三个头：
 *
 *     x-signature-version: web2
 *     x-signature:         <window.ssignature>
 *     x-time:              <window.stime>
 *
 * 这两个值由 hanime 自己打包在 `hanime-cdn.com/js/vendor.<hash>.min.js` 里的一个
 * Emscripten 模块算出（模块内部通过 ASM_CONSTS 回调 `window.ssignature = ...`）。
 *
 * 做法（移植自 DonMecca/hanime-stremio 的 lib/clients/hanime_web_signer.js，
 * MIT License, Copyright (c) 2025 Anime Source）：
 *
 *   1. 在 Node 里伪造最小的浏览器全局对象（window / document / navigator / CustomEvent）
 *   2. `process.type = 'renderer'` 把 Emscripten 胶水层逼到「内嵌 base64 WASM」分支，
 *      否则它会走 Node 的 fs 分支去找一个并不存在的 .wasm 文件
 *   3. require 那份 vendor 文件，等它把 window.stime 填上
 *   4. 之后每 dispatch 一个 "e" 事件，它就重算一次签名
 *
 * **不做的事**：不逆向签名算法、不启动无头浏览器、本进程零网络访问。
 *
 * 关于 vendor 文件的来源
 * ----------------------
 * 那份 200KB 文件是 hanime.tv 的**专有代码**（不是本项目、也不是参考项目的 MIT 代码）。
 * 为了不随本项目分发它，这里**不 vendor、不打包**，改由 Python 侧通过代理从 hanime
 * 官方 CDN 下载到本地缓存，再把路径作为命令行参数传进来。
 * 这样本项目分发的只有自己写的代码，同时站点更新签名时也能自动跟上。
 *
 * 协议（stdin/stdout 行式 JSON）
 * ------------------------------
 *     -> {"cmd":"ping"}    <- {"ok":true,"ready":true}
 *     -> {"cmd":"sign"}    <- {"ok":true,"signature":"...","time":"1789..."}
 *     -> {"cmd":"exit"}    （进程退出）
 *
 * 用法
 * ----
 *     node hanime_signer.js <vendor.js 本地路径>
 *
 * 退出码：0 正常；1 启动失败（stderr/stdout 上会有一行 {"ok":false,"error":...}）
 */

'use strict';

const fs = require('fs');
const path = require('path');

const BOOT_TIMEOUT_MS = Number(process.env.HANIME_BOOT_TIMEOUT_MS || 15000);
const SIGN_WAIT_MS = Number(process.env.HANIME_SIGN_WAIT_MS || 5000);

// --------------------------------------------------------------------------- //
// 最小浏览器环境
// --------------------------------------------------------------------------- //

class CustomEvent {
  constructor(type, init = {}) {
    this.type = type;
    this.detail = init && init.detail;
  }
}

function createWindowMock() {
  const listeners = {};
  const location = {
    href: 'https://hanime.tv/',
    origin: 'https://hanime.tv',
    protocol: 'https:',
    host: 'hanime.tv',
    hostname: 'hanime.tv',
    pathname: '/',
    search: '',
    hash: '',
    toString() { return this.href; },
  };

  const windowObj = {
    addEventListener(ev, fn) { (listeners[ev] = listeners[ev] || []).push(fn); },
    removeEventListener(ev, fn) {
      if (listeners[ev]) listeners[ev] = listeners[ev].filter((x) => x !== fn);
    },
    dispatchEvent(e) {
      for (const fn of (listeners[e.type] || []).slice()) {
        try { fn(e); } catch (_) { /* 单个回调异常不影响其它 */ }
      }
      return true;
    },
    setTimeout, clearTimeout, setInterval, clearInterval,
    location,
    navigator: {
      userAgent:
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
        '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
      platform: 'Win32',
    },
    crypto: require('crypto').webcrypto,
    performance: { now: () => Date.now() },
    console,
  };

  windowObj.window = windowObj;
  windowObj.self = windowObj;
  windowObj.top = windowObj;
  windowObj.parent = windowObj;
  windowObj.frames = windowObj;

  // Emscripten 用 document.currentScript.src 判断自己从哪加载，
  // 这里只要给它一个像 URL 的字符串即可。
  windowObj.document = {
    currentScript: { src: 'https://hanime-cdn.com/js/vendor.js' },
    addEventListener() {},
    removeEventListener() {},
    createElement() { return { style: {}, setAttribute() {}, appendChild() {} }; },
    getElementsByTagName() { return []; },
    querySelector() { return null; },
    location,
  };

  return { windowObj, location };
}

function defineGlobal(name, value) {
  // Node 21+ 把 navigator / CustomEvent 等挂成了只读 getter，
  // 直接 `global.x = v` 会抛 "Cannot set property ... which has only a getter"。
  try {
    Object.defineProperty(global, name, {
      value, writable: true, configurable: true, enumerable: true,
    });
  } catch (_) {
    try { global[name] = value; } catch (_) { /* 实在不行就算了 */ }
  }
}

function installBrowserGlobals(windowObj, location) {
  defineGlobal('window', windowObj);
  defineGlobal('document', windowObj.document);
  defineGlobal('self', windowObj);
  defineGlobal('location', location);
  defineGlobal('navigator', windowObj.navigator);
  defineGlobal('CustomEvent', CustomEvent);

  // 关键一步：见文件头说明
  process.type = 'renderer';
}

// --------------------------------------------------------------------------- //
// 启动
// --------------------------------------------------------------------------- //

let READY = false;
let BOOT_ERROR = null;

function boot(vendorPath) {
  if (!vendorPath) throw new Error('用法: node hanime_signer.js <vendor.js 路径>');
  if (!fs.existsSync(vendorPath)) throw new Error('vendor.js 不存在: ' + vendorPath);

  const { windowObj, location } = createWindowMock();
  installBrowserGlobals(windowObj, location);

  // Emscripten 的 main() 在注册完监听后会抛一个无害的迟到异常，吞掉
  const swallow = () => {};
  process.on('uncaughtException', swallow);
  process.on('unhandledRejection', swallow);

  try {
    require(path.resolve(vendorPath));
  } catch (_) {
    // 同步阶段异常不影响后续：WASM 是异步实例化的
  }
  return windowObj;
}

function waitFor(fn, timeoutMs) {
  return new Promise((resolve) => {
    const deadline = Date.now() + timeoutMs;
    (function loop() {
      let v = null;
      try { v = fn(); } catch (_) { v = null; }
      if (v) return resolve(v);
      if (Date.now() > deadline) return resolve(null);
      setTimeout(loop, 25);
    })();
  });
}

// --------------------------------------------------------------------------- //
// stdio 协议
// --------------------------------------------------------------------------- //

function reply(obj) {
  process.stdout.write(JSON.stringify(obj) + '\n');
}

async function main() {
  const vendorPath = process.argv[2];

  let win;
  try {
    win = boot(vendorPath);
  } catch (e) {
    BOOT_ERROR = String((e && e.message) || e);
    reply({ ok: false, error: 'boot: ' + BOOT_ERROR });
    process.exit(1);
  }

  const got = await waitFor(() => (win.stime ? win.stime : null), BOOT_TIMEOUT_MS);
  if (!got) {
    BOOT_ERROR = `WASM 已加载但 ${BOOT_TIMEOUT_MS}ms 内未产出签名`;
    reply({ ok: false, error: BOOT_ERROR });
    process.exit(1);
  }

  READY = true;
  reply({ ok: true, event: 'ready', time: String(win.stime) });

  let buf = '';
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', async (chunk) => {
    buf += chunk;
    let idx;
    while ((idx = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, idx).trim();
      buf = buf.slice(idx + 1);
      if (!line) continue;

      let cmd = {};
      try { cmd = JSON.parse(line); } catch (_) {
        reply({ ok: false, error: 'bad json' });
        continue;
      }

      if (cmd.cmd === 'exit') process.exit(0);

      if (cmd.cmd === 'ping') {
        reply({ ok: true, ready: READY });
        continue;
      }

      if (cmd.cmd === 'sign') {
        if (!READY) { reply({ ok: false, error: BOOT_ERROR || 'not ready' }); continue; }
        try {
          // 触发 "e" 事件 → WASM 重算签名
          win.dispatchEvent(new CustomEvent('e'));
          const sig = await waitFor(() => win.ssignature, SIGN_WAIT_MS);
          const t = win.stime;
          if (!sig || !t) reply({ ok: false, error: 'signature empty' });
          else reply({ ok: true, signature: String(sig), time: String(t) });
        } catch (e) {
          reply({ ok: false, error: String((e && e.message) || e) });
        }
        continue;
      }

      reply({ ok: false, error: 'unknown cmd: ' + cmd.cmd });
    }
  });

  process.stdin.on('end', () => process.exit(0));
}

main().catch((e) => {
  reply({ ok: false, error: 'fatal: ' + String((e && e.message) || e) });
  process.exit(1);
});
