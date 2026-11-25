# local-video-site

一个面向个人和小型局域网的本地视频下载、整理与播放平台。

`local-video-site` 将网页视频采集、断点续传、媒体库扫描、剧集管理、后台任务、
局域网访问和 Web/Android 播放整合在同一个项目中。视频文件保存在自己的硬盘或
外置存储中，元数据、任务记录和索引保存在本地 SQLite 数据库，不依赖第三方云盘。

项目适合以下场景：

- 将自己有权保存的网页视频下载到本地。
- 把分散在不同目录中的视频整理成可搜索、可播放的视频库。
- 管理电影、电视剧、动漫、纪录片等视频集、视频季和剧集。
- 在电脑、手机或 Android TV 上通过局域网观看。
- 管理外置硬盘、播放缓存、封面、预览、字幕和后台维护任务。

> 本项目不提供 DRM 解密，也不用于绕过登录、付费或版权访问控制。请只下载和
> 管理自己有权保存、传播和观看的内容。公网部署不是默认支持场景。

## 主要能力

### 网页视频下载

- 通过独立 Chrome、Chrome DevTools Protocol 和页面网络请求识别真实媒体地址。
- 支持 HLS、DASH、直接 MP4/WebM 等常见媒体形式，并由 `yt-dlp` 提供回退下载。
- 支持站点策略：媒体来源优先级、页面视频身份校验、媒体类型顺序、画质上下限、
  无匹配画质时的回退和播放按钮选择器。
- 默认优先选择高画质，并通过站点预设限制目标范围和兜底方式；任务创建时保存
  策略快照，之后修改站点策略不会影响已经创建的任务。
- 支持任务排队、暂停、继续、失败重试、断点续传和无进度自动恢复。
- 支持 HLS `.resume.json` 检查点，以及 yt-dlp `.part` 断点。
- 支持批量 URL、剧集批量发现、下载任务日志和失败原因记录。
- 下载完成后自动写入视频库，并生成缩略图、封面预览、字幕引用、分类和演员信息。

### 视频库与媒体整理

- 自动扫描 `download/`、`download-full/`、`download-hub/` 和 `download-other/`。
- 通过 SQLite 保存视频、文件信息、媒体信息、系列、季、集、任务和资产关系。
- 支持单集视频以及“视频集 → 视频季 → 剧集”的多级结构。
- 支持手动分类、自动分类、演员、收藏、访问级别和多条件检索。
- 支持视频集分类和视频集封面；视频季支持独立封面，并可继承或选择下级封面。
- 支持缩略图、自定义封面、悬浮预览和原始网页地址回填。
- 支持扫描外置硬盘中的视频；单个文件可直接入库，目录可按视频集识别季和集数。
- 支持本地目录与外置盘之间的迁移，采用分块复制和 SHA-256 完整性校验。
- 外置盘断开后保留记录并显示离线状态，重新连接后可恢复播放。

### 播放与媒体处理

- Web 播放器支持 HTTP Range 拖动、快捷键、播放进度记忆和文本字幕切换。
- 支持浏览器原生播放 MP4、WebM 等格式；不兼容文件可按需生成播放缓存。
- 支持无损重封装、播放缓存容量限制、FIFO 清理和缓存状态检查。
- MKV 默认优先使用 VLC 打开原始文件，避免为每个 MKV 额外生成整套缓存。
- 管理员可以把不兼容视频转换为标准 MP4；字幕可选择外挂字幕或写入兼容格式。
- 支持视频剪辑、格式任务、缩略图修复、预览生成和软删除任务。

### 账号、局域网与安全

- 内置游客、普通用户、管理员三类权限。
- 视频支持访问级别 `0`、`1`、`2`，服务端统一执行权限过滤。
- 游客可浏览访问级别 `0`；普通用户可收藏并管理自己的下载任务；管理员拥有管理权限。
- 局域网模式只接受本机、RFC1918 私有地址和链路本地地址，拒绝公网来源。
- 请求来源、Same-Origin、会话、下载归属和管理员权限均在服务端校验。
- 空数据库首次启动会创建 `admin / 123456`，并要求立即修改密码。
- 删除、迁移等敏感操作带二次确认、校验码或任务记录。

### 客户端

- 响应式 Web 界面，适用于桌面浏览器和手机浏览器。
- `android-tv-native` 使用 Kotlin、Jetpack Compose、Media3 ExoPlayer，同时支持
  Android TV 遥控器和手机触摸操作。
