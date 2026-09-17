/*
 * 播放历史记录功能的无头自检。
 *
 * 为什么要有这个：历史记录的逻辑（去重覆盖、迁移、选择删除）都在前端，
 * 而它又直接动 localStorage —— 光靠肉眼看代码很容易漏掉"重看变成两条记录"
 * 或者"删除没写回本地"这类错误。这里用一个极简 DOM 桩把 index.html 里的
 * <script> 真跑起来，然后按真实操作顺序调它的函数。
 *
 *   node tools/history_check.js
 *
 * 无第三方依赖。退出码非 0 表示失败。
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
const js = scripts.sort((a, b) => b.length - a.length)[0];

/* ------------------------------------------------------------------ *
 * 极简 DOM 桩：只实现 index.html 真正用到的那部分
 * ------------------------------------------------------------------ */

class El {
  constructor(tag) {
    this.tagName = String(tag || "div").toUpperCase();
    this._cls = new Set();
    this.children = [];
    this.style = {};
    this.dataset = {};
    this.attrs = {};
    this.textContent = "";
    this._html = "";
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this.title = "";
    this.onclick = null;
    this.onerror = null;
  }
  get className() { return [...this._cls].join(" "); }
  set className(v) { this._cls = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get classList() {
    const s = this._cls;
    return {
      add: (...c) => c.forEach((x) => s.add(x)),
      remove: (...c) => c.forEach((x) => s.delete(x)),
      contains: (c) => s.has(c),
      toggle: (c, on) => {
        const want = on === undefined ? !s.has(c) : !!on;
        want ? s.add(c) : s.delete(c);
        return want;
      },
    };
  }
  get innerHTML() { return this._html; }
  set innerHTML(v) { this._html = String(v); if (this._html === "") this.children = []; }
  appendChild(c) { this.children.push(c); return c; }
  append(...cs) { cs.forEach((c) => this.children.push(c)); }
  remove() {}
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k] ?? null; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener() {}
  querySelector() { return new El("h2"); }
  load() {}
  play() {}
  pause() {}
  currentTime() {}
}

const registry = new Map();
const document = {
  getElementById(id) {
    if (!registry.has(id)) registry.set(id, new El("div"));
    return registry.get(id);
  },
  createElement: (t) => new El(t),
  addEventListener() {},
  querySelector: () => new El("div"),
};

const store = new Map();
const localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
  clear: () => store.clear(),
};

/* ---- fetch 桩：返回和真后端同形状的假数据 ---- */
const SITES = [
  { key: "hanime", name: "hanime.tv", domain: "hanime.tv", resolver: "hanime",
    available: true, supports_search: true, search_kind: "catalog", has_tags: true },
  { key: "PornHub", name: "PornHub", domain: "pornhub.com", resolver: "ytdlp",
    available: true, supports_search: true, search_kind: "paged", has_tags: false },
  { key: "RedTube", name: "RedTube", domain: "redtube.com", resolver: "ytdlp",
    available: true, supports_search: true, search_kind: "paged", has_tags: false },
];

let fetchLog = [];
function fetchStub(url) {
  fetchLog.push(String(url));
  const body = (() => {
    if (url.startsWith("/api/sites")) return { count: 3, sites: SITES };
    if (url.startsWith("/api/hanime/tags")) return { count: 0, tags: [] };
    if (url.startsWith("/api/random")) {
      return { site: "RedTube", site_name: "RedTube", query: "", terms: [], term_count: 0,
               tags: [], random: true, page: 1, total_pages: null, total_count: "",
               count: 1, results: [{ id: "900", title: "随机来的", url: "https://x/900",
                                     thumbnail: "https://t/900.jpg", duration: "10:00" }] };
    }
    if (url.startsWith("/api/resolve")) {
      return { id: "900", title: "解析后的标题", duration: 600, uploader: "u", view_count: 1,
               thumbnail: "https://t/900.jpg", tags: [], formats: [{ label: "720p", height: 720, url: "https://m/900.mp4" }] };
    }
    if (url.startsWith("/api/thumb")) return { url: "https://t/og.jpg" };
    return {};
  })();
  return Promise.resolve({ ok: true, status: 200, statusText: "OK", json: async () => body });
}

