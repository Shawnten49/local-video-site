# 网页视频下载器

这是一个 Python 命令行工具。它使用 Playwright 通过 Chrome DevTools Protocol
连接到独立 Chrome，捕获网页播放产生的媒体请求，再把真实媒体 URL、页面 Cookie
和请求头交给 yt-dlp 下载。

完整安装、参数、日志和故障排查说明见
[使用说明.md](使用说明.md)。

## 环境安装

需要 Python 3.9+ 和 Google Chrome。

```bash
cd /path/to/local-video-site
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -r requirements.txt
```

## 基本用法

```bash
.venv/bin/python -m local_video_site.downloader.entrypoints.download_video "https://example.com/series/episode-1"
```

默认行为：

- 启动独立 Chrome：

  ```bash
  open -na "Google Chrome" --args \
    --remote-debugging-port=9222 \
    --user-data-dir="/path/to/local-video-site/.chrome-cdp-profile"
  ```

- 通过 `http://127.0.0.1:9222` 建立 CDP 连接。
- 自动打开首集页面、触发静音播放，并监听 m3u8、mpd、mp4、webm 等媒体请求。
- 对 DPlayer 等延迟创建的播放器执行周期性安全激活，兼容点击后才加载片源的问题。
- 识别 KVS/KT Player 的 `.fp-play`，并过滤 roomad、creative、广告 CDN 等媒体请求。
- 页面带视频 ID 时优先选择 URL 命中该 ID 的真实媒体，避免把预览广告当成主视频。
- 默认将小于 20 MiB 的视频候选和最终文件视为广告并忽略。
- 从首集页面标题中提取系列名，移除“第 N 集”、字幕标记、站点名和“高清”等噪声。
- 文件名包含 `#` 时，从第一个 `#` 开始截断，不保存后续标签内容。
- 保存到 `/path/to/local-video-site/download/<系列名>.<扩展名>`。
- 每次任务创建独立标签页，任务结束后自动关闭该标签页。
- 下载期间自动关闭播放器或广告点击新建的弹窗页面，不影响已有标签页。
- 下载前检查同名 MP4、TS、WebM 等完整文件；文件已存在时直接跳过下载。
- 同名旧文件小于 20 MiB 时会被认定为广告残留并移除，允许重新下载真实视频。
- 下载先进行 3 次断点续传；全部失败后清除当前任务的残留文件并全新下载一次。
- 检测到 AES 分片缓存损坏时，只清理当前视频的 `.part`/`.ytdl` 临时文件后重试。
- 进度写入终端；完整日志写入 `logs/download-时间.log`，汇总写入
  `logs/summary.log` 和 `logs/summary.jsonl`。

## 常见参数

```bash
# 手动指定系列名
.venv/bin/python -m local_video_site.downloader.entrypoints.download_video URL --series-name "系列名"

# 页面自动播放识别不到时，指定播放按钮
.venv/bin/python -m local_video_site.downloader.entrypoints.download_video URL --play-selector ".player .play-button"

# 已经通过浏览器开发者工具拿到媒体 URL
.venv/bin/python -m local_video_site.downloader.entrypoints.download_video URL --media-url "https://cdn.example/video.m3u8"

# 给媒体探测和下载分别增加重试
.venv/bin/python -m local_video_site.downloader.entrypoints.download_video URL \
  --extract-retries 3 \
  --download-attempts 4 \
  --retries 5 \
  --fragment-retries 10

# 内网代理或端口 9222 被另一个 CDP Chrome 占用时复用该实例
.venv/bin/python -m local_video_site.downloader.entrypoints.download_video URL --reuse-cdp

# 下载完成后关闭本次启动的 Chrome
.venv/bin/python -m local_video_site.downloader.entrypoints.download_video URL --close-browser

# 保持本次启动的 Chrome 打开，便于检查页面或手动登录
.venv/bin/python -m local_video_site.downloader.entrypoints.download_video URL --keep-browser-open
```

如果登录受限，可先在自动打开的 Chrome 中登录，再重新执行命令并使用
`--keep-browser-open --reuse-cdp`。

## 批量下载

将 URL 逐行写入 `urls.txt`，先启动 CDP Chrome，然后执行：

```bash
.venv/bin/python -m local_video_site.downloader.entrypoints.batch_download urls.txt
```

指定批量下载目录：

```bash
cd /path/to/local-video-site

.venv/bin/python -m local_video_site.downloader.entrypoints.batch_download \
  /path/to/local-video-site/urls.txt \
  --output "/path/to/local-video-site/download-other"
```

