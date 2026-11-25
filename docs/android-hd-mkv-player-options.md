# Android / Android TV 高清 MKV 播放方案调研

## 目标视频特征

当前故障文件的实际参数：

```text
封装：Matroska
视频：HEVC Main 10
分辨率：3840×2160
像素：yuv420p10le
HDR：BT.2020 / SMPTE ST 2084 (PQ)
Dolby Vision：Profile 7，BL + EL + RPU
音频：TrueHD 7.1 / Atmos
```

这不是普通 H.264 MKV。播放方案必须区分：

1. 能播放视频画面。
2. 正确输出 HDR10。
3. 完整支持 Dolby Vision Profile 7 动态元数据和增强层。
4. 正确解码、下混或直通 TrueHD/Atmos。

Android TV 设备通常可以处理 HEVC Main10，但不能假定支持 DoVi Profile 7
双层文件和 TrueHD。

## 当前客户端情况

当前 `android-tv-native` 使用：

```text
AndroidX Media3 / ExoPlayer 1.4.1
```

Media3 可以处理 Matroska 容器，但实际视频解码器由 Android
`MediaCodec` 提供。设备没有对应硬解码能力时，当前客户端没有内置软件视频
解码器或 FFmpeg 解码扩展。

此外，当前 TV API 返回的 `mime_type` 固定为 `video/mp4`，并且原有播放准备
流程会把需要浏览器兼容性的 MKV 转成 MP4。这与“TV 原生直接播放 MKV”的
目标冲突。

## 可选方案

### 1. AndroidX Media3 / ExoPlayer

优点：

- 当前已经集成，UI、遥控器、剧集切换和进度记录无需重写。
- Apache-2.0 许可证，集成成本最低。
- Matroska 容器支持由 Media3 提供。
- 4K HEVC 是否流畅取决于电视的硬件解码能力。

局限：

- H.265/10-bit、HDR、DoVi 和音频支持都依赖设备 `MediaCodec`。
- TrueHD 支持不是所有 Android TV 设备都具备。
- 默认 APK 不包含完整的视频软件解码能力。
- DoVi Profile 7 的 EL/RPU 通常不能保证被 Android 硬解码器完整处理。

适用定位：

```text
首选后端，用于设备原生支持的格式。
```

官方资料：

