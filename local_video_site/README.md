# 本地视频库

FastAPI 本地视频浏览网站。代码会扫描现有下载目录，将视频索引写入 SQLite，
生成缩略图，并通过支持 HTTP Range 的接口播放视频。

## 启动

```bash
cd /path/to/local-video-site
./start_video_site.sh
```

启动脚本默认不打开浏览器。需要后台运行：

```bash
./start_video_site.sh --background
```

后台模式的 PID 默认写入 `local_video_site/data/video-site.pid`，控制台输出追加到
`local_video_site/data/logs/background-YYYY-MM-DD.log`。如需显式打开浏览器：

```bash
./start_video_site.sh --open
```

网站运行期间阻止 macOS 因空闲进入睡眠：

```bash
./start_video_site.sh --keep-awake
./start_video_site.sh -k
./start_video_site.sh --keep-awake --background
```

`--keep-awake` 使用 macOS 内置的 `caffeinate -is`，不会保持显示器常亮。防休眠
PID 默认写入 `local_video_site/data/keep-awake.pid`。该参数不能阻止手动睡眠、
合上笔记本上盖、断电或网络中断；长期在线仍应保持电脑接通电源。

关闭服务：

```bash
./stop_video_site.sh
```

统一运行语法检查、Python 编译和完整测试：

```bash
./scripts/check.sh
```

同时构建两个 Android 客户端：

```bash
CHECK_ANDROID=1 ./scripts/check.sh
```

默认地址：

```text
http://127.0.0.1:8766
```

运行状态与就绪检查：

```text
GET /api/health   # 进程存活
GET /api/ready    # 数据库、内部存储目录、后台 Worker 就绪状态
```

`/api/ready` 在未就绪时返回 HTTP `503` 和 `Retry-After: 5`。

管理员可以查看和持久化运行时数据完整性检查：

```text
GET  /api/manage/integrity
POST /api/manage/integrity/run
```

手机局域网访问：

```bash
./start_video_site.sh --lan
```

局域网后台启动：

```bash
./start_video_site.sh --lan --background
```

局域网模式使用现有账号系统，并拒绝非私有网络来源。发布前必须至少存在一个
有效管理员账号，且默认管理员 `admin / 123456` 必须已经完成首次改密。
权限规则为：游客只能浏览访问级别 `0`；普通用户可以浏览级别 `0`、`1` 并管理
自己的下载任务；管理员拥有全部级别及管理权限。

账号体系之外的网络来源、视频访问级别、下载任务归属、Same-Origin 请求和
首次管理员初始化与强制改密校验仍然生效，不应删除。

首次启动会快速扫描以下目录，并在后台生成元数据和缩略图：

- `download/`
- `download-full/`
- `download-hub/`
- `download-other/`

## Android TV 原生游客 API

原生电视客户端使用 `/api/tv/v1/` 只读接口，不需要 Cookie，也不会暴露管理员
页面或访问级别 `1`、`2` 的视频。第一版提供全部视频、详情/剧集和播放准备：

```text
GET  /api/tv/v1/ping
GET  /api/tv/v1/videos
GET  /api/tv/v1/videos/{video_id}
GET  /api/tv/v1/videos/{video_id}/playback
POST /api/tv/v1/videos/{video_id}/prepare
```

所有查询在服务端按 `access_level = 0`、`missing = 0`、`is_deleted = 0`
过滤。分类和演员接口留到原生客户端下一版本。

## 手动扫描

```bash
# 只更新索引
.venv/bin/python -m local_video_site scan

# 更新索引并等待元数据和缩略图生成完成
.venv/bin/python -m local_video_site scan --metadata --wait --workers 2

# 查看最近一次扫描状态
.venv/bin/python -m local_video_site scan --status

# 实时查看服务及扫描日志
tail -f "$(ls -t local_video_site/data/logs/server-*.log | head -1)"

# 重新生成全部自动分类
.venv/bin/python -m local_video_site classify --rebuild

# 清空分类并重新获取 xHamster 官网分类
.venv/bin/python -m local_video_site classify \
  --rebuild --clear --fetch-xhamster --workers 2

# 同一次抓取会同时更新普通分类和演员数据

# 获取演员头像源地址后，下载本地演员头像
.venv/bin/python -m local_video_site actor-avatars \
  --retry-failed \
  --report local_video_site/data/logs/actor-avatar-download.json

# 仅处理指定演员
.venv/bin/python -m local_video_site actor-avatars --actor-id 932

# 预检需要原地重封装的视频
.venv/bin/python -m local_video_site playback-normalize --all --dry-run

# 原地重封装全部分片 MP4，不重新编码
.venv/bin/python -m local_video_site playback-normalize --all --workers 1

# 查看并按 10 GiB 上限清理播放缓存
.venv/bin/python -m local_video_site playback-cache --status
.venv/bin/python -m local_video_site playback-cache \
  --prune --max-size 10GiB --max-age-days 30

# 修复没有封面的视频
.venv/bin/python -m local_video_site thumbnails --missing

# 从下载汇总中补全视频原始网页地址
.venv/bin/python -m local_video_site backfill-urls --summary logs/summary.jsonl
```

