# NoADPornSite

本地视频直链播放器。**重在线播放、轻下载管理、无媒体库管理。**

RedTube 的预播广告是「4 秒后可跳过」——等 4 秒再自动点按钮没什么意义。
真正有价值的做法是**根本不进入广告播放链路**：站点把正片的 CDN 直链明文放在页面里，
把这个直链交给自己的播放器，广告就从来不曾被请求过。这个项目做的就是这件事。

```
浏览器 (127.0.0.1:8000)          只用连本机 —— 不需要代理、不需要翻墙
      │
      ├── /api/random   关键词为空时的随机推荐（不知道看什么就刷一批）
      ├── /api/search   站点搜索（自己抓，yt-dlp 不支持搜索）
      ├── /api/resolve  yt-dlp 解析出 1080p/720p/480p/240p 直链
      └── /api/media    流式中转（透传 Range，可拖进度条）
      │
      ▼
本地 FastAPI ──[走 PROXY]──▶ 站点 / 广告无关的 CDN
```

---

## 前置环境

只有两样东西要自己装，装完能在 PATH 里找到就行：

| 需要 | 版本 | 干什么用的 | 没有会怎样 |
|---|---|---|---|
| **Python** | **3.10 或更新** | 跑后端 | 完全起不来 |
| **Node.js** | **18 或更新** | hanime.tv 的 WASM 签名助手 | **另外两个站照常可用**，只有 hanime 取流失败 |

**Python 3.10 是硬下限**：`fastapi` / `uvicorn` / `starlette` / `anyio` / `click` / `yt-dlp`
声明的 `Requires-Python` 全是 `>=3.10`，低于这个版本 pip 根本装不上。
`start.bat` 会替你把版本卡住；手动装的话自己注意。

**Node 是可选的**：只有 hanime.tv 需要它（它的取流要跑站点自己的 WASM 来算签名）。
不装也能正常用，RedTube / PornHub 完全不受影响。

自己确认一下：

```bash
python --version     # 需要 >= 3.10
node --version       # 需要 >= 18（可选）
```