- [Media3 supported formats](https://developer.android.com/media/media3/exoplayer/supported-formats)
- [AndroidX Media source](https://github.com/androidx/media)
- [Media3 FFmpeg decoder extension](https://github.com/androidx/media/tree/release/libraries/decoder_ffmpeg)

### 2. Media3 FFmpeg 解码扩展

优点：

- 可以补充 MediaCodec 不支持的视频或音频解码。
- 可保留现有 Media3 播放器界面和剧集逻辑。

局限：

- 需要自行编译并提供 Android native 库，集成和发布复杂度高。
- 4K HEVC 软件视频解码对电视 CPU 压力很大。
- 软件解码不能自动获得 DoVi Profile 7 和 HDR 的正确显示链路。
- FFmpeg 编译选项会影响 LGPL/GPL 许可证义务。

适用定位：

```text
补充特定音频或视频编解码，不作为 4K MKV 的默认解码方案。
```

### 3. LibVLC / VLC for Android

优点：

- 对 Matroska、HEVC、AC3、EAC3、TrueHD 等格式和音频类型的覆盖范围广。
- 支持硬件解码、字幕、音轨切换和网络流。
- 可以直接使用 HTTP Range 播放服务器上的原始 MKV。
- Android/TV 使用成熟。

局限：

- 需要嵌入 native 库，APK 体积增加。
- Compose 控制层需要通过播放器适配器接入 LibVLC Surface，不能直接复用
  Media3 `PlayerView`。
- HDR/DoVi 最终仍受电视显示能力、HDMI 输出和音频直通能力限制。
- DoVi Profile 7 是否完整保留必须实测；某些设备只能播放 HDR10 基础层。
- 需要处理 LibVLC 的 LGPL 动态链接和许可证声明要求。

适用定位：

```text
推荐作为原生 MKV 的首选兼容播放器后端。
```

官方资料：

- [VLC Android source](https://github.com/videolan/vlc-android)
- [LibVLC Android source](https://code.videolan.org/videolan/libvlcjni)
- [VLC license](https://github.com/videolan/vlc/blob/master/COPYING)

### 4. libmpv / mpv-android

优点：

- FFmpeg 解码能力完整，格式兼容性很强。
- 字幕、音轨、HDR tone mapping、播放缓冲和精确 seek 能力较强。
- 对 AC3/EAC3/TrueHD 等音频下混能力较好。

局限：

- native 库体积、Compose 接入和遥控器控制改造量高于 LibVLC。
- GPL 许可证对应用分发有更强约束。
- 4K HEVC 如果回退到软件解码，电视 CPU 可能无法流畅播放。
- Android 硬件解码和 HDR 输出仍受 SoC、系统 Surface 和显示设备限制。

适用定位：

```text
高级兼容模式或单独的 full APK flavor，不作为第一版默认播放器。
```

官方资料：

- [mpv source](https://github.com/mpv-player/mpv)
- [mpv manual](https://mpv.io/manual/stable/)
- [mpv-android](https://github.com/mpv-android/mpv-android)

### 5. Kodi

优点：

- 最成熟的媒体中心之一，TV 遥控器体验完善。
- 格式、字幕、音轨和硬件解码支持广。

局限：

- Kodi 是独立应用，不是可以简单嵌入当前 APK 的播放器插件。
- 引入完整 Kodi 会形成第二套媒体库、播放进度和账号/API 体系。
- GPL 许可证和工程体积不适合当前轻量客户端。

适用定位：

```text
可作为“外部播放器”兼容选项，而不是当前 APK 的内嵌组件。
```

官方资料：

- [Kodi source](https://github.com/xbmc/xbmc)

## 推荐架构

### 服务端增加原始流模式

新增 TV 原生播放接口，例如：

```text
GET /api/tv/v1/videos/{id}/original
```

要求：

- 直接返回原始 MKV，不经过 playback-cache。
- 正确返回 `video/x-matroska`。
- 支持 HTTP Range 和 `206 Partial Content`。
- 返回视频、音频、HDR、DoVi 和容器元数据。
- 继续执行访问级别和账号权限检查。
- 不能因为客户端支持 MKV 就绕过视频可见性规则。

建议元数据：

```json
{
  "container": "matroska",
  "mime_type": "video/x-matroska",
  "video_codec": "hevc",
  "pixel_format": "yuv420p10le",
  "width": 3840,
  "height": 2160,
  "hdr_format": "dolby_vision",
  "dolby_vision_profile": 7,
  "color_transfer": "smpte2084",
  "audio_codecs": ["truehd"],
  "audio_channels": 8
}
```

### 客户端播放器后端抽象

不要把所有播放逻辑直接绑定到 Media3 的 `PlayerView`。建议抽出：

```kotlin
interface VideoPlayerBackend {
    fun attach(context, surface)
    fun prepare(uri, startPositionMs)
    fun play()
    fun pause()
    fun seekTo(positionMs)
    fun selectAudioTrack(id)
    fun selectSubtitle(id)
    fun release()
}
```

实现后端：

```text
Media3Backend
LibVlcBackend
MpvBackend
```

Compose 播放控制层只依赖 `VideoPlayerBackend`，这样可以保留现有的顶部信息、
播放/暂停、10 秒 seek、音轨、字幕和下一集功能。

### 自动选择规则

```text
MP4 / H.264 / AAC
    → Media3

MKV + HEVC Main10 + 设备支持 HEVC
    → 优先 Media3 原生播放

MKV + TrueHD/AC3/EAC3 但 Media3 不支持
    → LibVLC

DoVi Profile 7 / HDR 元数据复杂 / Media3 失败
    → LibVLC

LibVLC 仍失败
    → 外部播放器或服务器兼容转码
```

不应仅根据扩展名判断。Android 端需要检测：

- `video/hevc` 是否支持。
- 设备是否声明支持 10-bit HEVC 和对应 profile/level。
- 设备是否支持 HDR 输出。
- 是否支持 Dolby Vision MIME/profile。
- 是否支持 TrueHD、AC3、EAC3 passthrough 或软件解码。

## 推荐实施顺序

### 阶段 1：外部兼容播放

当 Media3 无法播放时，显示：

```text
该视频需要兼容播放器
[使用 VLC 打开] [使用系统播放器打开]
```

通过 Android `Intent.ACTION_VIEW` 打开原始流。这样不增加 APK 体积，也能快速
验证电视硬件和已安装播放器能否处理目标 MKV。

### 阶段 2：嵌入式 LibVLC 后端

这是推荐的长期方案：

- Media3 作为主播放器。
- LibVLC 作为高清 MKV 和复杂音频回退。
- 保留现有 Compose 控件和下一集功能。
- 提供 `standard` 和 `full` 两种 APK flavor：

```text
standard：Media3 + 外部播放器
full：Media3 + LibVLC
```

### 阶段 3：可选 mpv flavor

只有在 LibVLC 对 DoVi/HDR/字幕仍不满足时，再增加独立的 mpv flavor。
不要默认将 mpv 与主 APK 合并，避免体积和 GPL 许可证影响普通安装包。

### 阶段 4：服务器兼容转码

对于电视完全不支持的视频，保留服务器转码作为最后兜底：

```text
1080p H.264 + AAC MP4
```

这条路径保证任何设备都能播放，但耗时最长。

## 验收矩阵

至少测试以下组合：

| 视频 | 视频编码 | 音频 | 目标 |
|---|---|---|---|
| 普通 MP4 | H.264 | AAC | Media3 直接播放 |
| 普通 MKV | H.264 | AAC/AC3 | Media3 或 LibVLC |
| 4K MKV SDR | HEVC Main10 | AC3 | 优先硬件 HEVC |
| 4K MKV HDR10 | HEVC Main10 | EAC3 | 正确 HDR 输出或 tone mapping |
| 4K DoVi P7 MKV | HEVC Main10 + DoVi | TrueHD 7.1 | 记录是否完整 DV、HDR10 回退或失败 |
| 1080p 兼容 MP4 | H.264 | AAC | 服务器转码兜底 |

每个测试记录：

- 实际使用的后端和编码器。
- 硬件解码是否启用。
- 是否出现掉帧。
- 是否正确识别 HDR/DoVi。
- 是否能切换音轨和字幕。
- 拖动到 25%、50%、90% 时是否可播放。
- TrueHD 是直通、下混还是被忽略。
- 音频和画面是否同步。

## 最终建议

直接播放目标 MKV在技术上有多种可行路线，但“任意 Android TV 都能完整支持
DoVi Profile 7 + TrueHD Atmos”不现实。

推荐目标：

1. 保留 Media3 作为主播放器。
2. 增加原始 MKV 直链和支持 Range 的 TV 原生流接口。
3. 先用外部 VLC 验证目标电视是否具备硬件能力。
4. 长期增加嵌入式 LibVLC 作为自动回退。
5. 保留服务器 1080p 转码作为最终兼容方案。
6. 将 DoVi Profile 7 动态层和 TrueHD Atmos 的“完整支持”作为独立验收项目，
   不能仅以“能显示画面”视为通过。