指定目录：

```bash
.venv/bin/python -m local_video_site scan \
  --roots download-hub download-other \
  --metadata
```

## 配置

环境变量：

| 变量 | 说明 |
| --- | --- |
| `LOCAL_VIDEO_ROOTS` | 扫描目录，使用系统路径分隔符连接 |
| `LOCAL_VIDEO_DATA_DIR` | 数据和缩略图目录 |
| `LOCAL_VIDEO_DATABASE` | SQLite 文件路径 |
| `LOCAL_VIDEO_CAFFEINATE` | `--keep-awake` 使用的 `caffeinate` 可执行文件路径 |
| `LOCAL_VIDEO_KEEP_AWAKE_PID_FILE` | 防休眠进程 PID 文件路径 |
| `LOCAL_VIDEO_MIN_BYTES` | 入库最小文件大小，默认 1 MiB |
| `LOCAL_VIDEO_SCAN_ON_STARTUP` | 是否在服务启动时扫描，默认 `1` |
| `LOCAL_VIDEO_SCAN_WAIT` | 是否等待元数据和缩略图完成，默认 `0` |
| `LOCAL_VIDEO_CATEGORY_FETCH_ON_STARTUP` | 后台补抓未缓存的 xHamster 分类，默认 `1` |
| `LOCAL_VIDEO_PLAYBACK_CACHE` | 播放缓存目录 |
| `LOCAL_VIDEO_PLAYBACK_CACHE_MAX_BYTES` | 播放缓存总上限，默认 20 GiB |
| `LOCAL_VIDEO_PLAYBACK_CACHE_MAX_AGE_DAYS` | 播放缓存年龄清理，默认 `0` 表示只按容量清理 |
| `LOCAL_VIDEO_PLAYBACK_CACHE_PROTECT_SECONDS` | 最近访问缓存的保护时间，默认 `1800` 秒 |
| `LOCAL_VIDEO_PLAYBACK_REMUX_RESERVE_BYTES` | 重封装前要求保留的磁盘安全空间，默认 10 GiB |
| `LOCAL_VIDEO_FFMPEG_CONCURRENCY` | 全项目同时运行的 FFmpeg 进程上限，默认 `2` |
| `LOCAL_VIDEO_FFMPEG_INTERACTIVE_WAIT_SECONDS` | 在线播放准备等待 FFmpeg 槽位的时间，默认 `8` 秒 |
| `LOCAL_VIDEO_MOVI_PLAYER` | 浏览器兼容播放器模式：`off`、`admin`、`auto`、`force`，默认 `auto` |
| `LOCAL_VIDEO_MOVI_LOAD_TIMEOUT_SECONDS` | movi-player 加载超时，默认 20 秒 |
| `LOCAL_VIDEO_MOVI_BUFFER_MB` | movi-player 源数据缓存目标，默认 96 MiB |
| `LOCAL_VIDEO_MIGRATION_WORKER` | 是否启动视频迁移 Worker，默认 `1` |
| `LOCAL_VIDEO_MIGRATION_POLL_SECONDS` | 迁移任务扫描间隔，默认 3 秒 |
| `LOCAL_VIDEO_MIGRATION_CHUNK_BYTES` | 复制分块大小，默认 8 MiB |
| `LOCAL_VIDEO_MIGRATION_MIN_FREE_BYTES` | 迁移后要求保留的目标磁盘空间，默认 1 GiB |
| `LOCAL_VIDEO_MIGRATION_VERIFY` | 完整性校验方式，默认 `sha256` |
| `LOCAL_VIDEO_AUTO_MIGRATE_AGE_DAYS` | 自动迁往外盘的视频年龄阈值，默认 30 天 |
| `LOCAL_VIDEO_VOLUME_ROOTS` | 自动发现外置存储卷的根目录，默认 `/Volumes` |
| `LOCAL_VIDEO_STORAGE_REFRESH_SECONDS` | 后台刷新外置盘状态的间隔，默认 30 秒 |
| `LOCAL_VIDEO_EXTERNAL_SCAN_WORKER` | 是否启动外盘扫描 Worker，默认 `1` |
| `LOCAL_VIDEO_EXTERNAL_SCAN_POLL_SECONDS` | 外盘扫描队列检查间隔，默认 2 秒 |
| `LOCAL_VIDEO_EXTERNAL_SCAN_MAX_FILES` | 单次扫描最大文件数，默认 500 |
| `LOCAL_VIDEO_FORMAT_WORKER` | 是否启动 MKV 直接封装 Worker，默认 `1` |
| `LOCAL_VIDEO_FORMAT_POLL_SECONDS` | 格式任务队列检查间隔，默认 3 秒 |
| `LOCAL_VIDEO_FORMAT_TIMEOUT_SECONDS` | 单个格式任务超时，默认 7200 秒 |
| `LOCAL_VIDEO_FORMAT_DISK_RESERVE_BYTES` | 封装期间额外保留的磁盘空间，默认 1 GiB |
| `LOCAL_VIDEO_FORMAT_BATCH_MAX` | 单批最多加入的 MKV 数量，默认 500 |
| `LOCAL_VIDEO_DELETE_WORKER` | 是否启动视频/季/集删除 Worker，默认 `1` |
| `LOCAL_VIDEO_DELETE_POLL_SECONDS` | 删除任务扫描间隔，默认 3 秒 |
| `LOCAL_VIDEO_DOWNLOAD_WORKER` | 是否启动网页下载任务 Worker，默认 `1` |
| `LOCAL_VIDEO_DOWNLOAD_POLL_SECONDS` | 扫描下载任务的间隔，默认 `5` 秒 |
| `LOCAL_VIDEO_DOWNLOAD_TASK_ATTEMPTS` | 下载任务最大尝试次数，默认 `3` |
| `LOCAL_VIDEO_DOWNLOAD_CDP_PORT` | 下载任务复用的 Chrome CDP 端口，默认 `9222` |
| `LOCAL_VIDEO_DOWNLOAD_TIMEOUT_SECONDS` | 单个下载任务超时，默认 6 小时 |
| `LOCAL_VIDEO_DOWNLOAD_KEEP_AWAKE` | macOS 下载期间阻止系统睡眠，默认 `1` |
| `LOCAL_VIDEO_DOWNLOAD_KEEP_AWAKE_FLAGS` | `caffeinate` 参数，默认 `-ims` |
| `LOCAL_VIDEO_HLS_MANIFEST_TIMEOUT_SECONDS` | HLS 清单请求超时，默认 20 秒 |
| `LOCAL_VIDEO_HLS_SEGMENT_TIMEOUT_SECONDS` | HLS 分段请求超时，默认 45 秒 |
| `LOCAL_VIDEO_DOWNLOAD_STALL_TIMEOUT_SECONDS` | 无进度自动恢复阈值，默认 180 秒 |
| `LOCAL_VIDEO_PREVIEW_ENABLED` | 是否启用后台预览片段生成，默认 `1` |
| `LOCAL_VIDEO_PREVIEW_WIDTH` | 预览最大宽度，默认 `640` |
| `LOCAL_VIDEO_PREVIEW_DURATION` | 预览时长，默认 8 秒 |
| `LOCAL_VIDEO_PREVIEW_CRF` | H.264 CRF，默认 `28` |
| `LOCAL_VIDEO_LOG_LEVEL` | 日志级别，默认 `INFO` |
| `LOCAL_VIDEO_LOG_RETENTION_DAYS` | 每日日志保留天数，默认 `30` |

