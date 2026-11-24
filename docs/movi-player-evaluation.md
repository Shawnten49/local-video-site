# movi-player 可行性评估

评估日期：2026-11-19

## 结论

`movi-player` 在技术路径上适合当前项目的核心问题：

- 使用 FFmpeg/libavformat WASM 解封装 MKV。
- 视频优先使用 WebCodecs 解码，必要时使用 WASM 软件解码。
- 音频通过 FFmpeg WASM 解码；发布的 `movi.wasm` 包含 AC-3/E-AC-3 解码器。
- 因而不必先把 MKV 转成 MP4，也不必先把 AC3 音轨转成 AAC。

不建议立即全站替换现有播放器。更合适的做法是：

1. 固定并自托管 `movi-player@0.4.1` 的 slim 构建。
2. 通过功能开关或按编码条件，只对原生 `<video>` 无法处理的 MKV、HEVC、AC3 等视频启用。
3. 保留原生 MP4/H.264/AAC 播放路径和服务端音频兼容修复作为回退。
4. 先在视频 #796 和 #1024 上验证桌面 Chrome、Safari、手机及电视浏览器。

如果目标播放设备都满足浏览器要求，这个方案可以替代“网页为了播放而转视频/音频”的大部分工作。若还要支持旧电视浏览器，仍需保留服务端转码或音频替换方案。

## 已核验的实现机制

### MKV 与 HEVC

项目说明其播放管线为：

- FFmpeg `libavformat` 编译为 WASM，负责 MKV/MP4/TS/AVI 等容器解析。
- 视频优先调用 WebCodecs `VideoDecoder`。
- WebCodecs 不可用或不支持某个配置时，进入 WASM 软件视频解码。
- HEVC、AV1 等格式有专门的 codec string、关键帧和回退处理。

来源：

- <https://github.com/mrujjwalg/movi-player/blob/main/AGENTS.md>
- <https://github.com/mrujjwalg/movi-player/blob/main/README.md>

### AC3/E-AC3

源码中的音频解码器实现会强制走软件 WASM 音频路径，并支持多声道音频。下载并检查 npm `movi-player@0.4.1` 包后，`dist/wasm/external/movi.wasm` 中包含：

```text
(E-)AC3 decoder
libavcodec/ac3dec.c
```

因此它确实具备浏览器端解码 AC3/E-AC3 的能力，不是仅把 MKV 换个容器，也不是依赖操作系统原生 AC3 解码器。

来源：

- <https://www.npmjs.com/package/movi-player>
- npm 包：<https://registry.npmjs.org/movi-player/-/movi-player-0.4.1.tgz>
- 源码：<https://github.com/mrujjwalg/movi-player>

## 版本与维护状态

核验时 npm 最新版本为 `0.4.1`，发布于 2026-10-02。

- 许可：Apache-2.0。
- GitHub 仓库：约 108 stars、27 forks、12 个开放 issue。
- 仓库创建于 2026-02，仍属于较新的项目。
- 最近可见源码提交集中在 2026-10，社区规模相对 Video.js、Shaka Player 等成熟项目仍较小。

来源：

- <https://api.github.com/repos/mrujjwalg/movi-player>
- <https://api.github.com/repos/mrujjwalg/movi-player/releases/latest>
- <https://github.com/mrujjwalg/movi-player/issues>

这不表示项目不可用，但意味着升级回归、问题定位和长期维护需要项目自己承担一部分风险。

## 体积与运行成本

0.4.1 npm 包解压约 150 MB。

运行时有两种主要构建：

| 构建 | 本地 JS | WASM | 说明 |
|---|---:|---:|---|
| 默认 `element.js` | 约 12 MB | 嵌入 JS | 一个脚本即可用，但首次下载和解析较重 |
| `element/slim` | 约 4.7 MB | 独立约 5.4 MB | 更适合自托管、缓存和 CDN 压缩 |

README 标注的总传输体积约为 1.8–3.2 MB，具体取决于压缩方式。播放器内部默认 LRU 源数据缓存上限为 520 MB，可通过 `buffersize` 调低；在手机和电视上必须主动限制内存。

来源：