其中第一个参数是 URL 文件，`--output` 是视频输出目录。可以同时限制数量和
在首个失败时停止：

```bash
.venv/bin/python -m local_video_site.downloader.entrypoints.batch_download \
  /path/to/local-video-site/urls.txt \
  --output "/path/to/local-video-site/download-other" \
  --start-at 1 \
  --limit 10 \
  --stop-on-error
```

如果输出目录不是网站默认的 `download`、`download-full`、`download-hub` 或
`download-other`，需要通过 `LOCAL_VIDEO_ROOTS` 加入网站扫描范围：

```bash
LOCAL_VIDEO_ROOTS="/path/to/local-video-site/download-xmaster" \
./start_video_site.sh --restart
```

批处理会按文件顺序逐个调用
`local_video_site/downloader/entrypoints/download_video.py --reuse-cdp URL`，跳过空行、
注释和重复地址。每个任务完成后，会在下一个任务开始前随机等待 1–10 秒；
批处理同时输出关键进度、成功/失败数量、等待时长、批处理日志和 JSON 汇总。
只要存在失败，结束时就会逐条打印失败 URL、退出码和对应日志路径。

批量下载开始前会自动执行 `local_video_site/downloader/environment/enable_google.py`，确认 Google 环境可用后才
开始逐条下载；全部任务结束、失败停止或用户中断后，会执行
`local_video_site/downloader/environment/disable_google.py` 关闭环境。`--dry-run` 不会修改环境；如需跳过环境管理，
可使用 `--skip-environment-management`。

## URL 去重与切割

对 URL 文件按首次出现顺序去重，并默认每 15 条生成一个新 TXT：

```bash
.venv/bin/python -m local_video_site.downloader.entrypoints.split_urls urls.txt
```

输出为 `urls-1.txt`、`urls-2.txt` 等，原始 `urls.txt` 保持不变。默认不覆盖
已有输出，重新生成时可添加 `--force`。可使用 `--chunk-size N` 调整每个文件的
URL 数量。

## Google 网络环境开关

需要 Google 环境时：

```bash
.venv/bin/python -m local_video_site.downloader.environment.enable_google
```

仅检查 Chrome 是否可以访问 Google，不修改代理：

```bash
.venv/bin/python -m local_video_site.downloader.environment.enable_google --check-only
```

关闭 Shadowsocks 环境：

```bash
.venv/bin/python -m local_video_site.downloader.environment.disable_google
```

开启和关闭脚本都直接点击 ShadowsocksX-NG 菜单栏中的“打开 Shadowsocks”或
“关闭 Shadowsocks”。关闭脚本最后点击菜单中的“退出”结束应用。首次使用需要在
macOS“隐私与安全性”中授予 Terminal/Codex 辅助功能和自动化权限。

脚本会读取 `ShadowsocksOn` 验证菜单点击是否真正改变代理状态，再检查 Google。

## 本地视频库网站

启动本地视频浏览和播放网站：

```bash
./start_video_site.sh
```

该启动命令可以重复执行。如果站点已经在运行，会直接打开现有页面；需要加载最新
代码时使用 `./start_video_site.sh --restart`。端口被其他程序占用时，脚本会显示
占用进程，不会输出 Python 绑定错误。

同一 Wi-Fi 下使用手机访问：

```bash
./start_video_site.sh --lan
```

脚本会自动检测电脑的私有 Wi-Fi IP，并显示手机访问地址。未登录用户可以作为
游客浏览、播放和切换访问级别 `0` 的剧集；一般用户登录后可以收藏、浏览级别
`0`、`1` 的视频并管理自己的下载任务；视频管理、迁移和账号管理需要管理员登录。
局域网模式只允许回环地址、RFC1918 私有地址和链路本地地址，公网来源返回
`403`。使用 `--lan` 前必须至少存在一个有效管理员账号，并完成默认管理员改密。
该模式不会配置路由器端口转发或 UPnP。

空数据库首次启动会自动创建管理员：

```text
用户名：admin
初始密码：123456
```

登录后必须立即修改密码。完成修改前只能访问账号改密页面；局域网模式会拒绝
尚未修改默认密码的启动。共享部署建议在首次启动前通过 `.env` 覆盖
`LOCAL_VIDEO_BOOTSTRAP_ADMIN_USERNAME` 和
`LOCAL_VIDEO_BOOTSTRAP_ADMIN_PASSWORD`。

