@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo   NoADPornSite  -  本地视频直链播放器
echo ============================================================
echo.

REM ---------- 1. Python 必须在 PATH 里 ----------
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 找不到 python。
    echo         请安装 Python 3.10 或更新，安装时**勾选** "Add python.exe to PATH"。
    echo         https://www.python.org/downloads/
    pause
    exit /b 1
)

REM ---------- 2. Python 版本必须 >= 3.10 ----------
REM fastapi / uvicorn / starlette / anyio / click / yt-dlp 的 Requires-Python
REM 全都声明了 >=3.10。版本太低时 pip 只会丢一堆看不懂的解析错误，这里提前拦下来。
python -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [错误] Python 版本过低，需要 3.10 或更新。当前版本：
    python --version
    pause
    exit /b 1
)

REM ---------- 3. 依赖：缺了就自动装 ----------
REM 注意要连 cryptography 一起试探：它是**懒加载**的（只有 hanime 握手时才 import），
REM 只检查前四个的话，会漏装它，表现为"启动一切正常、一播 hanime 就崩"。
python -c "import fastapi,uvicorn,httpx,yt_dlp,cryptography" >nul 2>&1
if errorlevel 1 (
    echo [提示] 缺少依赖，正在自动安装（首次约几十 MB，需要能连 pypi）...
    echo.
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [错误] 依赖安装失败。如果是网络问题，可以换国内镜像重试：
        echo        python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
        pause
        exit /b 1
    )
    echo.
)

REM ---------- 4. Node：只有 hanime 取流需要，缺了不拦 ----------
node --version >nul 2>&1
if errorlevel 1 (
    echo [提示] 没找到 node —— RedTube / PornHub 照常可用，
    echo        但 hanime.tv 取流需要它（WASM 签名助手）。建议装一个 Node 18+：
    echo        https://nodejs.org/
    echo.
)

REM ---------- 5. ffmpeg：只有 hanime 的「下载」需要，缺了不拦 ----------
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo [提示] 没找到 ffmpeg —— 播放、以及 RedTube / PornHub 的下载都不受影响，
    echo        但 hanime 的视频「另存为」需要它（要把上百个加密分片解密再封装）。
    echo        下载后把 bin 目录加进 PATH：https://www.gyan.dev/ffmpeg/builds/
    echo.
)

echo 启动中... 浏览器打开 http://127.0.0.1:8000
echo 按 Ctrl+C 停止
echo.
python app.py

pause