- npm `movi-player@0.4.1/package.json`
- <https://github.com/mrujjwalg/movi-player/blob/main/README.md>
- <https://github.com/mrujjwalg/movi-player/blob/main/AGENTS.md>

## 浏览器与服务器要求

官方支持矩阵：

- Chrome 110+
- Edge 110+
- Safari 18+
- Firefox 130+

服务器要求：

- 支持 HTTP Range 请求，用于 Seek。
- 跨域时需要 CORS。
- COOP/COEP 可选；不开启也能播放，只是少了 SharedArrayBuffer 快速路径。

当前项目 `/media/{id}` 和 `/playback/{id}` 已使用支持 Range 的 `FileResponse` 并返回 `Accept-Ranges: bytes`，同源播放不需要额外 CORS。

官方 LG webOS issue 是目前最明确的风险案例：2022 年 webOS 22 使用 Chromium 87，无 WebCodecs，实测 MKV 元数据加载超时。因此 Android TV、LG/Samsung 电视 WebView 不能假设可用，必须逐台验证。

来源：

- <https://github.com/mrujjwalg/movi-player/issues/18>
- 当前项目 `local_video_site/web/routes/playback.py` 的 `media()` /
  `_file_video_response()`

## 对当前项目的影响

### 优点

- 直接面向 `/media/{id}` 播放 MKV，不需要生成 playback-cache。
- 可保留原始 HEVC 视频和 AC3/E-AC3 音频，不产生服务端转码损耗。
- 支持多音轨、字幕、Seek、Range、画质轨道和自定义请求参数。
- 自定义元素暴露部分 `HTMLMediaElement` API，现有播放位置保存、播放/暂停和键盘控制可部分复用。
- 可以按功能检测只对原生播放失败的视频启用，不影响兼容 MP4 的直接播放。

### 风险

- 首次加载比原生 `<video>` 重，WASM 编译与 JS 解析会造成冷启动延迟。
- HEVC 没有硬件 WebCodecs 时会转软件解码，1080p 尚需实测，4K 在手机/电视上可能无法实时。
- 默认缓存上限较大，低内存设备可能明显增加内存占用。
- 项目年轻、维护者和社区规模有限，升级需要回归测试。
- 现有站点的播放状态、断网提示、权限、续播、上一集/下一集和自定义键盘逻辑需要适配与回归。
- 不应直接引用未锁版本的公共 CDN，生产环境必须固定版本并自托管，避免供应链和 CDN 不可用风险。

## 推荐接入方式

1. 固定 `movi-player@0.4.1`，使用 `element/slim`，把 JS 和 `movi.wasm` 放进当前站点的静态资源目录。
2. 增加播放器功能开关，默认关闭全量替换。
3. 原生浏览器能力检测失败，或媒体为 MKV/AC3/E-AC3 时，再挂载 `<movi-player>`。
4. 同源使用 `/media/{video_id}`；保留现有 `playback-state` 对文件在线、外盘离线、权限和删除状态的判断。
5. 设置较小的 `buffersize`，手机端建议从 64–128 MB 开始压测。
6. 保留原生 `<video>` 和服务端音频兼容修复，作为旧浏览器或加载失败时的回退。
7. 至少实测：
   - 视频 #796：MP4 + HEVC + AC3 5.1。
   - 视频 #1024：MKV + HEVC + AC3 5.1。
   - 首次打开耗时。
   - 暂停、拖动、续播、倍速。
   - 手机 Chrome/Safari。
   - Android TV 和现有 LG/其他电视浏览器。

## 最终意见

**方案可行，且技术上很契合当前问题；值得做受控试点，但不建议直接作为全站唯一播放器上线。**

在当前桌面浏览器环境中，它很可能同时解决 MKV、HEVC 和 AC3 无声问题。若试点通过，可以作为“高兼容播放器”路径；服务端音频转 AAC 仍然应保留，用于旧电视、旧浏览器以及 WASM 加载失败时。最稳妥的最终架构是：

```text
原生可播放 MP4/H.264/AAC
→ 原生 <video>

原生不支持，但浏览器满足 WebCodecs 要求
→ movi-player

旧浏览器、电视或 movi-loader 失败
→ 服务端 AAC 音频兼容文件 / H.264 转码回退
```