- Android TV 首页提供电视剧、电影、动漫、纪录片、其它五类虚拟入口。
- 原生客户端通过只读游客 API 获取视频，不会直接暴露管理员接口或受限视频。
- MKV 可调用 Android 版 VLC 播放原片，字幕默认优先中英双语，其次中文。

## 典型工作流

```text
网页 URL
  ↓
Chrome / CDP 识别媒体请求
  ↓
站点策略选择来源与画质
  ↓
后台下载 Worker、断点续传
  ↓
媒体标准化、缩略图、分类和演员同步
  ↓
SQLite 视频库
  ↓
Web / 手机 / Android TV 播放
```

## 技术栈

| 层级 | 技术 |
| --- | --- |
| 服务端 | Python 3.9+、FastAPI、Uvicorn |
| 页面渲染 | Jinja2、原生 JavaScript、CSS |
| 数据库 | SQLite、版本化迁移、数据完整性检查 |
| 网页自动化 | Playwright、Google Chrome、CDP |
| 下载与传输 | yt-dlp、`curl_cffi`、HTTP Range、HLS、AES-128 |
| 媒体处理 | FFmpeg、FFprobe、无损重封装、缩略图和预览生成 |
| 文本与元数据 | `jieba`、站点解析器、分类与演员抽取 |
| 客户端 | Kotlin、Jetpack Compose、AndroidX TV、Media3、Retrofit、Coil |
| 测试与质量 | `unittest`、Coverage、Ruff、Mypy、Shell/Node.js 语法检查 |
| 运行环境 | macOS 优先支持，Web 服务也可在其他 Python 环境运行 |

## 安装

### 依赖要求

- Python 3.9 或更高版本。
- Google Chrome。
- FFmpeg 和 FFprobe。
- Git。
- 使用下载功能时需要能够从服务端访问目标网页和媒体 CDN。
- 使用 Android TV 客户端时需要 Java 17、Android SDK 和 Gradle Wrapper。

macOS 可先安装常用依赖：

```bash
brew install python ffmpeg
```

需要处理 HDR/DoVi 或使用特殊色彩转换时，建议安装带 `zscale`、`libplacebo`
等滤镜的 FFmpeg：

```bash
brew install ffmpeg-full
```

程序会自动优先查找 `ffmpeg-full`，也可以通过 `LOCAL_VIDEO_FFMPEG` 指定路径。

### 获取代码

```bash
git clone git@github.com:Shawnten49/local-video-site.git
cd local-video-site
```

也可以使用 HTTPS：

```bash
git clone https://github.com/Shawnten49/local-video-site.git
cd local-video-site
```

### 创建 Python 环境

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

### 准备视频目录

默认扫描以下四个目录：

```text
download/
download-full/
download-hub/
download-other/
```

可以手动创建：

```bash
mkdir -p download download-full download-hub download-other
```

也可以把已有视频放入任意目录，然后通过 `LOCAL_VIDEO_ROOTS` 将它加入扫描范围。

## 快速开始

启动本地服务：

```bash
./start_video_site.sh --open
```

默认访问地址：

```text
http://127.0.0.1:8766
```

首次启动会自动创建数据库和默认管理员：

```text
用户名：admin
初始密码：123456
```

首次登录后必须修改密码。若准备开启局域网访问，请先完成改密。

常用启动方式：

```bash
# 后台运行
./start_video_site.sh --background

# 重新加载最新代码
./start_video_site.sh --restart

# 后台运行并阻止 macOS 空闲休眠
./start_video_site.sh --background --keep-awake

# 同一局域网访问
./start_video_site.sh --lan
```

关闭服务：

```bash
./stop_video_site.sh
```

运行状态端点：

```text
GET /api/health   # 进程是否存活
GET /api/ready    # 数据库、目录和后台 Worker 是否全部就绪
```

未就绪时，`/api/ready` 返回 HTTP `503`；正常时返回 HTTP `200`。

## 使用说明

### 浏览和播放

网站启动后会自动扫描视频目录。首页可以：

- 查看视频缩略图、标题、时长和分辨率。
- 使用悬浮预览快速浏览视频。
- 按分类、演员或收藏筛选。
- 进入视频详情页播放、切换剧集、添加收藏或更换封面。
- 对不兼容格式生成播放缓存，或使用 VLC 打开 MKV 原片。

常用页面：