/* ------------------------------------------------------------------ *
 * 把页面脚本跑起来
 * ------------------------------------------------------------------ */

const sandbox = { document, localStorage, fetch: fetchStub, console,
                  setTimeout, clearTimeout, URLSearchParams, JSON, Math, Date, Set, Map, Promise, String, Number, Array, Object, RegExp, Error };
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(js, sandbox, { filename: "index.html<script>" });

const run = (code) => vm.runInContext(code, sandbox);
const $ = (id) => registry.get(id);

// init() 是 async 的，它 await 了 /api/sites，所以下面这些同步断言跑的时候它还没完成。
// 这里先把 init() 该建立的映射同步建好（内容与 fetch 桩一致），
// 让断言跑在"页面已初始化完"的状态上；init() 本身是否真的建好了，最后再单独验一次。
run(`siteList = ${JSON.stringify(SITES)};
     searchCapable = siteList.filter((s) => s.supports_search).map((s) => s.name);
     siteList.forEach((s) => siteByKey.set(s.key, s));
     state.site = "RedTube";`);

let pass = 0, fail = 0;
function ck(name, cond, extra) {
  (cond ? pass++ : fail++);
  console.log(`  [${cond ? "PASS" : "FAIL"}] ${name}${extra !== undefined ? "  " + extra : ""}`);
}
const hist = () => JSON.parse(store.get("history") || "[]");

/* ------------------------------------------------------------------ */

console.log("=== 1. pushHistory：写入 / 覆盖 / 排序 ===");
run(`pushHistory({url:"u1", title:"A", site:"RedTube", id:"1"}, {thumbnail:"t1.jpg", duration:100})`);
ck("第一次写入 = 1 条", hist().length === 1, `len=${hist().length}`);
ck("字段齐全", (() => { const r = hist()[0]; return r.url && r.title && r.site && r.id && r.thumbnail && r.duration && r.ts; })());
ck("封面/时长来自 resolve", hist()[0].thumbnail === "t1.jpg" && hist()[0].duration === 100);

run(`pushHistory({url:"u2", title:"B", site:"PornHub", id:"2"}, {thumbnail:"t2.jpg", duration:200})`);
run(`pushHistory({url:"u3", title:"C", site:"hanime", id:"3"}, {thumbnail:"t3.jpg"})`);
ck("三条不同视频 = 3 条记录", hist().length === 3, `len=${hist().length}`);
ck("最新的在最前", hist().map((r) => r.url).join(",") === "u3,u2,u1", hist().map((r) => r.url).join(","));

// 关键需求：再看一遍 = 用最新记录覆盖旧的
const tsBefore = hist().find((r) => r.url === "u1").ts;
run(`pushHistory({url:"u1", title:"A 改过的标题", site:"RedTube", id:"1"}, {thumbnail:"t1new.jpg", duration:111})`);
ck("重看同一个视频**不会**多出一条", hist().length === 3, `len=${hist().length}`);
ck("重看后被提到最前", hist()[0].url === "u1", hist().map((r) => r.url).join(","));
ck("重看时用最新数据覆盖旧的",
   hist()[0].title === "A 改过的标题" && hist()[0].thumbnail === "t1new.jpg" && hist()[0].duration === 111);
ck("时间戳更新了", hist()[0].ts >= tsBefore);

console.log("\n=== 2. 不自动删除 + 配额上限 ===");
store.delete("history");
for (let i = 0; i < 2005; i++) {
  run(`pushHistory({url:"c${i}", title:"t${i}", site:"RedTube", id:"${i}"}, {})`);
}
ck("只在配额安全阀（2000）处截断，其余不自动删", hist().length === 2000, `len=${hist().length}`);
ck("保留的是最新的（c2004 在最前）", hist()[0].url === "c2004", hist()[0].url);

