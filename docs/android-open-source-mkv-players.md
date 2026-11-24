# Android / Android TV 开源 MKV 播放器选型

调研日期：2026-11-19

## 结论

可以直接安装使用的开源播放器有 VLC、Just Player、mpv-android、Next Player、Kodi
和 Nova Video Player。它们对 MKV 的支持程度并不相同：

- 视频、音频和字幕的兼容性最稳的是 VLC，适合先在 TCL 电视上直接验证 MKV。
- Just Player 体积较小，Media3 FFmpeg 音频扩展包含 DTS、DTS-HD、TrueHD 等解码能力，
  适合做轻量级对照测试；但没有输入网络 URL 的界面。
- mpv-android 的软件视频解码和 libass 字幕能力较强，适合处理异常 MKV、ASS 字幕和多音轨。
- Next Player 有 Android TV 界面、网络 URL 播放和外部应用 Intent API，最适合作为当前客户端
  的“外部播放器回退”候选。
- Kodi 和 Nova 更接近媒体中心/媒体库，功能全面但接入当前视频网站的成本较高。

如果目标是“今天就能在电视上播放”，建议按以下顺序实测：

1. VLC：先确认 MKV 文件本身以及电视网络访问没有问题。
2. Next Player：确认能否通过 URL/Intent 直接播放当前视频站地址。
3. Just Player：检查轻量播放器和 FFmpeg 音频解码效果。
4. mpv-android：如果仍有 DTS、TrueHD、PGS/ASS 字幕或异常编码问题，再做最终兜底测试。

如果目标是“把高兼容 MKV 播放能力嵌入当前 APK”，优先评估 LibVLC；保留当前 Media3
播放器作为轻量、低延迟的默认路径。

## 可直接安装的播放器

| 播放器 | Android TV | MKV 相关能力 | 获取方式 | 许可证/注意点 |
|---|---|---|---|---|
| VLC for Android | 官方支持 | Matroska；DTS/TrueHD 等音频；丰富字幕格式；硬件解码和网络流 | VideoLAN 官方 APK、Google Play、F-Droid | 应用 GPLv2+；LibVLC 库 LGPLv2.1 |
| Just Player | 官方支持 | MKV；Media3 FFmpeg 音频扩展支持 AC3/EAC3/DTS/DTS-HD/TrueHD；ASS/SRT 等 | GitHub Releases、Google Play、F-Droid | 仓库标注 Unlicense；随包 FFmpeg 依赖仍需单独核对许可证 |
| mpv-android | 官方支持，APK 在 GitHub/F-Droid | 硬件/软件视频解码；libmpv；libass；多字幕；可打开网络 URL | GitHub Releases、F-Droid | 应用仓库标注 MIT；libmpv 和原生依赖有独立许可证 |
| Next Player | 官方提供 TV 版 | Media3 + FFmpeg 音频；MKV；网络 URL；外部播放 Intent API | GitHub Releases、Google Play、F-Droid | GPLv3.0；官方仍提示项目开发中 |
| Kodi | Android 平台可用 | 广泛容器/编码支持，网络播放和媒体中心能力 | Kodi 官方下载页、Google Play | GPL-2.0-or-later；应用较重，适合媒体中心而不适合只做一个播放按钮 |
| Nova Video Player | Android 家庭影音场景 | FFmpeg 核心；网络文件库；多媒体格式支持 | GitHub Releases、Google Play、F-Droid | Apache-2.0；原生 FFmpeg 等组件需核对附带许可证 |

### 1. VLC for Android

最适合作为第一选择。VideoLAN 官方下载页当前列出 Android 版本 3.7.0，并明确支持
Android TV；官方功能页明确列出 Matroska (MKV)、DTS、TrueHD 和多种字幕格式。

优势：

- 兼容范围最广，MKV、复杂音频、字幕、网络流的支持成熟。
- 有官方 APK 和商店版本，可直接侧载。
- 可以先独立于当前 APK 验证问题究竟来自 MKV 编码、电视解码器还是服务器取流。

不足：

- 作为外部播放器时，当前 APK 的选集、续播和下一集逻辑无法自然接管。
- 若要修改其界面，VLC 应用本身是 GPLv2+，分发修改版有开源义务。

### 2. Just Player

Just Player 官方 README 明确写明支持 Android TV、MKV、网络流以及 Media3 FFmpeg
音频扩展；其音频支持列表包含 AC-3、E-AC-3、DTS、DTS-HD 和 TrueHD。

优势：

- 比完整媒体中心轻量，播放控制直接。
- 对 MKV 中特殊音频编码的覆盖明显强于当前仅使用系统 MediaCodec 的播放器。
- GitHub、F-Droid 和 Google Play 都有发布渠道。

不足：

- 官方 README 明确说没有用于输入网络 URL 的 UI。
- 因此需要由浏览器、文件管理器或当前客户端通过 Android `ACTION_VIEW` Intent 调起。
- 它不是可导入的播放器 AAR，不适合直接嵌入当前 Activity。

### 3. mpv-android

mpv-android 官方 README 明确写明支持硬件和软件视频解码、libass 样式字幕、双字幕以及
“Open URL”网络流。Android TV 版本需要从 GitHub Releases、F-Droid 或手动安装 APK 获取。

优势：

- 软件解码路径适合处理电视系统解码器不支持的编码。
- libass 对 ASS/SSA 字幕的支持比普通 Media3 播放器更好。
- 可以手动打开网络 URL，便于直接验证服务器地址。

不足：

- 官方明确说明它不是一个可导入的 AAR/模块。
- 若集成到当前 APK，需要自行移植 `MPVLib`、视图层和 JNI 原生构建，工程量明显大于 Media3。