数据默认保存在：

```text
local_video_site/data/
```

## 扫描外盘视频

视频管理页提供“扫描外盘视频”入口。扫描只读取外盘源文件，不复制、不移动、
不删除源文件；缩略图和预览片段保存在本地数据目录。

- 单个视频解析后直接加入视频库。
- 视频集模式识别季和集数，并生成视频集、季、集关系。
- 目录按普通批量扫描时先生成缩略图、预览和暂存记录，人工选择后才写入视频库。
- 每个扫描批次和普通批量明细都可以设置访问级别 `0`、`1`、`2`。
- 视频集内所有集数使用同一访问级别。
- 外盘拔出后视频显示“外置盘离线”；重新挂载后根据卷身份恢复路径。

## 播放功能

- 原生进度条、快进、快退、音量、全屏。
- `←` / `→`：5 秒。
- `J` / `L`：10 秒。
- 空格：播放/暂停。
- `M`：静音。
- `F`：全屏。
- 播放位置保存在浏览器本地存储。
- 首页右上角“分类”入口，按标题分词生成分类并按视频数量排序。
- 首页、分类页和分类视频集合页支持首页、上一页、下一页、尾页，以及下拉选择页码跳转。
- 首页“收藏”入口展示已收藏视频，详情页顶部可以添加或取消收藏。
- 首页“演员”入口展示带本地头像的演员清单；演员详情页顶部展示头像，视频详情页
  底部分别展示演员和普通分类。