console.log("\n=== 3. 旧数据迁移（recents → history） ===");
store.clear();
store.set("recents", JSON.stringify([
  { title: "旧记录1", url: "old1", site: "RedTube", id: "11" },
  { title: "旧记录2", url: "old2", site: "PornHub", id: "22" },
]));
run(`migrateRecents()`);
ck("旧记录并入 history", hist().length === 2 && hist()[0].url === "old1", `len=${hist().length}`);
ck("旧 key 被清掉", store.get("recents") === undefined);
ck("缺的封面字段补成空串（前端会回退到 /api/thumb）", hist()[0].thumbnail === "");
// 迁移不应覆盖已有的新数据
store.set("recents", JSON.stringify([{ title: "x", url: "old3" }]));
run(`migrateRecents()`);
ck("已有 history 时不被旧数据覆盖", hist().every((r) => r.url !== "old3"), hist().map((r) => r.url).join(","));

console.log("\n=== 4. 历史记录视图 ===");
store.clear();
run(`pushHistory({url:"h1", title:"番剧A", site:"hanime", id:"s1"}, {thumbnail:"a.jpg"})`);
run(`pushHistory({url:"h2", title:"管子B", site:"RedTube", id:"2"}, {thumbnail:"b.jpg"})`);
run(`pushHistory({url:"h3", title:"管子C", site:"PornHub", id:"3"}, {thumbnail:"c.jpg"})`);
// 当前选中站点设成 RedTube（非竖版），用来验证逐卡片的竖版判断
run(`state.site = "RedTube"`);
run(`openHistory()`);
ck("进入了历史模式", run(`historyMode`) === true);
ck("工具栏出现", $("htool").hidden === false);
ck("三张卡片都渲染了", $("grid").children.length === 3, `cards=${$("grid").children.length}`);
ck("分页条隐藏", $("pager").style.display === "none");
ck("非选择模式下卡片没有 pick 类", $("grid").children.every((c) => !c.classList.contains("pick")));
ck("番剧那条单独用竖版封面（其它两条是横版）",
   $("grid").children[2].classList.contains("portrait") &&
   !$("grid").children[1].classList.contains("portrait") &&
   !$("grid").children[0].classList.contains("portrait"),
   "第3张(hanime)=portrait");
ck("按钮高亮为已进入", $("hisbtn").className.includes("on"));
ck("列表标签显示条数", $("hlbl").innerHTML.includes("3"), $("hlbl").innerHTML.replace(/<[^>]+>/g, "").slice(0, 40));

console.log("\n=== 5. 删除流程：删除 → 选择 → 确定删除 ===");
ck("初始按钮文案 = 删除", $("hdel").textContent === "删除", $("hdel").textContent);
ck("初始取消按钮隐藏", $("hcancel").hidden === true);
run(`onHistoryDel()`);                       // 第一下 = 进入选择模式
ck("变成选择模式", run(`historySelMode`) === true);
ck("按钮文案变为 确定删除", $("hdel").textContent === "确定删除", $("hdel").textContent);
ck("没选任何项时 确定删除 是灰的", $("hdel").disabled === true);
ck("取消按钮出现", $("hcancel").hidden === false);
ck("卡片变成可选中（pick 类）", $("grid").children.every((c) => c.classList.contains("pick")));

