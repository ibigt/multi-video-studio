 # Video Studio

一个单服务运行的 RTSP 多路流媒体管理平台：**FastAPI + SQLite + FFmpeg + 原生 JavaScript + TailwindCSS**。

浏览器不能直接播放 RTSP，因此服务端按需将选中的流转换为短切片 HLS；录制则使用独立的 FFmpeg 进程直接写入 MP4。前后端共用一个 FastAPI 进程，无需分开部署。

![Video Studio 界面](docs/screenshot.png)

<div>
  <a href="docs/six-up.png">六路同屏布局</a> ·
  <a href="docs/mobile.png">移动端布局</a>
</div>

## 功能

- ✅ 新增、编辑、删除 RTSP 流，配置持久化到 SQLite
- ✅ 自由切换 **1～6 路**同屏 HLS 实时预览，支持静音、全屏、清空画面
- ✅ 每路流独立的录制开始/停止按钮，录制为 MP4 并按时间自动命名
- ✅ 录制文件列表，网页端直接回放（支持 HTTP Range 拖动进度）、下载、删除
- ✅ `ffprobe` 定时探测连接状态，在线/离线实时标记，断流弹出提示并自动恢复
- ✅ 每路流可开关「断流自动重连」：预览转码异常退出或源恢复后自动重启，浏览器端自动重新挂载 HLS
- ✅ 预览进程按需启动、空闲自动回收；录制进程独立隔离，优雅停止以写入完整 MP4 索引
- ✅ 基础日志、全局异常捕获、请求参数校验、文件名/路径安全处理
- ✅ TailwindCSS 编译后的静态页面由 FastAPI 托管，单服务启动
- ✅ 响应式布局，桌面端/移动端自适应

## 技术架构

```
┌───────────────────────────── 浏览器（Tailwind + 原生 JS） ─────────────────────────────┐
│  流管理 CRUD        hls.js 播放 1~6 路预览        录制控制 / 文件回放 / 状态轮询           │
└───────────────────────────────────┬────────────────────────────────────────────────────┘
                                    │  HTTP / JSON / HLS
┌───────────────────────────────────▼────────────────────────────────────────────────────┐
│ FastAPI 单服务                                                                          │
│  ├─ routers/        streams / recordings / media / system                              │
│  ├─ StreamManager  按需启动 FFmpeg → HLS（空闲回收、自动重连）                           │
│  ├─ RecorderManager 独立 FFmpeg 进程 → MP4（优雅停止、启动恢复、临时文件清理）             │
│  ├─ StatusMonitor  ffprobe 子进程定时测活 → SQLite 状态                                   │
│  └─ StaticFiles    SPA、编译后的 Tailwind CSS、内置 hls.js                                │
└───────────────────┬───────────────────────────┬────────────────────────────────────────┘
                    │                           │
               SQLite 数据库                FFmpeg / ffprobe 子进程
               data/rtsp_studio.db          （不占用 FastAPI 事件循环）
```

### 数据流

**实时预览**

1. 页面选择一路流 → `POST /api/streams/{id}/preview/start`
2. `PreviewManager` 为这路流启动一个隔离的 FFmpeg 进程，输出 1 秒切片的 HLS
3. 前端轮询 `GET /api/streams/{id}/preview/ready`，播放列表就绪后再交给 hls.js
4. hls.js 请求 `/media/hls/{id}/index.m3u8` 和 TS 分片；每次请求都会刷新空闲计时
5. 页面关闭 / 切换流 / 75 秒无请求后，服务端自动结束对应 FFmpeg 进程并清理 HLS 临时目录

**录制**

1. `POST /api/streams/{id}/recording/start` 创建记录并启动独立 FFmpeg
2. FFmpeg 将 RTSP 转码为 H.264/AAC 写入 `*.part.mp4`
3. 停止时向 FFmpeg 的 stdin 写入 `q`，让 MP4 的 `moov` 索引完整落盘
4. 使用 `ffprobe` 校验产物后重命名为 `{流名称}_{YYYYmmdd_HHMMSS}.mp4`，状态更新为 `completed`
5. 进程异常退出会标记为 `failed` 并清理残缺文件；服务重启会修复遗留的「录制中」记录

## 项目结构