关闭自动初始化后，也可以在终端创建首个管理员：

```bash
LOCAL_VIDEO_AUTO_BOOTSTRAP_ADMIN=0 \
  .venv/bin/python -m local_video_site users bootstrap --username admin
```

查看本地账号：

```bash
.venv/bin/python -m local_video_site users list
```

关闭本地视频站：

```bash
./stop_video_site.sh
```

关闭命令只终止健康检查确认属于本项目的进程；如果端口被其他程序占用，会拒绝
关闭并显示占用信息。使用自定义端口时，启动和关闭命令都设置
`LOCAL_VIDEO_PORT`。

或直接运行：

```bash
.venv/bin/python -m local_video_site serve --open
```

默认地址为 `http://127.0.0.1:8766`。网站自动扫描 `download`、
`download-full`、`download-hub` 和 `download-other`，展示缩略图、名称、时长
和分辨率，并支持 HTTP Range 拖动播放。AV1、MKV、TS 等兼容性不稳定的文件会在
首次播放时按需重封装到播放缓存。

服务启动时会增量扫描视频目录，新视频会自动加入数据库，并在后台生成元数据、
缩略图和分类。未变化视频不会重复处理；应用数据目录和播放缓存会被排除。可通过
以下命令查看最近一次扫描状态：

```bash
.venv/bin/python -m local_video_site scan --status
```

首页“下载模块”旁提供“视频管理”入口。管理页支持按 ID、名称、路径、目录和
存储状态查询，支持分页、查看完整属性、删除视频，以及本地目录与外置盘之间的
双向迁移。迁移记录位于 `/manage/migrations`。

迁移任务在独立后台线程中串行执行。视频先复制到目标目录的临时文件，计算并校验
SHA-256，确认目标文件完整后才更新数据库并删除旧副本。缩略图、封面和悬浮预览
始终保留在本地数据目录，不参与迁移。

手动迁移可以选择一批本地视频迁往外置盘，也可以连接外置盘后将视频迁回本地。
自动迁移只筛选创建时间超过 30 天、位于本地且没有活动下载或重命名任务的文件。
外置盘使用隐藏存储标识文件识别；断开外置盘时视频记录和信息仍然保留，播放接口
会返回“外置存储未连接”，不会把记录永久标记为丢失。

`videos.original_url` 保存下载视频的原始网页地址。后续下载完成时会自动写入；
已有视频可从下载汇总中回填：

```bash
.venv/bin/python -m local_video_site backfill-urls --summary logs/summary.jsonl
```

### 数据库字段字典

SQLite 没有原生的列备注语法。有限取值字段通过 `schema_value_sets`、
`schema_value_set_items` 和 `schema_columns` 保存，并可通过
`schema_column_summary` 直接查询字段含义和全部允许值：

```bash
sqlite3 -header -column local_video_site/data/library.db \
  "SELECT table_name, column_name, default_value, allowed_values
   FROM schema_column_summary
   WHERE table_name = 'videos'
   ORDER BY column_name;"
```

例如 `videos.missing=0` 表示文件存在，`1` 表示最近扫描未找到文件。
严格字段由数据库触发器校验，写入未登记的值会直接失败；站点分类、审计动作等
开放代码允许后续扩展，并通过 `is_open=1` 标识。

服务日志同时输出到终端和按天文件：

```text
local_video_site/data/logs/server-YYYY-MM-DD.log
```

实时查看：

```bash
tail -f "$(ls -t /path/to/local-video-site/local_video_site/data/logs/server-*.log | head -1)"
```

默认保留 30 天。日志级别和保留天数可通过环境变量调整：

```bash
LOCAL_VIDEO_LOG_LEVEL=DEBUG
LOCAL_VIDEO_LOG_RETENTION_DAYS=30
```

首页 `LOCAL VIDEO` 右侧提供“分类”入口。分类由视频名称分词自动生成，分类页按
归属视频数量从多到少排列，支持搜索分类；点击分类可进入该分类的视频集合页。
扫描视频库时会自动重建分类，也可以单独执行：

```bash
.venv/bin/python -m local_video_site classify --rebuild
```

xHamster 视频会优先从原网页的官方分类标签提取分类，其他来源使用标题分词。
抓取结果会缓存；完整清空并重新获取：

```bash
.venv/bin/python -m local_video_site classify \
  --rebuild --clear --fetch-xhamster --workers 2
```

