# Local Video TV APK

> 该 WebView 客户端已废弃，仅保留作历史参考。新部署请使用
> `android-tv-native`。

Android TV WebView 客户端，默认访问：

```text
http://YOUR_LAN_IP:8766/
```

## 构建

```bash
cd /path/to/local-video-site/android-tv-shell
./gradlew assembleRelease \
  -PLOCAL_VIDEO_SERVER_URL=http://YOUR_LAN_IP:8766/
```

APK 输出：

```text
app/build/outputs/apk/release/app-release.apk
```

## 安装到 TCL 电视

优先使用 TCL 自带的“多屏互动”：

1. 电视首页打开“多屏互动”。
2. 手机扫码安装同一版本的手机端。
3. 手机和电视连接同一局域网。
4. 打开手机端“工具”→“应用推送”。
5. 选择 `app-release.apk` 并推送到电视。
6. 电视端允许接收和安装。

如果电视允许 ADB 安装，也可以执行：

```bash
adb connect 电视IP:5555
adb install -r app/build/outputs/apk/release/app-release.apk
```

## 使用

- 电视遥控器使用方向键移动焦点，OK 选择。
- 搜索框不会抢占首页焦点；先选中“搜索”按钮才会进入文字输入。
- 在输入框中按返回键会退出输入并恢复遥控器导航。
- 播放页方向键左右快退/快进 5 秒。
- 播放页提供“-10 秒”“播放/暂停”“+10 秒”电视按钮。
- 页面右下角“设置”或遥控器菜单键可以修改服务器地址。
- 登录 Cookie 和播放进度保存在电视本地。

视频服务器应使用局域网模式：

```bash
cd /path/to/local-video-site
./start_video_site.sh --lan
```