```text
.
├── app/
│   ├── main.py                  # FastAPI 入口、生命周期、静态资源托管
│   ├── config.py                # 集中配置（pydantic-settings / .env）
│   ├── database.py              # 异步 SQLite、WAL、Session
│   ├── models.py                # SQLAlchemy 模型：Stream / Recording
│   ├── schemas.py               # Pydantic 请求/响应契约
│   ├── constants.py             # 状态枚举常量
│   ├── api_utils.py             # ORM → API 响应转换
│   ├── dependencies.py          # FastAPI 服务依赖
│   ├── logging_config.py        # 控制台 + 轮转文件日志
│   ├── timeutils.py             # UTC / 本地时间处理
│   ├── routers/
│   │   ├── streams.py           # 流 CRUD、状态检测、预览控制
│   │   ├── recordings.py        # 录制启停、回放、下载、删除
│   │   ├── media.py             # HLS 播放列表/分片
│   │   └── system.py            # 健康检查、运行信息
│   ├── services/
│   │   ├── stream_manager.py    # HLS 预览进程、空闲回收、重连
│   │   ├── recorder.py          # MP4 录制进程、优雅停止、产物校验
│   │   ├── status_monitor.py    # ffprobe 周期测活
│   │   └── ffmpeg_utils.py      # 命令构造、进程控制、脱敏、路径安全
│   └── static/
│       ├── index.html           # 单页 UI
│       ├── css/app.css          # 已编译的 Tailwind 产物（可直接运行）
│       ├── js/app.js            # 页面状态、预览、录制、轮询
│       ├── js/api.js            # fetch API 封装
│       ├── vendor/hls.min.js    # 本地内置 hls.js，无需 CDN
│       └── favicon.svg
├── data/                        # 运行时生成（数据库 / 录制 / HLS / 日志）
├── tests/                       # pytest API 与工具函数测试
├── docs/screenshot.png
├── requirements.txt
├── requirements-dev.txt
├── run.py                       # 读取 .env 中的 HOST/PORT 后启动
├── .env.example
├── pyproject.toml               # ruff / pytest 配置
├── tailwind.config.js           # 可选：重新编译 CSS
└── package.json
```

## 快速开始

### 1. 环境要求

- Python **3.10+**（推荐 3.11/3.12）
- FFmpeg **5.0+**，且 `ffmpeg`、`ffprobe` 在 `PATH` 中
- 现代浏览器（Chrome / Edge / Safari / Firefox）

```bash
ffmpeg -version
ffprobe -version
```

Windows 可使用：

```powershell
winget install Gyan.FFmpeg
# 安装后重新打开终端
```

macOS：`brew install ffmpeg`；Debian/Ubuntu：`sudo apt install ffmpeg`。

### 2. 安装依赖

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python run.py
```

Linux / macOS：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python run.py
```

浏览器打开：**http://127.0.0.1:8000**

> `python run.py` 会读取 `.env` 中的 `HOST` / `PORT`。也可以直接使用：
>
> ```bash
> uvicorn app.main:app --host 0.0.0.0 --port 8000
> ```
>
> 只能使用 **单 worker**。多 worker 会各自持有独立的进程管理器和 SQLite 连接，导致预览/录制任务无法共享。

### 3. 使用

1. 点击「新增流」，填写名称和 RTSP 地址，例如：
   `rtsp://admin:password@192.168.1.20:554/Streaming/Channels/101`
2. 在「实时预览」的任意画面下拉框选择流；顶部 `1~6` 切换同屏路数。
3. 点击画面右下角「录制」或流列表中的「录制」开始录制，再次点击停止并保存。
4. 在「录制文件」中点击播放按钮直接回放，或下载原始 MP4。
5. 状态每 15 秒自动检测，也可点击「检测全部」或单行刷新按钮立即检测。

## 配置