### 4. Next Player

Next Player 官方 README 提供 Android TV 截图和 TV 版本，支持网络 URL、FFmpeg 音频解码，
并公布了 MX Player 风格的外部播放器 Intent API。

它的 Intent API 支持：

- 视频 URL 和 MIME 类型。
- 标题、初始播放位置。
- 播放队列。
- 外挂字幕。
- 自定义 HTTP Header。
- 硬件解码或 FFmpeg 软件解码提示。
- 返回播放完成原因。

因此它适合当前客户端采用“内置 Media3 播放失败后，交给外部高兼容播放器”的方案。

风险：

- 官方 README 仍标注项目正在开发、可能存在 bug。
- 应用使用 GPLv3；如果复用其代码并分发当前 APK，需要满足 GPLv3 的相应义务。

### 5. Kodi 和 Nova

Kodi 的官方 README 将自身定位为家庭影院和媒体中心，支持 Android，并强调常见音视频格式
和网络播放。它适合希望统一浏览 SMB/WebDAV/媒体库的用户，但如果只是从当前网站点一个
视频，再启动大型媒体中心并不划算。

Nova Video Player 使用 FFmpeg 多媒体核心，适合本地/网络媒体库。它更接近“媒体库应用”，
不是提供单一播放组件的小型库。

## 嵌入当前 APK 的三种技术路线

### 路线 A：外部播放器回退，工作量最小

当当前 Media3 播放器报出：

- `ERROR_CODE_DECODING_FORMAT_UNSUPPORTED`
- `ERROR_CODE_DECODER_INIT_FAILED`
- `ERROR_CODE_PARSING_CONTAINER_UNSUPPORTED`
- 有视频但没有声音
- 只能播放但字幕异常

客户端显示“用兼容播放器打开”，通过 Android `ACTION_VIEW` 调起 VLC、mpv-android 或
Next Player。Next Player 的显式 Intent 还能携带标题、起始位置、播放队列和字幕。

优点：

- 不改现有播放器架构。
- 外部播放器独立升级，维护成本低。
- 不把大型原生库塞进当前 APK。

缺点：

- 播放体验离开当前应用。
- 返回、续播、下一集和自定义控制需要额外同步，或者直接交给外部播放器维护。

### 路线 B：嵌入 LibVLC，兼容性优先

VideoLAN 官方提供 Android `libvlc-all` AAR，并明确说明可用于为自己的 Android 应用提供
播放能力。其官方说明包含几乎所有媒体格式、硬件解码、HDR、DTS-HD/TrueHD 音频透传等能力。

适合：

- 必须保持在当前 APK 内播放。
- MKV 的音频、字幕和编码种类复杂。
- 可以接受 APK 体积增加和原生 ABI 管理。

注意：

- LibVLC 是 LGPLv2.1；动态链接和分发时仍需遵守 LGPL 要求。
- 当前播放器使用 Compose + Media3 `PlayerView`，切换 LibVLC 需要重新实现 Surface、
  播放器状态、拖动进度、字幕和生命周期处理。
- 建议只保留 `arm64-v8a`，或同时保留电视实际需要的 ABI，避免 APK 过大。

### 路线 C：Media3 + FFmpeg 扩展

Just Player 和 Next Player 都通过 Media3 的 FFmpeg 扩展增强了音频解码。这个路线与当前
Media3/ExoPlayer 架构最接近，但需要自行构建/打包 FFmpeg 解码扩展，并处理 GPL/LGPL
许可证、ABI 和软件解码性能。

适合：

- 主要问题是 DTS/TrueHD 等音频，而不是全部视频编码。
- 希望继续复用现有 Compose 控制层和 Media3 状态机。

不适合直接假定：

- 单纯把 `.mkv` 改名为 `.mp4` 就能解决兼容性。
- 添加 FFmpeg 扩展就一定能在 4K/10-bit/HDR 上获得实时性能。

## 对当前项目的建议

推荐保留两级方案：

```text
普通 MP4 / 电视支持较好的 MKV
  -> 当前 Media3 播放器，保持低延迟和当前 UI

Media3 解码失败或出现有画面无声音
  -> 通过 Intent 交给 Next Player 测试/播放

如果外部播放器成为长期依赖，且必须在当前界面内完成
  -> 再评估 LibVLC 替换 Media3
```

最省事的第一步不需要改代码：在 TCL 电视安装 VLC，然后直接打开同一个 MKV 播放地址。
如果 VLC 正常、当前 APK 不正常，问题就在当前 Media3/系统 MediaCodec 路径；如果 VLC
也无法播放，则应继续检查 MKV 容器、音频编码、HTTP Range 和服务器响应。

## 主要来源

- [VLC Android 官方下载页](https://www.videolan.org/vlc/download-android.html)
- [VLC 官方功能列表](https://www.videolan.org/vlc/features.html)
- [VLC for Android 官方源码](https://code.videolan.org/videolan/vlc-android)
- [libVLC 官方说明](https://www.videolan.org/vlc/libvlc.html)
- [Just Player 官方仓库](https://github.com/moneytoo/Player)
- [mpv-android 官方仓库](https://github.com/mpv-android/mpv-android)
- [mpv 官方手册](https://mpv.io/manual/stable/)
- [Next Player 官方仓库](https://github.com/anilbeesetti/nextplayer)
- [Next Player 官方 Intent API](https://github.com/anilbeesetti/nextplayer/blob/main/PLAYER_API.md)
- [Kodi 官方仓库](https://github.com/xbmc/xbmc)
- [Kodi 官方许可证](https://github.com/xbmc/xbmc/blob/master/LICENSE.md)
- [Nova Video Player 官方仓库](https://github.com/nova-video-player/aos-AVP)
