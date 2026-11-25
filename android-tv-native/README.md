# Local Video TV Native

自适应 Android 原生客户端，使用 Kotlin、Jetpack Compose 和 Media3 ExoPlayer。
同一个 APK 同时支持手机触摸操作和 Android TV 遥控器操作。

第一版只包含：

- 游客免登录访问
- 全部视频列表
- 视频详情和剧集列表
- 原生视频播放
- 文本字幕与外挂 VTT 字幕
- MKV 默认使用 VLC 外部播放，不再生成播放缓存
- 本地记忆播放进度
- 服务器地址设置

分类和演员浏览将在下一版本加入。当前版本不包含搜索、登录、收藏、下载或管理功能。

## 外部 VLC 播放

对于 Media3 无法直接播放的高清 MKV，详情页和播放失败页会提供
“用 VLC 播放原片”。现在所有 MKV 点击播放都会优先直接打开 VLC，
不再请求服务端生成 playback-cache。该模式直接打开服务器原始文件，
不会同步应用内播放进度和自动下一集。

需要在电视上安装 VLC for Android：

```text
包名：org.videolan.vlc
```

未安装 VLC 时可以选择其他支持 `video/x-matroska` 的播放器。外部播放器不会
同步当前客户端的播放进度或下一集状态。

## 默认服务器

```text
http://YOUR_LAN_IP:8766/
```

客户端只读取服务端的 `/api/tv/v1/*` 游客只读接口。服务端仍强制只返回
`access_level = 0` 的视频。

## 构建

```bash
cd /path/to/local-video-site/android-tv-native
./gradlew assembleRelease \
  -PLOCAL_VIDEO_SERVER_URL=http://YOUR_LAN_IP:8766/
```

APK 输出：

```text
app/build/outputs/apk/release/app-release.apk
```

## 安装

通过 TCL “多屏互动 → 工具 → 应用推送”选择 APK，或在开启 ADB 后执行：

```bash
adb connect 电视IP:5555
adb install -r app/build/outputs/apk/release/app-release.apk
```

应用与旧 WebView 版使用相同包名和签名，可直接覆盖安装。

## 遥控器操作

- 首页方向键移动视频焦点，OK 打开详情。
- 详情页 OK 播放，方向键切换剧集。
- 播放器左右键快退/快进 10 秒。
- 播放器 OK 播放/暂停。
- 播放器返回键退出播放。
- 默认自动选择中英双语字幕；没有双语时选择中文字幕。
- 设置页由左侧导航主动进入，其他页面不会自动聚焦输入框。

## 手机和平板

- 手机竖屏使用顶部工具栏和两列视频网格。
- 手机横屏自动增加视频列数。
- 详情页可以触摸滚动，并提供明确的返回按钮。
- 详情页使用单一清晰海报和深色渐变背景，不重复铺满同一封面。
- 手机详情页海报顶格全宽展示，返回按钮悬浮在安全区左上角；横屏和电视在单张 Hero 海报上叠加标题与播放入口。
- 播放器点击画面可显示控制层，包含返回、中央播放/暂停、可拖动时间轴和下一集入口。
- 双击画面左右侧可快退/快进 10 秒，电视端继续使用方向键和 OK 键。
- 当前时间和总时长位于进度条上方；播放器不再长期展示操作提示文字。
- 播放器运行在独立 PlayerActivity 中，避免视频 Surface 与详情页共用窗口层级。
- 自动播放开始时不会显示中央大按钮；用户操作后中央按钮约 0.6 秒开始淡出。
- 顶部和底部完整控制层约 3.5 秒后淡出，暂停状态同样适用。
- 拖动时间轴、播放结束、播放错误期间不会自动隐藏控制层。
- 手机竖屏顶部栏分两行显示返回/状态和完整标题，并避开屏幕安全区。
- “下一集”不再放在顶部；片尾 30 秒显示独立提示卡，结束后显示重播/下一集面板。
- 手机正常显示系统状态栏，只在播放器内进入沉浸模式。
- 屏幕常亮只在播放期间生效，退出播放器后恢复系统栏。
- 设置页不会自动弹出键盘；窄屏下主按钮全宽，次级按钮等宽排列。
- 播放器使用 TextureView，退出时先停止播放并清除视频 Surface，再返回详情页。

首页的手机品牌栏、全部视频标题、统计信息和视频卡片位于同一个滚动容器中；
向下浏览时标题会同步移出屏幕。电视端左侧导航固定，右侧内容统一滚动。

debug 构建可以在手机上强制验证电视布局：

```bash
adb shell am start -n com.shawn.localvideotv/.MainActivity \
  --ez force_tv_ui true
```

该参数只在 debug APK 中生效，release APK 会按真实设备类型选择布局。

## 服务端

先启动局域网模式：

```bash
cd /path/to/local-video-site
./start_video_site.sh --lan
```

服务端健康检查：

```text
GET /api/tv/v1/ping
```