首页“分类”旁的“收藏”入口会展示已收藏视频。在视频详情页顶部点击“添加收藏”
即可加入收藏；已收藏的视频会显示“取消收藏”按钮。收藏页支持按最近收藏、名称、
时长和最近更新时间排序，并复用现有的下拉页码分页。

首页“演员”入口展示从 xHamster 页面提取的演员清单，按本地视频数量排序并支持
搜索。视频详情页底部会分别展示该视频的演员和普通分类。演员抓取与分类抓取复用
同一次 xHamster 页面请求，并同时保存演员外部 ID 和头像地址。

演员头像会下载到 `local_video_site/data/actor-avatars/`，演员清单和演员详情页
优先展示本地头像；没有头像的演员使用姓名首字母占位。首次初始化或补全头像：

```bash
.venv/bin/python -m local_video_site actor-avatars \
  --retry-failed \
  --report local_video_site/data/logs/actor-avatar-download.json
```

仅处理指定演员，或重新下载已有头像：

```bash
.venv/bin/python -m local_video_site actor-avatars --actor-id 932
.venv/bin/python -m local_video_site actor-avatars --refresh
```

播放时会优先直连原视频。分片 AV1 MP4 会在下载完成时自动进行一次无损原地
重封装；已有视频可批量预检和处理：

```bash
.venv/bin/python -m local_video_site playback-normalize --all --dry-run
.venv/bin/python -m local_video_site playback-normalize --all --workers 1
```

只有无法无损重封装的文件才使用播放缓存。默认缓存上限为 10 GiB，并按创建时间
进行 FIFO 清理：

```bash
.venv/bin/python -m local_video_site playback-cache --status
.venv/bin/python -m local_video_site playback-cache \
  --prune --max-size 10GiB --max-age-days 30
```

首页最右侧的“下载模块”进入下载管理页。可以一次输入多个 URL，每个 URL 创建
一条独立任务。后台 Worker 串行下载视频，完成后再生成缩略图、同步分类和演员。
任务列表支持关键词、状态、下载目录查询和分页，并显示进度、文件名、错误原因和
重试次数。

下载页通过 AJAX 局部更新任务行，不再自动整页刷新。打开“新增任务”弹窗或页面
处于后台时会暂停轮询；表单未保存就关闭时会二次确认，已输入内容不会被后台进度
刷新清空。

“下载清晰度”默认跟随站点策略。站点策略页可以分别配置传输方式、视频来源顺序、
页面视频身份校验、媒体类型顺序、目标清晰度和无对应档位时的回退方式。已知站点
使用内置预设，未配置站点自动使用 generic 兜底。任务创建时会保存当时的媒体策略
快照，因此后续修改站点策略不会改变已创建任务；站点详情页也可以在保存前对单个
网页试运行当前策略，查看候选和最终选择。

运行中或等待中的任务可以暂停，暂停后保留 `.part` 和 HLS 分段检查点；点击“继续
下载”会重新排队并从断点恢复。暂停不会增加失败次数，取消已暂停任务时会清理断点
文件。

相关环境变量：

```bash
LOCAL_VIDEO_DOWNLOAD_WORKER=1
LOCAL_VIDEO_DOWNLOAD_POLL_SECONDS=5
LOCAL_VIDEO_DOWNLOAD_TASK_ATTEMPTS=3
LOCAL_VIDEO_DOWNLOAD_CDP_PORT=9222
LOCAL_VIDEO_DOWNLOAD_TIMEOUT_SECONDS=21600
LOCAL_VIDEO_DOWNLOAD_KEEP_AWAKE=1
LOCAL_VIDEO_DOWNLOAD_KEEP_AWAKE_FLAGS=-ims
LOCAL_VIDEO_HLS_MANIFEST_TIMEOUT_SECONDS=20
LOCAL_VIDEO_HLS_SEGMENT_TIMEOUT_SECONDS=45
LOCAL_VIDEO_DOWNLOAD_STALL_TIMEOUT_SECONDS=180
```

视频管理页的“扫描外盘视频”用于原地解析外盘文件，不复制或迁移源视频。单个视频
解析后直接加入视频库；视频集模式会生成系列、季和集数关系；普通批量模式先生成
缩略图和预览供人工审核，确认后才写入视频表。扫描批次和普通批量明细均支持访问
级别 `0、1、2`，视频集内所有集数必须使用相同访问级别。

macOS 下载期间会自动启动 `caffeinate -ims -w <下载进程PID>`，防止系统因为
长时间空闲而睡眠。显示器仍可关闭；下载完成、失败或暂停后 `caffeinate` 自动退出。