- 首页视频卡片支持从当前播放时间截取自定义封面，并可以恢复自动封面。
- 视频详情页支持两步确认和删除校验码校验。视频文件和派生资源会被删除，视频表
  记录使用 `is_deleted` 标识软删除；分类、演员、收藏和任务历史保留。

普通 H.264/AAC MP4、标准 AV1 MP4 和 VP8/VP9 WebM 会直接播放。分片 AV1 MP4
会在下载完成或执行 `playback-normalize` 时，通过 FFmpeg `-c copy` 原地转换为
普通 MP4。该过程不重新编码，长期不保留第二份视频。

真正无法无损标准化的文件才按需生成播放缓存：

```text
local_video_site/data/playback-cache/
```

播放缓存有总容量上限，启动和生成缓存前会清理源文件失效、不再需要的缓存，并按
创建时间从旧到新进行 FIFO 淘汰。最近访问过的缓存会短暂保护，避免删除正在播放
的文件。

## 下载任务

首页右上角“下载模块”进入 `/downloads`。新增任务时输入下载目录和一行一个的
网页地址，系统会为每个 URL 创建一条 `download_tasks` 记录。

后台 `videos-download-worker` 每 5 秒扫描一次任务表，每次只执行一个任务：

1. 打开网页并下载视频。
2. 原地标准化为可直接播放的 MP4。
3. 写入视频库并生成元数据、缩略图。
4. 同步官方分类和演员。
5. 更新任务状态、进度、结果和错误原因。

下载管理页只在浏览器中对任务表、统计和分页做局部更新，不重新加载整个页面。新增
任务弹窗打开、表单正在提交或浏览器页面不可见时不会轮询；关闭未保存的表单会要求
用户确认。

下载清晰度默认使用“跟随站点策略”。`/downloads/site-policies` 的站点详情页可以
编辑视频选择策略，包括媒体来源优先级、页面视频身份校验、媒体类型优先级、清晰度
目标、清晰度回退、最低/最高分辨率和播放按钮选择器。用户配置优先于站点内置预设，
站点没有内置预设时使用 generic 策略。任务创建时保存策略快照，站点详情页可以对
指定网页执行不下载文件的媒体发现试运行。

任务失败会按 30 秒、2 分钟、10 分钟退避重试。服务重启时，运行中的任务会自动
恢复为待处理，达到最大尝试次数后标记失败。

下载中和待处理任务支持暂停：

```text
pending/running → pausing → paused → pending → running
```

暂停时向下载子进程发送 `SIGINT`，不会删除 `.part`。yt-dlp 使用自身的断点续传；
Browser HLS 使用同级 `.resume.json` 保存分段序号、文件大小和播放列表签名，恢复
时从下一个未完成分段继续。暂停不计为失败，服务重启不会自动恢复暂停任务，必须
在下载管理页点击“继续下载”。

macOS 上每个下载子进程会额外启动一个保活进程：

```bash
caffeinate -ims -w <下载进程PID>
```

它只阻止系统空闲睡眠和磁盘空闲休眠，不强制点亮显示器。下载完成、失败、暂停或
服务停止时下载子进程退出，保活进程也随之退出。合盖睡眠仍受 MacBook 硬件和电源
策略控制，下载大文件时建议连接电源。

## 悬浮预览

首页、分类视频、演员视频和收藏页的缩略图支持鼠标悬浮播放。预览文件保存在：

```text
local_video_site/data/previews/
```

后台 `video-preview-worker` 单线程生成缺失预览，默认 8 秒、最大宽度 640、
无音频、H.264/yuv420p。前端使用 `preload="none"`，鼠标停留 200 ms 后才加载，
一次只播放一个视频；移开、页面隐藏或卡片离开视口时立即停止。

### 下载停滞恢复

HLS 清单和分段 `fetch()` 均使用 AbortController。分段请求超时后自动重试。
Worker 同时维护 `last_progress_at`、`heartbeat_at` 和 `stall_count`：

```text
180 秒无实际进度
  → 中断当前子进程
  → 保存 .part 和 HLS resume.json
  → 状态重新设为 pending
  → 新进程刷新 CDN 签名后从断点继续
```

连续三次停滞会标记失败，避免无限自动恢复。

```bash
.venv/bin/python -m local_video_site previews --missing
.venv/bin/python -m local_video_site previews --status
.venv/bin/python -m local_video_site previews --retry-failed
```

服务默认只监听 `127.0.0.1`。不要直接绑定公网地址；局域网访问请使用
`./start_video_site.sh --lan`。该模式要求先创建有效管理员，并继续执行网络来源、
账号角色和视频访问级别校验。