| 页面 | 用途 |
| --- | --- |
| `/` | 视频首页 |
| `/downloads` | 下载任务管理 |
| `/downloads/site-policies` | 站点下载策略 |
| `/categories` | 自动分类 |
| `/actors` | 演员列表 |
| `/favorites` | 收藏 |
| `/manage/videos` | 视频管理 |
| `/manage/series` | 视频集、季、集管理 |
| `/manage/videos/external-scan` | 外置硬盘扫描 |
| `/manage/migrations` | 存储迁移 |
| `/manage/settings` | 管理员参数配置 |

### 创建下载任务

进入下载管理页，输入下载目录和一个或多个网页地址。每个 URL 会创建独立任务，
由后台 Download Worker 依次处理。

任务创建时可以选择：

- 跟随站点策略。
- 指定目标画质。
- 选择媒体来源优先级和回退方式。
- 设置是否校验页面视频身份。

站点策略页支持对网页执行“媒体发现试运行”，只分析候选媒体而不实际下载，便于
确认画质和 URL 是否选对。

### 扫描现有视频

只更新视频索引：

```bash
.venv/bin/python -m local_video_site scan
```

扫描并等待元数据、缩略图生成：

```bash
.venv/bin/python -m local_video_site scan \
  --metadata \
  --wait \
  --workers 2
```

查看最近一次扫描状态：

```bash
.venv/bin/python -m local_video_site scan --status
```

扫描指定目录：

```bash
.venv/bin/python -m local_video_site scan \
  --roots download-hub download-other \
  --metadata
```

### 命令行下载

下载单个网页视频：

```bash
.venv/bin/python -m local_video_site.downloader.entrypoints.download_video \
  "https://example.com/video/123"
```

批量下载：

```bash
.venv/bin/python -m local_video_site.downloader.entrypoints.batch_download \
  urls.txt \
  --output download-other
```

更多参数见 [使用说明.md](使用说明.md)。

### 外置硬盘

在“视频管理 → 扫描外盘视频”中选择目录后，可以：

- 直接把单个视频加入媒体库。
- 按视频集结构识别季和集。
- 先扫描、生成预览，再人工确认入库。
- 为批次设置统一的访问级别。

迁移到外置盘时，系统会先复制到临时文件，完成 SHA-256 校验后再更新数据库并
清理源文件。缩略图、封面和预览保留在本地数据目录。

## 配置

配置优先级为：

```text
环境变量 > 数据库中的管理员配置 > 代码默认值
```

常用环境变量：

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `LOCAL_VIDEO_PORT` | Web 服务端口 | `8766` |
| `LOCAL_VIDEO_DATA_DIR` | 数据、日志和媒体资产目录 | `local_video_site/data` |
| `LOCAL_VIDEO_DATABASE` | SQLite 数据库路径 | `.../data/library.db` |
| `LOCAL_VIDEO_ROOTS` | 视频扫描目录，使用系统路径分隔符 | 默认四个下载目录 |
| `LOCAL_VIDEO_LAN_MODE` | 是否启用局域网安全策略 | `0` |
| `LOCAL_VIDEO_FFMPEG` | 指定 FFmpeg 可执行文件 | 自动查找 |
| `LOCAL_VIDEO_FFMPEG_CONCURRENCY` | 同时运行的 FFmpeg 任务数 | `2` |
| `LOCAL_VIDEO_DOWNLOAD_WORKER` | 是否启用下载 Worker | `1` |
| `LOCAL_VIDEO_PLAYBACK_CACHE_MAX_BYTES` | 播放缓存总上限 | `20 GiB` |
| `LOCAL_VIDEO_LOG_LEVEL` | 日志级别 | `INFO` |
| `LOCAL_VIDEO_LOG_RETENTION_DAYS` | 日志保留天数 | `30` |

`.env.example` 是可参考的配置模板。当前项目不会自动读取 `.env`，需要在启动
Shell 中显式导入：

```bash
cp .env.example .env
# 编辑 .env 中的路径和账号配置
set -a
. ./.env
set +a
./start_video_site.sh --restart
```

完整环境变量和后台任务参数见
[local_video_site/README.md](local_video_site/README.md)。

## 数据与备份

默认数据目录：

```text
local_video_site/data/
├── library.db             # SQLite 主数据库
├── thumbnails/            # 视频缩略图
├── covers/                # 视频封面
├── series-covers/         # 视频集封面
├── season-covers/         # 视频季封面
├── previews/              # 悬浮预览
├── actor-avatars/         # 演员头像
├── subtitles/             # 外挂字幕
├── playback-cache/        # 按需播放缓存
├── download-task-logs/    # 下载任务日志
├── delete-quarantine/     # 删除隔离区
└── logs/                  # 服务日志
```