视频卡片支持悬浮预览。后台会为可播放视频预生成约 8 秒、640×360、无音频的
H.264 MP4；单线程依次处理，避免同时启动大量 FFmpeg。鼠标停留约 200 ms 后才会
加载预览，同一时间只播放一个片段，移开后恢复缩略图。触摸设备不启用悬浮播放。

HLS 请求带有浏览器 AbortController 超时和分段重试。Worker 如果连续 180 秒没有
下载进度，会自动停止当前子进程、保存断点、刷新 CDN 会话并重新排队；连续三次
停滞才把任务标记失败。

```bash
.venv/bin/python -m local_video_site previews --missing
.venv/bin/python -m local_video_site previews --status
.venv/bin/python -m local_video_site previews --retry-failed
```

首页视频卡片右上角提供“更换封面”。可以在弹窗中播放或拖动视频，将当前时间点
截取为自定义封面，也可以恢复自动生成的封面。

视频播放页顶部提供“删除视频”。需要经过两步确认并输入删除校验码。系统不会
自动生成通用默认校验码；管理员可以在“账号管理 → 参数配置”中设置或更换，
页面只显示是否已配置，不提供明文读取。删除会清理视频文件、封面、预览和播放
缓存；视频数据库记录改为 `is_deleted=1` 软删除状态，收藏、分类、演员、下载
历史和迁移历史保留用于审计。忘记校验码时可在本机重置：

```bash
.venv/bin/python -m local_video_site delete-password
```

## 参数配置

管理员可以打开“账号管理 → 参数配置”，集中调整下载、扫描、媒体、存储、
任务线程和账号安全参数。配置优先级固定为：

```text
环境变量 > 数据库覆盖值 > 代码默认值
```

已被环境变量设置的项目在网页中只读。数据库、资源目录、局域网模式等启动级
参数也只读展示，修改后需要调整启动命令或环境变量并重启服务。删除校验码属于
敏感参数，页面只显示“已配置/未配置”，不返回明文或哈希；忘记当前校验码时，
应回到本机执行上面的 `delete-password` 命令。

## 工作机制

1. `chrome.py` 使用独立用户目录启动 Chrome，并等待 `/json/version` 就绪。
2. `sniffer.py` 在页面跳转前注册 CDP 网络响应观察器，同时扫描
   `<video>`、`<source>` 和 Performance Resource API。
3. `naming.py` 选择可信的页面标题候选并清理成文件名。
4. 对于可跨域读取的 HLS，`browser_hls.py` 按分辨率优先选择页面提供的最高
   画质，可识别 250p、360p、480p、720p、1080p 及更高变体；如果只观察到低画质子清单，
   会回溯主清单重新选择，再通过页面自身的 Chrome 网络逐分片下载并合并。
   HLS 默认并发 2，连续无错误后最多提升到 3；每批处理 6 个分段，单段和批次
   各重试 2 次。连续限流会指数冷却，5 分钟内第三次限流会保留断点并自动暂停。
   断点每 2 个批次或 16 MiB 保存一次，异常退出时立即保存。
   当同时存在高于 1080p 的画质时，优先选择最高不超过 1080p 的变体。
   低于 360p 的变体会被忽略；没有 360p 及以上画质时任务直接失败。
   对于页面已选定的单一直接 MP4，yt-dlp 使用 `best`，避免因直链缺少
   `height` 元数据而被 360p 过滤器误排除。
5. `downloader.py` 将有签名的媒体 URL、Cookie、Referer、User-Agent 交给
   yt-dlp，并在浏览器 HLS 路径不可用时回退到原始网页；yt-dlp 会自动使用
   环境代理或本地 1087/1088 HTTP 代理。AES-128 HLS 通过
   `pycryptodomex` 加速解密；默认使用 Chrome TLS 指纹访问限制普通 HTTP
   客户端的媒体 CDN。完成文件、总耗时和错误会写入汇总。

## 已知限制

- Widevine/FairPlay 等 DRM 加密内容不能被本工具解密，也不能绕过登录、付费或
  版权访问控制。仅应下载自己有权保存的内容。
- 页面只暴露 `blob:` 地址且没有可观察到的清单请求时，普通网络嗅探无法得到媒体
  源；优先尝试页面内的播放器请求，必要时使用 `--allow-page-fallback`。
- 多个视频请求可能包含广告或预览流。工具会优先选择 HLS、DASH 和 MP4，并逐条
  打印候选地址；自动选择不正确时使用 `--media-url` 明确指定。