下载：[Python](https://www.python.org/downloads/)（Windows 安装时**务必勾选**
`Add python.exe to PATH`，否则 `start.bat` 找不到它）、[Node.js](https://nodejs.org/)

### 还需要一个能连出去的代理

站点和它们的 CDN 都在墙外，后端默认出口是 `http://127.0.0.1:7890`（Clash 混合端口）。
**先把代理跑起来再启动**，否则两个 tube 站的搜索会直接超时。

浏览器本身**不需要任何代理** —— 所有上游媒体都由后端中转，浏览器只连 `127.0.0.1`。

用别的方式改出口，见下面的「配置」。

---

## 安装 / 启动

两条路，选一条就行 —— 跑起来的是同一个东西。

### 方式一：双击 `start.bat`（Windows，最快）

1. 装好 Python（3.10+，且进了 PATH）
2. **双击 `start.bat`**
3. 浏览器打开 <http://127.0.0.1:8000>

**依赖是自动装的，不用手动 `pip install`。** `start.bat` 依次做四件事：

```
检查 python 在不在 PATH        不在   → 报错退出，并给出下载地址
检查 Python 版本 >= 3.10       不够   → 报错退出（否则 pip 只会丢一堆看不懂的解析错误）
用 import 试探依赖装没装        缺了   → 自动 pip install -r requirements.txt
找不到 node 时给一句提示        不拦   → RedTube / PornHub 照常可用，只是 hanime 用不了
最后 python app.py
```

所以**第一次双击会先把依赖装上（需要联网，几十 MB 量级），之后就秒开**。

> **`start.bat` 只自动装 Python 依赖，不会装 Node** —— Node 不是 pip 包，脚本只能
> 检测到缺失后提示你。想用 hanime.tv 就得自己去 [nodejs.org](https://nodejs.org/) 装一次。
>
> 装依赖走的是系统网络 / pip 自己的代理设置，**不走上面那个出口代理**。
> 国内网络嫌慢可以换镜像：
> ```bash
> python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
> ```

### 方式二：自己装依赖再启动（跨平台）

`start.bat` 只是把下面这两条命令包了一层 —— 效果完全一样：

```bash
python -m pip install -r requirements.txt
python app.py
```

想用虚拟环境（推荐，免得污染系统环境）：

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux
python -m pip install -r requirements.txt
python app.py
```

**macOS / Linux 上没有 `start.bat`，用这条。**

然后浏览器打开 <http://127.0.0.1:8000>。

---

## 配置

改出口代理（默认 `http://127.0.0.1:7890`）：

```powershell
$env:ADSKIPER_PROXY = "socks5://127.0.0.1:7891"   # 或留空表示直连
python app.py
```

全部环境变量：

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `ADSKIPER_PROXY` | `http://127.0.0.1:7890` | 出口代理。留空 = 直连 |
| `ADSKIPER_HOST` | `127.0.0.1` | 监听地址。**别改成 0.0.0.0**，本服务没有鉴权 |
| `ADSKIPER_PORT` | `8000` | 监听端口 |
| `ADSKIPER_MEDIA_HOSTS` | 空 | 追加媒体 CDN 域名后缀，逗号分隔 |
| `ADSKIPER_RESOLVE_TTL` | `3000` | 直链解析缓存秒数（签名 URL 约 2 小时过期） |

---

## 为什么这么设计

### 1. yt-dlp 当库用，不 fork

```python
import yt_dlp
ydl = yt_dlp.YoutubeDL({"proxy": PROXY, "skip_download": True})
info = ydl.extract_info(url, download=False)
```

yt-dlp 一年发布 600+ 个版本，几乎每天在修各站解析器。fork 等于自己维护 60 万行代码的分叉。
需要扩展就写 **plugin**：

```
%APPDATA%\yt-dlp\plugins\<包名>\yt_dlp_plugins\extractor\myplugin.py
```

```python
from yt_dlp.extractor.redtube import RedTubeIE

class MyRedTubeIE(RedTubeIE, plugin_name='myplugin'):
    """plugin_name 会让它替换掉内置的 RedTubeIE，无需改源码"""
    def _real_extract(self, url):
        return super()._real_extract(url)
```

extractor 插件自动生效（URL 匹配就调用），且优先级高于内置解析器。
⚠️ 所有插件都会被无条件导入执行，**不要装来路不明的插件**。

### 2. 为什么媒体要走后端中转，而不是让浏览器直接连 CDN

| 原因 | 说明 |
|---|---|
| **绕开 GFW** | CDN 域名（`*.rdtcdn.com`）在国内同样被墙。中转后浏览器只连 `127.0.0.1` |
| **绕开 CORS** | CDN 完全不返回 `Access-Control-Allow-Origin`。中转后同源，顺带把 `hls.js` 的路也堵上了 |
| **统一带 Referer** | `<video src>` 无法自定义请求头，所以用 `&r=<视频页URL>` query 参数传给后端转发 |
| **SSRF 防护** | 白名单只放行已知媒体 CDN，避免本地服务被恶意网页当跳板 |

### 3. 为什么只用渐进式 MP4，不用 HLS

实测 RedTube 的 CDN 不返回任何 CORS 头。`<video src="mp4">` 不受 CORS 限制，能直接播；
但 `hls.js` 要用 XHR 取 m3u8，会被 CORS 挡死。所以 `_normalize_info()` 只保留渐进式 MP4。

### 4. 广告为什么消失

RedTube 有两条互不相干的链路：

```
广告链路：  /_xa/ads?zone_id=...  ──▶ 播放器 AdRoll 模块 ──▶ 4 秒后可跳过
正片链路：  /media/mp4?s=<签名>    ──▶ CDN 直链（1080p/720p/...）
```

本项目只走第二条。广告请求从未发出，`ads_test.js`、`ab_detection`、TrafficJunky 蜜罐
**全都不执行**，因为根本不加载站点的页面和 JS。

---

## 已知坑（都踩过了）

| 现象 | 原因 | 处理 |
|---|---|---|
| `/api/sites` 返回 **502 Bad Gateway** | httpx 默认 `trust_env=True`，会通过 `getproxies()` 读到 **Windows 注册表里的系统代理**，连 `127.0.0.1` 的请求都被丢给 Clash | 已显式设 `trust_env=False`。自己写测试脚本时也要注意 |
| 所有视频都报「没有可用的 MP4 直链」 | RedTube 的渐进式 MP4 条目 yt-dlp 给的 `vcodec` 是 `None`（未知），若按 `vcodec == 'none'` 判纯音频会误伤 | 只把字符串 `'none'` 当纯音频 |
| 播放到一半报错 | 直链签名约 2 小时过期 | 点「重载直链」重新解析，缓存 TTL 默认 3000 秒 |
| 搜索页拿到年龄门 | 缺年龄确认 cookie | 适配器里带 `accessAgeDisclaimerRT=1; accessRT=1` |
| 某一档清晰度**拖动进度条失效** | 实测 `240P` 那档的 CDN 边缘节点**直接忽略 Range**，返回 `200` + 整个文件（1080p/720p/480p 都是正常的 `206`） | CDN 侧行为，无解。换一档即可；`200` 不影响从头播放 |
| 控制台刷 `socket.send() raised exception.` | 客户端中途断开（切清晰度/关页面）时 uvicorn 的日志 | 已用 `BackgroundTask` 关闭上游连接，正常情况下不再出现 |
| **很多封面是全黑的** | 封面 CDN 有两道关卡：① `pix-cdn77.rdtcdn.com` **强制校验 Referer**，不带就 `403`（返回 868 字节 HTML 错误页）——**视频 CDN 不校验，只有图片 CDN 校验**；② CDN77 后端偶发 `502`，此时**同一视频的其他尺寸仍是好的** | ① 后端按域名后缀兜底附上 `Referer`（`DEFAULT_REFERER_BY_SUFFIX`）；② 解析器把一个卡片里的**所有封面尺寸**都收集成候选，前端逐个回退；③ 全挂了再调 `/api/thumb` 取视频页 `og:image`；④ 彻底失败才显示「封面不可用」占位 |
| 最后一张卡的封面候选暴涨到 30 个 | 卡片是按 `<li … data-video-id>` 切分的，**最后一块会一直延伸到页面末尾**（后面没有下一个卡片可供切分），把整页尾部的 `<img>` 全收了进来 | 每块按 `</li>` 截断。正常应为 2 个候选 |
| `/api/resolve?id=<PornHub 的 id>` 报 **400「id 必须为纯数字」** | 早期实现假设"id 就是数字"，但 **PornHub 的 id 是 viewkey（十六进制串，如 `68069b6b253eb`）**，一刀切全被拒了。`/api/thumb` 因为没有这个假设所以一直是好的 | 改成只校验 URL 安全字符，页面 URL 交给适配器的 `video_page_url()` 拼（PornHub 拼成 `view_video.php?viewkey=`）。**前端一直用 `?url=` 所以没暴露**，但 API 层是可复现的 |

---

## 封面为什么需要三级回退

RedTube 列表页的封面走 `pix-cdn77.rdtcdn.com`（CDN77），实测有这些脾气：

1. **必须带 Referer**。不带 → `403`。带上 `https://www.redtube.com/` → `200`。
2. **`hash=` 是绑定尺寸签名的**，不能自己把 `rs:fit:304:171` 改成 `rs:fit:640:360` 重算 —— 改了必然 `403`。
3. **CDN77 后端会偶发 `502`**，而且同一个视频**只有部分尺寸**受影响：实测 `304:171` 挂了而 `224:126` 是好的。
4. 列表页里每个视频现成可用的尺寸就 2~4 个（`data-src` / `data-o_thumb` / webp / jpg）。

所以顺序是：**首选尺寸 → 其余卡片内候选 → 视频页 `og:image`（`rs:fit:1280:720`，走另一条路径）→ 占位**。
`/api/thumb` 只在前面全失败时才被调用，不影响正常加载；结果缓存 6 小时。

---

## 支持的站点

三个站点，分属**两种截然不同的形态**：

| 站点 | 形态 | 搜索 | 取流 |
|---|---|---|---|
| **RedTube** | 服务端渲染的列表页 | 抓 HTML，36 条/页 | yt-dlp |
| **PornHub** | 服务端渲染的列表页 | 抓 HTML，38 条/页 | yt-dlp |
| **hanime.tv** | Astro 前端 + JSON API | **全量索引 + 本地过滤** | **WASM 签名握手 → HLS** |

前两个同属 Aylo（原 MindGeek），页面结构、播放器、CDN 都很相近。
hanime 完全不同，逻辑单独放在 `hanime.py` 里，**它挂了不影响另外两个**。

站点切换是搜索框右边的**并排按钮**，一次点击切换，选择记在 localStorage。

### ⚠️ 站点清单只管"按钮上能选什么"

`SUPPORTED_SITES` 只决定界面上的站点按钮。前两个站走 yt-dlp，所以**粘任何 yt-dlp
支持的链接都能播** —— 哪怕不在清单里。

### 为什么只有这三个站能"搜关键词"

yt-dlp 的 `RedTubeIE._VALID_URL` 只匹配视频 ID 形式的 URL，**不提供搜索**；
`_SEARCH_KEY` 机制也只有 YouTube / Bilibili / SoundCloud 等少数站点实现了。
hanime 更是**完全没有 yt-dlp extractor**。
所以"能搜关键词"是本项目自己写的（`RedTubeSite` / `PornHubSite` / `hanime.py`），
不是 yt-dlp 给的能力。其余站点只能粘链接。

### 想扩展时的两个入口

1. **只加播放**：不用改代码，粘链接即可。若该站 CDN 不在
   `MEDIA_SUFFIXES`（会报 403「域名不在白名单内」），加进 `ADSKIPER_MEDIA_HOSTS`
   环境变量；若 CDN 要 Referer（403/404），加进 `DEFAULT_REFERER_BY_SUFFIX`。
2. **加搜索**：写一个 `SiteAdapter` 子类（见下节），并往 `SUPPORTED_SITES` 加一行。

---

## hanime.tv（形态不同，逻辑单独隔离）

hanime 是**番剧站**，和 tube 站完全不是一回事：yt-dlp 不支持、取流要过一道
WASM 签名的握手、搜索接口一次返回全量索引。相关代码在 `hanime.py` +
`hanime_signer.js`，**整体可降级**：目录接口挂了只会让这个站点不可用。

### 三条链路

```
目录（不需要认证、不需要签名）
    GET guest.freeanimehentai.net/api/v11/search_hvs
    → 一次性返回全量索引（约 3400 条 / 4MB），带结构化 tags
    → 搜索、标签过滤、排序、分页全部在本地做（缓存 30 分钟）

取流（需要签名）
    POST auth.hanime.tv/api/v11/handshake
    ← 头：x-signature-version: web2 / x-signature / x-time
    → 响应头 x-token（AES-256-GCM 封装的 JSON）→ sources[] → HLS 地址

播放
    m3u8 经 /api/media 中转时**改写每条 URI**，让分片和 AES 密钥也走代理
    → 前端用 hls.js 播放
```

### 签名怎么来的（关键设计）

hanime 把签名算法打在一个 Emscripten 模块里（`hanime-cdn.com/js/vendor.<hash>.min.js`）。
本项目的做法**移植自 [DonMecca/hanime-stremio](https://github.com/DonMecca/hanime-stremio)**
（MIT, Copyright (c) 2025 Anime Source）：不逆向算法、不启动无头浏览器，而是

1. 在 Node 里伪造最小的浏览器全局对象（`window` / `document` / `navigator` / `CustomEvent`）
2. **`process.type = 'renderer'`** —— 把 Emscripten 胶水层逼到「内嵌 base64 WASM」
   分支，否则它会去 fs 找一个并不存在的 `.wasm` 文件
3. `require` 那份 vendor，等它把 `window.stime` 填上
4. 之后每 `dispatchEvent(new CustomEvent('e'))` 一次，它就重算一次签名

**但那个 200KB 的 vendor 文件是 hanime.tv 的专有代码**（不属于本项目，也不属于任何
MIT 参考项目），所以本项目**不打包、不分发**它 —— 改由 Python 通过代理从官方 CDN
运行时抓取并缓存到 `.cache/hanime/`。顺带的好处是站点更新签名后会自动跟上。

> 想要"完全照搬参考项目布局"（把 vendor 文件提交进仓库）也完全可行，技术上等价。
> 这里选择运行时抓取，是为了让本项目分发的只有自己写的代码。

### 账号：不做

游客（不登录）能拿到 **≤720p**（实测 720p/480p/360p）。1080p 那一档返回的是
`kind: "promotion"` 且 `src` 为空的**推广占位**，需要登录才能解锁。
本项目**不做任何账号功能**，所以1080p 不可用。

### 广告：天然免疫

握手响应里带 `is_preroll_enabled` / `preroll_urls`（实测指向 TrafficJunky 等），
页面里还有 `adv1.clickadu.net` 的 iframe 广告。本项目**只取 `sources`，完全忽略这些
字段**，也不加载站点页面 —— 所以三类广告一个都不会出现。

### 实测要点

| 项 | 结论 |
|---|---|
| 目录接口 | 4.21 MB / 3404 条 / 1.2s，**忽略所有查询参数**（永远返回全量） |
| 标签 | **61 个**，每条带真实 `tags` 数组 → 可做**精确多标签 AND**（tube 站做不到） |
| 术语差异 | 没有 `lesbian`（对应 **`yuri`**）、没有 `outdoor`（最接近 **`public sex`**） |
| m3u8 | 相对路径 `/hls/...`，VOD，AES-128 |
| AES 密钥 | `ct.htv-services.com/sign.bin`，16 字节，内容是**固定常量** `"0123456701234567"` |
| 分片 | `.html` 伪装成网页，实为 TS 数据；**不需要 Referer** |
| **分片 CDN** | **轮换域名池**！抽 41 个播放列表出现 **48 个不同主机**，全部形如 `p32.htv-dragonlands.com` / `etheirys.htv-hydaelyn-13.com`。见下节 |
| 被墙情况 | `hanime.tv` 与 `auth.hanime.tv` 在国内被墙（需代理）；`guest.freeanimehentai.net` **没被墙** |

### 那个轮换 CDN 域名池（踩过的坑）

| 现象 | 原因 | 处理 |
|---|---|---|
| 部分视频分片 403「域名不在白名单内」 | 一开始只把抽样里出现的 `htv-tsukuyomi.com` 加进白名单，后来发现实际有 40+ 个主机 | 改用**正则白名单** `^(?:[a-z0-9-]+\.)*htv-[a-z0-9-]+\.com$` |
| 部分分片 502，且代理/直连都报 SSL EOF | 有的分片 CDN 挂在 **Cloudflare** 后面，代理节点在 TLS 层被拒；而**同一个主机直连也会偶发 TLS 重置**（实测同一 URL 连发 3 次：2 次 200、1 次 ConnectError，底层是 anyio 的 `BrokenResourceError`，与请求头无关） | `_open_upstream()` 按「代理 → 直连 → 代理 → 直连」重试，每轮间隔递增。直连用**一次性客户端**（复用的 AsyncClient 在 `send(stream=True)` 下会直接抛 ConnectError） |

### 前端适配

hanime 的形态不同，所以界面上做了四处自适应：

1. **标签筛选条** —— 只在选中有结构化标签的站点时出现。61 个标签渲染成可点的
   chips（默认显示前 24 个 + 「全部」展开），点击即筛选、**多选＝AND**、再点取消。
   顶部提示条也换成该站点的说明（因为它的搜索语义和 tube 站不同）。
2. **竖版封面** —— 番剧封面是 268×394，网格换成 `.grid.portrait` 的竖版比例，
   并在卡片上露出前 3 个标签。
3. **hls.js 播放** —— `formats[].is_hls=true` 时走 hls.js（浏览器原生只有 Safari
   支持 m3u8）。hls.js 从 CDN 引入（Apache-2.0），取不到会给出明确提示，不影响其它站。
4. **播放页的标签行** —— 标题下方把该视频的所有标签横向铺开（`.ptags`）。
   一条番剧实测 **2~27 个**（中位 10、平均 10.5），所以是 `flex-wrap: wrap`
   的多行横排，而不是单行横滚 —— 单行会把一半标签挤出可视区。

   **只有 hanime 显示标签**，这是刻意的：它的 tag 是站方给定的结构化元数据；
   tube 站则不适合 —— 实测 RedTube 的 `tags`/`categories` 两个字段都是空的，
   PornHub 会同时给 11 个 `categories` + 16 个自由 `tags`（还混着意大利语关键词），
   铺在标题下只会变成噪音。所以 `_normalize_info()`（yt-dlp 那条路）**故意不返回
   `tags` 字段**，前端 `renderPlayerTags()` 拿到空值就整行隐藏，
   UI 里不需要任何按站点特判的逻辑。想让 tube 站也显示，在后端加上字段即可。

### 配置

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `ADSKIPER_HANIME_CATALOG_TTL` | `1800` | 目录缓存秒数 |
| `ADSKIPER_HANIME_STREAM_TTL` | `5400` | 取流结果缓存秒数 |
| `ADSKIPER_HANIME_CACHE` | `.cache/hanime` | vendor 文件缓存目录 |
| `HANIME_BOOT_TIMEOUT_MS` | `15000` | 签名助手启动超时（传给 Node） |

### 自检脚本

`tools/hanime_probe.py` 是**独立验证脚本**，可以在不启动主程序的情况下确认整条链路：

```bash
python tools/hanime_probe.py            # 用内置 slug
python tools/hanime_probe.py <slug>     # 指定 slug
```

它会依次打印：vendor 地址 → 签名 → 握手 HTTP 状态 → 解密后的 `sources`。
站点改版时先跑这个，能快速判断是"我们坏了"还是"站点变了"。

---

## 搜索格式：空格分隔多个词 = AND

**这是本站唯一的搜索语法**，界面上有常驻提示。

```
lesbian                      → 全部含这个词
lesbian threesome            → 同时含这两个词
lesbian threesome outdoor    → 三个词都要命中
```

实测收窄效果（RedTube，用站点自己返回的 `searchCount`）：

| 搜索词 | 结果总数 |
|---|---|
| `lesbian` | 82,103 |
| `lesbian threesome` | **9,533** |
| `lesbian threesome outdoor` | **312** |

三条实测性质：

- **顺序无关**：`lesbian threesome` 与 `threesome lesbian` 返回同一个结果集。
- **草稿词会被忽略**：`lesbian zzzqqqww` ≈ `lesbian`，不会因为一个词不认得就返回 0。全是生造词才返回 0。
- **`+` 也当分隔符**：站点 URL 本身就是 `a+b+c` 形式，所以从别处复制过来的 `lesbian+threesome`
  能直接用。这也意味着**不能搜字面加号**（本站场景下无影响）。

实现要点在 `app.py` 的 `split_terms()` / `encode_query()`：

```python
def encode_query(q: str) -> str:
    # 不要对整串调用 quote() —— '+' 会被转义成 %2B（字面加号），
    # 站点就不再按多词 AND 处理了。必须逐个词编码再用 '+' 连接。
    return "+".join(quote(t, safe="") for t in split_terms(q))
```

最多取 8 个词（`MAX_TERMS`），防止拼出离谱的长查询。

### 为什么不做元数据筛选

实测过两个站的元数据能力，结论是**投入产出比太低**：

| | RedTube | PornHub |
|---|---|---|
| 分类体系 | ❌ 没有（`/tags/x` 是 404） | ✅ 98 个分类，但 **单选**——`?c=27&c=65` 只认最后一个 |
| 分类 + 关键词混用 | — | ❌ 直接 404（`?search=x&c=65` 与 `?c=27&search=x` 都是 Page Not Found） |
| 时长/清晰度/取向 | ❌ 死参数 | 部分可用（`hd=1`、`p=professional`） |
| 时间范围 | ✅ `period=` | ❌ 搜索页无效 |

**两站都无法用元数据表达"必须同时属于分类 A 和分类 B"**，而这恰好是"多词 AND"能表达的语义。
所以只保留了多词 AND。

---

## 随机推荐（关键词为空时的默认内容）

关键词框为空、且没选任何标签时，列表区显示 **10 个随机视频**，格式和搜索结果完全一样
（点开就能播）。搜索格式提示行的正下方有个 **「🎲 刷新」** 按钮换一批；
**一旦搜索框有内容或选中了标签，按钮就置灰**——有搜索条件时"随机"没有意义。

三个站的实现方式不同，因为它们的"随机"能力差很远：

| 站点 | 做法 | 候选池 |
|---|---|---|
| RedTube | 站点没有随机接口，`?page=N` 还一律 404 → 在 6 个固定列表页里随机挑 | 六个入口合计约 **225** 条 |
| PornHub | `?o=<排序档>&page=<随机页>`，排序档 × 页码都能用 | 每页 35~47 条，页码空间很大 |
| hanime | 全量目录本来就在内存里（约 3400 条）→ **真随机** | **3404** 条 |

### 关键设计：记住最近给过的，新一轮避开

"随机"最容易做错的地方是**只看池子大小**。实测过一件事：把单页候选池
（PornHub 47 条）换成每站累积的大池子（涨到 500+ 条）之后，用
「任意两次抽样的期望重叠」去衡量是变好的，但**用户的体感反而变差** ——
因为用户对比的是**上一次和这一次**。池子大了，任意两两组合的重叠总量会上升，
而"连续两次"的重叠才是眼睛能看到的东西。

所以真正的解法不是把池子做大，而是**直接排除最近展示过的条目**：

```python
_recent_shown: dict[str, deque[str]] = {}       # 每站记最近 6 轮
RECENT_WINDOW = 60                              # 10 个/轮 × 6 轮

def _pick_random(pool, count, site_key):
    recent = _recent_shown.setdefault(site_key, deque(maxlen=RECENT_WINDOW))
    fresh = [x for x in pool if x["id"] not in recent]
    if len(fresh) < count:
        fresh = pool        # 池子快被"避"空了就放宽，宁可重复也不能越刷越少
    picks = random.sample(fresh, min(count, len(fresh)))
    recent.extend(x["id"] for x in picks)
    return picks
```

实测结果（三站各连续刷新 12 次，每次 10 个）：

| 站点 | 相邻两次的重叠 | 12 轮共 120 个位置 / 不同 id |
|---|---|---|
| RedTube | **0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0** | 120 / 113 |
| PornHub | **0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0** | 120 / 118 |
| hanime | **0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0** | 120 / 120 |

（RedTube 只有 225 条候选，12 轮之后自然开始饱和；hanime 池子最大所以一个不重。）

### 顺带加的一道过滤：不要 6 秒的短片

随机抽样会把 PornHub 上 `0:06` 的剪辑翻出来，混在推荐里看着像坏了。
所以时长**能解析出来**时要求至少 1 分钟（`MIN_RANDOM_SECONDS`）：

```python
def _long_enough(item):
    secs = _dur_seconds(item.get("duration", ""))
    return secs is None or secs >= MIN_RANDOM_SECONDS   # 时长未知的一律放行
```

hanime 的目录里没有时长字段（`duration` 为空），所以不受这条影响。

### 入口 URL 是"猜"的，所以要容错

PornHub 的页码上限**是浮动的**：实测 `o=mv/cm/tr` 到第 40 页都还在，但
`o=ht` 第 20 页还行、**第 25 页就 404**。所以：

- `BROWSE_MAX_PAGE = 18` 留出安全余量（4 档 × 18 页 ≈ 3000 条候选，够用了）
- 猜错也不报错：一轮里的入口全挂了就**换一批再猜一轮**；同一轮挂掉一个入口不算致命；
  新页全挂了但累积池里还有货就凑合用（`_browse_rows`）

### 前端接线

- 首次载入、以及**关键词为空时切换站点** → 自动加载随机推荐
- 搜索框空着点「搜索」→ 也是加载随机推荐（而不是什么都不做）
- 搜索框的 `input` 事件、`doSearch`、`runSearch`、`selectSite` 都会调 `updateShuffleBtn()`
- 随机推荐的返回里 `total_pages` 为 `null`，`renderGrid()` 据此**隐藏分页条**
- 「← 返回列表」的判据从 `!state.q` 改成 `!state.q && !state.random`

**每次打开都是随机推荐**：`init()` 里**不重放**上次的搜索词 —— 早先的实现会在刷新页面时
自动重跑 `localStorage.lastQuery`，那样搜索框非空、刷新按钮是灰的，就看不到随机推荐了。
既然改成了"打开即随机"，`lastQuery` 就没有任何地方会读它，所以连写入也一并删掉了。
**搜索词现在不跨会话保留**；想接着上次搜就重新输一遍。

---

## 播放历史记录

右上角最右边是 **「历史记录」** 按钮（再点一次退出，退出后回到随机推荐）。点进去后
**复用搜索结果的卡片网格**，所以缩略图、时长角标、点击播放的行为都和搜索页一致。

历史存在 `localStorage.history`，规则只有三条：

| 规则 | 说明 |
|---|---|
| **重看只覆盖，不新增** | 同一个 URL 第二次播放时，用最新的标题/封面/时长覆盖旧记录并提到最前。实测「看 3 条 → 重看第 1 条」之后仍然是 3 条 |
| **不自动删除** | 只有用户手动删才会消失。`HISTORY_MAX = 2000` 纯粹是 localStorage 配额的安全阀（单条约 250B，2000 条约 0.5MB），正常个人使用到不了 |
| **删除要两步** | 见下 |

### 删除：两步确认 + 选择模式

```
[删除]  ──点一下──▶  进入选择模式：卡片可勾选
                     按钮变 [确定删除]，旁边出现 [取消]
                     没勾任何项时 [确定删除] 是灰的
         ──再点一下──▶  彻底删除勾选的记录（同时写回 localStorage）
```

- 一条记录都没有时 **[删除] 本身就是灰的**
- 选择模式下点卡片 = 勾选/取消（直接改 class，**不整页重绘**，几千条也不卡）
- 勾选状态用 `.card.pick.sel` 表达：绿色描边 + 右上角打勾
- **[取消]** 用于反悔 —— 破坏性操作没有退路是设计缺陷，所以多加了这一个按钮

### 两个实现细节

**逐卡片的竖版封面**：历史里会混着番剧（竖版 268×394 封面）和 tube 站（横版 16:9），
所以竖版比例**按每一条记录判断**（`card.classList.toggle("portrait", ...)`），
不能只看当前选中的站点 —— 搜索页因为只有一个站点，仍然用网格级的 `.grid.portrait`。

**记录自己的站点**：`playItem()` 用 `it.site || state.site`，卡片封面兜底请求
（`/api/thumb?id=&site=`）也带各自的站点。否则从历史里点开一个 hanime 的番剧、
而当前站点选的是 RedTube 时，封面会去 RedTube 找，直接 404。

### 旧数据迁移

早期版本用 `localStorage.recents`（只留 12 条、没有封面）。`migrateRecents()` 在
`init()` 里跑一次把它们并进 `history`，然后删掉旧 key —— 不会丢已攒下的记录。
旧记录没有封面字段，前端的封面三级回退会自动去 `/api/thumb` 补。

### 自检脚本

`tools/history_check.js` 用一个极简 DOM 桩把 `index.html` 里的 `<script>` 真跑起来，
按真实操作顺序验证上面这些规则（写入/覆盖/排序/迁移/选择/删除/取消/空状态）：

```bash
node tools/history_check.js      # 56 项断言，无需启动服务、无第三方依赖
```

历史逻辑全在前端且直接动 localStorage，"重看变成两条记录"或"删除没写回本地"
这类错误光看代码很容易漏，所以值得有个可重复跑的检查。

---

## 加一个新站点的搜索适配器

继承 `SiteAdapter`，注册进 `SITES`，再往 `SUPPORTED_SITES` 加一行（站点按钮由它驱动）。
**`key` 必须和 yt-dlp 的 `IE_NAME` 完全一致**（注意 RedTube / PornHub 是大写驼峰）。

```python
class SomeSite(SiteAdapter):
    key, name, base = "SomeSite", "SomeSite", "https://www.somesite.com"
    cookie = ""

    @classmethod
    def search_url(cls, q, page):
        return f"{cls.base}/video/search?search={quote(q)}&page={page}"

    @classmethod
    def video_page_url(cls, vid):
        # /api/thumb 取 og:image 兜底封面时要用
        return f"{cls.base}/view/{vid}"

    @classmethod
    def parse_search(cls, body, page=1):
        ...
        return items, total_pages, total_count   # 必须是三元组

SITES[SomeSite.key] = SomeSite
```

两个实测得来的注意点：

1. **最后一块必须按 `</li>` 截断**。卡片是按 `<li … data-video-id>` 切分的，最后一块会
   一直延伸到页面末尾，不截断就会把整页尾部的 `<img>` 都当成该视频的封面候选
   （实测能凑出 30 个垃圾候选）。
2. **结果里的 `id` 要是"能拼出视频页 URL"的那个值**。RedTube 用数字 id，PornHub 用
   `viewkey`（十六进制串，`view_video.php?viewkey=…`），所以两边的 `video_page_url()`
   实现不同。

顺便把该站 CDN 域名加进 `MEDIA_SUFFIXES`（或环境变量 `ADSKIPER_MEDIA_HOSTS`），
需要校验 Referer 的再加进 `DEFAULT_REFERER_BY_SUFFIX`。

### PornHub 的实测要点

| 项 | 结论 |
|---|---|
| 搜索页 | `https://www.pornhub.com/video/search?search=<q>&page=<n>`，38 条/页，**不需要 cookie**（但仍带上年龄 cookie 更稳） |
| 总页数 | **页面上不提供**，只能在"下一页按钮 disabled"时推断出当前页就是末页，所以 `total_pages` 通常是 `None` |
| 视频页 | `view_video.php?viewkey=<vkey>` |
| CDN | `*.phncdn.com`，**必须带 Referer**（不带直接 404，不是 403），已由 `DEFAULT_REFERER_BY_SUFFIX` 覆盖 |
| URL 签名 | 带 **`ip=` 参数**，按请求 IP 签名 —— 所以解析和取流**必须走同一个出口代理**，否则 404 |
| 限速 | 形如 `rate=500k`，数值对应码率（720P_4000K → 500KB/s ≈ 4Mbps），正常播放够用；**测试时务必带 `Range`**，否则会拉整个几百 MB 文件 |

---

## API

| 端点 | 说明 |
|---|---|
| `GET /api/sites` | 支持的站点（含 `supports_search`） |
| `GET /api/random?site=&count=` | 随机推荐。`count` 默认 10、上限 60。返回结构和 `/api/search` 一致，多一个 `random: true` |
| `GET /api/search?q=&page=&site=` | 搜索。`q` 用空格分隔多个词（AND）。返回里带 `terms` / `term_count` |
| `GET /api/thumb?id=&site=` | 兜底封面：取视频页 `og:image` |
| `GET /api/resolve?url=` 或 `?id=&site=` | 解析直链，`&refresh=1` 强制刷新 |
| `GET /api/media?u=&r=` | Range-aware 流式中转（m3u8 会自动改写 URI） |
| `GET /api/docs` | 自动生成的接口文档 |

`?id=` 的形状**各站不同**（RedTube 纯数字、PornHub 是 viewkey、hanime 是 slug），
所以只校验 URL 安全字符，页面 URL 由适配器的 `video_page_url()` 拼 —— 见下面「已知坑」。

---

## 许可证（"能不能商用"）

### 本项目本身：MIT

见仓库根目录的 [`LICENSE`](LICENSE)。可以自由使用、修改、分发、商用、闭源二次开发，
只要保留版权声明和许可声明。

选 MIT 而不是别的，是因为它和下面这张依赖表的方向一致 —— 全部宽松，没有任何 copyleft
需要你去兼容，所以你拿这个项目再做什么都不受牵连。

### 本项目的依赖 —— 全部宽松许可

| 组件 | 许可证 | 商用 |
|---|---|---|
| **yt-dlp** | **Unlicense**（公有领域） | ✅ 最宽松，无任何限制 |
| fastapi / pydantic / anyio / h11 | MIT | ✅ |
| uvicorn / httpx / starlette / httpcore / click / idna | BSD-3-Clause | ✅ |
| certifi | MPL-2.0 | ✅ 文件级 copyleft，作为依赖使用无影响 |
| **cryptography** | Apache-2.0 OR BSD-3-Clause | ✅ |
| **hls.js**（前端 CDN 引入） | Apache-2.0 | ✅ |
| **Node.js**（签名助手） | MIT | ✅ |

**本项目不包含任何 copyleft（GPL/AGPL）依赖**，所以你自己项目的许可证可以自由选择
（MIT / Apache / 闭源商业都行）—— 本项目自己选了 MIT。

### 借鉴过的项目

| 项目 | 许可证 | 用法 |
|---|---|---|
| [DonMecca/hanime-stremio](https://github.com/DonMecca/hanime-stremio) | **MIT** | `hanime_signer.js` 的浏览器环境伪造思路**移植自此**，已在文件头注明出处与版权 |
| [anime-src/hanime-stremio](https://github.com/anime-src/hanime-stremio) | **MIT** | 交叉参考（Node `vm` 沙箱方案、`refresh-htv-signature` 思路） |
| [MatrixRobots/Hanime-Api](https://github.com/MatrixRobots/Hanime-Api) | **MIT** | 交叉参考（Python 侧结构、运行时抓 vendor 的做法） |

MIT 的复用条件是**保留版权声明**，本项目的相关文件里都写了。

⚠️ **刻意避开**的项目（因为许可证不合适）：

| 项目 | 许可证 | 为什么不用 |
|---|---|---|
| Lysagxra/HanimeDownloader | **GPL-3.0** | copyleft 传染，抄进来会污染整个项目 |
| Kraptor123/Cs-GizliKeyif | **无许可证** | 默认保留所有权利，只能看不能抄 |
| rxqv/htv | **无许可证** | 同上 |

### ⚠️ 一个重要例外：那个 200KB 的 vendor 文件

hanime 的 `vendor.<hash>.min.js` 是**hanime.tv 自己的专有代码**，
**MIT 覆盖不到它**（MIT 只保护参考项目作者自己写的那部分）。

所以本项目**不分发、不打包**它，改为运行时从官方 CDN 抓取到 `.cache/hanime/`。
这样本项目仓库里只有自己写的代码。若要再干净一步，把 `.cache/` 加进 `.gitignore`。

### 许可证 ≠ 用途合法（这条比上面所有都重要）

上面的"可商用"指的是**这些代码**可以商用。但：

- 绕过 hanime.tv 的签名访问控制，可能触及其服务条款中的**反规避条款**
- hanime.tv 播放的是**未授权分发的内容**；本项目也不加载任何站点页面、不下发其广告
- 这些都是**独立于代码许可证**的法律问题

**自己用 和 商业化出售，法律风险完全不是一个量级。** 一份许可证清单不能替代这个判断。

---

## 注意

仅用于个人学习与自用。绕过广告与访问控制属于违反站点服务条款的行为。
上一节说明了各依赖的许可证，但**不构成法律意见**；若要商业使用，请自行评估用途侧的风险。
