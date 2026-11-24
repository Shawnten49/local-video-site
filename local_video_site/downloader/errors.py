from __future__ import annotations


class PermanentDownloadError(RuntimeError):
    """A download failure that cannot succeed through automatic retries."""


_YOUKU_ACCOUNT_RESTRICTED_MARKERS = (
    "youku server reported error -6004",
    "该账号检测到有可疑违规",
    "优酷账号被限制播放",
)


def permanent_download_error_message(error) -> str:
    """Return an actionable message for known non-retryable failures."""

    message = str(error or "").strip()
    lowered = message.casefold()
    if any(
        marker.casefold() in lowered
        for marker in _YOUKU_ACCOUNT_RESTRICTED_MARKERS
    ):
        return (
            "优酷账号被限制播放（错误 -6004）。"
            "请先在下载专用浏览器中登录未被限制的优酷账号，"
            "确认该视频可以正常播放后再重试。"
            "该问题不是断点、网络或画质设置导致的。"
        )
    if "unsupported url:" in lowered:
        return (
            "下载器无法识别该媒体地址。"
            "系统不会再重复重试无效 URL，请检查站点是否更换了播放接口。"
        )
    return ""