所有配置集中在 `app/config.py`，通过项目根目录 `.env` 覆盖；无需改代码。完整模板见 [.env.example](.env.example)。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `HOST` | `0.0.0.0` | 监听地址 |
| `PORT` | `8000` | 服务端口（`python run.py` 使用） |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `ACCESS_LOG` | `true` | Uvicorn 访问日志 |
| `DATA_DIR` | `./data` | 数据根目录 |
| `RECORDINGS_DIR` | `./data/recordings` | MP4 保存目录 |
| `HLS_DIR` | `./data/hls` | HLS 临时目录 |
| `LOGS_DIR` | `./data/logs` | 日志目录 |
| `DATABASE_URL` | 自动 | 默认 `sqlite+aiosqlite:///{DATA_DIR}/rtsp_studio.db` |
| `FFMPEG_BIN` | `ffmpeg` | FFmpeg 可执行文件路径 |
| `FFPROBE_BIN` | `ffprobe` | ffprobe 可执行文件路径 |
| `STREAM_TIMEOUT_SECONDS` | `10` | 探测/读取流超时（秒） |
| `STATUS_CHECK_INTERVAL_SECONDS` | `15` | 定时测活间隔 |
| `STATUS_CONCURRENCY` | `4` | 同时执行的探测数 |
| `AUTO_RECONNECT_DEFAULT` | `true` | 新增流时未传值则使用此默认 |
| `RECONNECT_DELAY_SECONDS` | `3` | 预览自动重连基础退避 |
| `PREVIEW_WIDTH` | `1280` | 预览最大宽度，低配可设 `960`/`720` |
| `PREVIEW_FPS` | `15` | 预览帧率 |
| `PREVIEW_BITRATE` | `1500k` | 预览码率 |
| `PREVIEW_HLS_TIME` | `1` | HLS 分片时长（秒） |
| `PREVIEW_HLS_LIST_SIZE` | `6` | HLS 播放列表保留分片数 |
| `PREVIEW_IDLE_TIMEOUT_SECONDS` | `75` | 无请求多久后回收预览进程 |
| `RECORDING_BITRATE` | `2500k` | 录制视频码率 |
| `RECORDING_AUDIO_BITRATE` | `96k` | 录制音频码率 |
| `RECORDING_PRESET` | `veryfast` | x264 preset |
| `RECORDING_MAX_SECONDS` | `0` | `0` 为手动停止；大于 0 时达到时长自动保存 |
| `RECORDING_STOP_TIMEOUT_SECONDS` | `12` | 优雅停止等待上限，超时后强制结束 |

示例：降低 6 路并发预览的 CPU 占用：

```dotenv
PREVIEW_WIDTH=960
PREVIEW_FPS=12
PREVIEW_BITRATE=800k
RECORDING_BITRATE=1800k
```

## API

启动后可访问 Swagger：**http://127.0.0.1:8000/docs**。

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/health` | 健康检查、FFmpeg 状态 |
| `GET` | `/api/system/info` | 目录、码率、流/录制统计 |
| `GET` | `/api/streams` | 流列表（含实时预览/录制状态） |
| `POST` | `/api/streams` | 新增流 |
| `GET` | `/api/streams/{id}` | 流详情 |
| `PUT` | `/api/streams/{id}` | 编辑流 |
| `DELETE` | `/api/streams/{id}` | 删除流（录制中会先停止并保存） |
| `POST` | `/api/streams/{id}/check` | 立即检测连接 |
| `POST` | `/api/streams/{id}/preview/start` | 启动该流 HLS 转码 |
| `GET` | `/api/streams/{id}/preview/ready` | 播放列表是否已就绪 |
| `POST` | `/api/streams/{id}/preview/stop` | 停止预览转码 |
| `POST` | `/api/streams/{id}/recording/start` | 开始录制 |
| `POST` | `/api/streams/{id}/recording/stop` | 停止并保存录制 |
| `GET` | `/api/recordings` | 录制文件列表 |
| `GET` | `/api/recordings/{id}/file` | 在线回放（支持 Range） |
| `GET` | `/api/recordings/{id}/download` | 下载 MP4 |
| `DELETE` | `/api/recordings/{id}` | 删除录制记录及文件 |
| `GET` | `/media/hls/{stream_id}/index.m3u8` | HLS 播放列表 |

请求示例：

```bash
curl -X POST http://127.0.0.1:8000/api/streams \
  -H "Content-Type: application/json" \
  -d '{"name":"大厅入口","url":"rtsp://admin:pass@192.168.1.20:554/stream1","auto_reconnect":true}'