建议备份：

- 视频源目录。
- `library.db`。
- `local_video_site/data/` 中的封面、缩略图、字幕和必要配置。

数据库、日志、下载结果和 `.env` 属于本地运行数据，不应提交到 Git。

服务日志按天写入：

```text
local_video_site/data/logs/server-YYYY-MM-DD.log
```

实时查看：

```bash
tail -f "$(ls -t local_video_site/data/logs/server-*.log | head -1)"
```

## 项目结构

```text
local-video-site/
├── local_video_site/      # Web 服务、下载器、媒体库和后台任务
├── android-tv-native/     # Kotlin + Compose 原生 Android/TV 客户端
├── android-tv-shell/      # 已废弃的 WebView 客户端，仅作历史参考
├── tests/                 # Python 单元与集成测试
├── scripts/               # 检查和运维脚本
├── docs/                  # 架构与播放器调研文档
├── start_video_site.sh    # 启动脚本
└── stop_video_site.sh     # 停止和残留进程清理脚本
```

主要服务端模块：

| 目录 | 职责 |
| --- | --- |
| `downloader/` | Chrome/CDP、媒体嗅探、HLS、策略和命令行下载 |
| `downloads/` | 下载任务、进度、暂停恢复和 Worker |
| `ingestion/` | 目录扫描、外盘扫描和导入 |
| `episodes/` | 视频集、视频季、剧集和批量剧集发现 |
| `media/` | 缩略图、封面、字幕、预览、重封装、剪辑和格式任务 |
| `storage/` | 外置盘识别、迁移和删除任务 |
| `catalog/` | 分类、演员和 TV 首页数据 |
| `identity/` | 账号、会话、角色和权限 |
| `web/` | FastAPI 路由、页面和安全中间件 |
| `jobs/` | 后台任务运行时、租约、完整性和 readiness |

## Android TV / 手机客户端

构建原生客户端：

```bash
cd android-tv-native
./gradlew assembleRelease \
  -PLOCAL_VIDEO_SERVER_URL=http://YOUR_LAN_IP:8766/
```

APK 输出：

```text
android-tv-native/app/build/outputs/apk/release/app-release.apk
```

安装到电视：

```bash
adb connect TV_IP:5555
adb install -r app/build/outputs/apk/release/app-release.apk
```

服务端应使用局域网模式启动：

```bash
./start_video_site.sh --lan
```

客户端的详细构建、遥控器操作和 VLC 配置见
[android-tv-native/README.md](android-tv-native/README.md)。

## 开发与测试

安装开发依赖：

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
```

运行完整检查：

```bash
./scripts/check.sh
```

该命令会执行：

- Shell 语法检查。
- 敏感信息扫描。
- Python 编译。
- JavaScript 语法检查。
- Ruff。
- Mypy。
- Coverage + `unittest`。

同时构建两个 Android 客户端：

```bash
CHECK_ANDROID=1 ./scripts/check.sh
```

## 已知限制

- 不处理 Widevine、FairPlay 等 DRM 内容。
- 网页播放器、CDN 和站点结构会变化，站点解析规则需要持续维护。
- 浏览器只暴露 `blob:` 且没有任何可观察媒体请求时，可能无法自动取得真实来源。
- 浏览器对 MKV、RMVB、部分 AV1/HEVC 文件支持有限，可能需要 VLC、重封装或转换。
- `--keep-awake`、VLC 自动调用和外置盘自动发现主要围绕 macOS 设计。
- 本项目默认面向个人和可信局域网，不应直接暴露到公网。

## 相关文档

- [使用说明.md](使用说明.md)：下载器命令行参数和故障处理。
- [local_video_site/README.md](local_video_site/README.md)：服务端完整配置和运维说明。
- [android-tv-native/README.md](android-tv-native/README.md)：Android/TV 客户端说明。
- [CONTEXT.md](CONTEXT.md)：视频、视频集、视频季等领域词汇。
- [docs/phase2-architecture.md](docs/phase2-architecture.md)：后台任务与媒体库架构。
- [docs/site-resolution-architecture.md](docs/site-resolution-architecture.md)：站点解析与画质策略。
- [SECURITY.md](SECURITY.md)：安全策略和漏洞报告方式。