// 模拟点击卡片勾选第 1、3 张
const cards = $("grid").children;
run(`__t = ${0}`); // noop
vm.runInContext("toggleHistorySel(__it, __card)", Object.assign(sandbox, {
  __it: { url: "h3" }, __card: cards[0],
}));
vm.runInContext("toggleHistorySel(__it, __card)", Object.assign(sandbox, {
  __it: { url: "h1" }, __card: cards[2],
}));
ck("选中 2 项", run(`historySel.size`) === 2, `size=${run("historySel.size")}`);
ck("选中的卡片有 sel 类", cards[0].classList.contains("sel") && cards[2].classList.contains("sel"));
ck("未选中的没有 sel 类", !cards[1].classList.contains("sel"));
ck("有选中后 确定删除 可点", $("hdel").disabled === false);
ck("标签显示已选数量", $("hlbl").innerHTML.includes("2"), $("hlbl").innerHTML.replace(/<[^>]+>/g, ""));

// 取消选中一项
vm.runInContext("toggleHistorySel(__it, __card)", Object.assign(sandbox, {
  __it: { url: "h3" }, __card: cards[0],
}));
ck("再点一次 = 取消选中", run(`historySel.size`) === 1 && !cards[0].classList.contains("sel"));
// 再选回来，然后确认删除
vm.runInContext("toggleHistorySel(__it, __card)", Object.assign(sandbox, {
  __it: { url: "h3" }, __card: cards[0],
}));
run(`onHistoryDel()`);                       // 第二下 = 真删
ck("选中的两条从 localStorage 删除", hist().map((r) => r.url).join(",") === "h2", hist().map((r) => r.url).join(","));
ck("剩下的还能看到", $("grid").children.length === 1, `cards=${$("grid").children.length}`);
ck("删除后退出选择模式", run(`historySelMode`) === false);
ck("按钮文案变回 删除", $("hdel").textContent === "删除", $("hdel").textContent);
ck("取消按钮重新隐藏", $("hcancel").hidden === true);

console.log("\n=== 6. 取消 / 退出 ===");
run(`onHistoryDel()`);
ck("再次进入选择模式", run(`historySelMode`) === true);
run(`exitHistorySel(); renderHistory();`);    // 点「取消」
ck("取消后回到普通模式", run(`historySelMode`) === false && $("hdel").textContent === "删除");
ck("取消不会删掉任何记录", hist().length === 1, `len=${hist().length}`);

run(`exitHistory()`);
ck("exitHistory 关掉工具栏", $("htool").hidden === true);
ck("exitHistory 去掉按钮高亮", !$("hisbtn").className.includes("on"));

console.log("\n=== 7. 空历史 ===");
store.clear();
run(`openHistory()`);
ck("没有记录时不报错、显示提示", $("grid").children.length === 0 && $("status").innerHTML.includes("还没有播放记录"));
ck("按钮禁用", $("hdel").disabled === true);

console.log("\n=== 8. 顶栏按钮与文案 ===");
ck("logo 已改名", html.includes("No<span>AD</span>PornSite") && !html.includes(">AD<span>Skiper"));
ck("<title> 同步改名", html.includes("<title>NoADPornSite</title>"));
ck("删掉了 yt-dlp 那句话", !html.includes("yt-dlp 支持的都行"));
ck("旧的 #recents 容器已移除", !html.includes('id="recents"'));
const iGo = html.indexOf('id="go"'), iSeg = html.indexOf('id="siteseg"'), iHis = html.indexOf('id="hisbtn"');
ck("搜索在站点按钮左边、历史记录在最右", iGo < iSeg && iSeg < iHis);

console.log("\n=== 9. init() 真的把站点装进去了吗（等微任务跑完再验） ===");
setTimeout(() => {
  ck("init() 建立 siteByKey 3 个站点", run("siteByKey.size") === 3, `size=${run("siteByKey.size")}`);
  ck("站点按钮渲染了 3 个", $("siteseg").children.length === 3, `n=${$("siteseg").children.length}`);
  ck("init() 后进入了随机推荐（不是历史视图）", run("historyMode") === false && run("state.random") === true);

  console.log(`\n=========== ${pass} PASS / ${fail} FAIL ===========`);
  process.exit(fail ? 1 : 0);
}, 60);