```

## 关键行为说明

### 预览与延迟

- 浏览器预览使用 **HLS**，不是 WebRTC；端到端延迟通常 **2～6 秒**。这是为了兼容浏览器、降低实现复杂度和支持 6 路并发。
- 每路预览都会用 x264 转码为浏览器可播放的 H.264 + AAC。6 路 720p/15fps 对 CPU 有明显压力，建议按机器性能下调 `PREVIEW_WIDTH` / `PREVIEW_FPS`。
- HLS 进程按需启动，不会为未观看的流持续转码。

### 录制

- 每个录制都是独立的 FFmpeg 进程，不阻塞 FastAPI 事件循环，也不与预览转码共享进程。
- 录制会把源视频转码为 H.264/AAC（因此 `RECORDING_BITRATE` 生效），生成标准 MP4。
- 文件命名示例：`大厅入口_20261124_231500.mp4`（服务器本地时间）。
- 写入时先使用 `.part.mp4`，停止并校验通过后才出现最终文件，因此列表中不会出现半成品。
- 录制期间**不要**直接删除 `data/recordings` 下的临时文件。
- 源流断开时，当前录制进程会退出，记录标记为 `failed`，不会把两段不同连接拼进同一个 MP4。此场景请开启预览自动重连或重新开始录制。

### 断流重连

- `auto_reconnect` 是每路流独立配置。
- 服务端：预览 FFmpeg 异常退出后按退避间隔重启，直到流恢复或被空闲回收。
- 浏览器端：HLS 遇到网络错误后自动重挂载；源恢复后通常可自动继续播放，也会显示「正在等待自动重连」。
- 状态提示独立于重连：`ffprobe` 周期测活结果会写入 SQLite，前端轮询并弹出断流提示。
- 录制进程没有自动拼接重连，原因见上。

### 数据与日志

```text
data/
├── rtsp_studio.db       # SQLite（WAL 模式）
├── recordings/          # 最终 MP4
├── hls/                 # 预览临时分片（可随时清理）
└── logs/
    ├── app.log          # 应用日志，5MB × 3 轮转
    ├── preview_*.log    # 每路预览的 FFmpeg stderr
    └── recording_*.log  # 每次录制的 FFmpeg stderr
```

RTSP 密码会保存在 SQLite 中（密码属于连接地址的一部分），不会写入普通日志或 API 错误消息；前端列表中的 URL 会显示为 `***@` 脱敏形式。该应用默认不包含用户认证，请勿直接暴露到公网；需要远程访问时建议放在带 HTTPS 与认证的反向代理之后，或将 `HOST` 改为 `127.0.0.1`。

## 开发与测试

```bash
pip install -r requirements-dev.txt
pytest
ruff check app tests
```

测试使用临时数据目录，不会污染 `data/`。

重新编译 Tailwind（仅修改样式时需要，运行时不需要 Node）：

```bash
npm install
npm run build:css
# 或开发时监听：npm run watch:css
```

## 常见问题

**启动时报「FFmpeg 或 ffprobe 未找到」**

安装 FFmpeg 并加入 `PATH`，或在 `.env` 中填写绝对路径：

```dotenv
FFMPEG_BIN=C:/ffmpeg/bin/ffmpeg.exe
FFPROBE_BIN=C:/ffmpeg/bin/ffprobe.exe
```

**流状态在线，但预览一直转圈**

- 查看 `data/logs/preview_<id>.log`。常见原因：摄像头只允许 1 路连接、编码为 H.265、URL 需要特定传输协议、账号无权限。
- 如果同时开着预览和录制，相机可能达到连接数上限。
- 可提高 `STREAM_TIMEOUT_SECONDS` 或降低并发路数。

**RTSP 连接失败 / 401 / 403**

用 ffprobe 单独验证地址：

```bash
ffprobe -rtsp_transport tcp -v error "rtsp://user:pass@camera/stream"
```

检查用户名、密码中的特殊字符是否做了 URL 编码（例如 `@` 应编码为 `%40`）、摄像头是否启用 RTSP、端口是否被防火墙拦截。

**录制停止后列表没有文件**

查看 `data/logs/recording_*.log`。若停止超时（`RECORDING_STOP_TIMEOUT_SECONDS`）会强制结束，残缺文件会被清理并标记失败。可适当调大超时。

**端口被占用**

修改 `.env` 中 `PORT`，然后通过 `python run.py` 启动；或显式使用 `uvicorn ... --port 新端口`。

**页面能打开但视频控制台 404**

正常启动阶段播放列表尚未生成时，前端会等待就绪后再加载。若持续 404，检查预览进程是否被空闲回收（`preview_active` 为 false），或查看预览日志。

## License

MIT
