from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple


@dataclass(frozen=True)
class ValueTerm:
    value: str
    label: str
    description: str
    sort_order: int = 0
    is_default: bool = False
    deprecated_at: Optional[str] = None


@dataclass(frozen=True)
class ValueSet:
    key: str
    label: str
    description: str
    values: Tuple[ValueTerm, ...]
    is_open: bool = False
    is_strict: bool = False


@dataclass(frozen=True)
class ColumnVocabulary:
    table_name: str
    column_name: str
    set_key: str
    sql_type: str
    nullable: bool
    null_meaning: str
    default_value: Optional[str]
    description: str
    enforcement: str = "trigger"


def _terms(*items: Tuple[str, str, str]) -> Tuple[ValueTerm, ...]:
    return tuple(
        ValueTerm(value, label, description, index, index == 0)
        for index, (value, label, description) in enumerate(items)
    )


VALUE_SETS: Tuple[ValueSet, ...] = (
    ValueSet(
        "boolean",
        "布尔值",
        "数据库中统一使用 0 和 1，不使用 true/false 字符串。",
        _terms(
            ("0", "否", "条件不成立或功能未启用。"),
            ("1", "是", "条件成立或功能已启用。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "access_level",
        "访问级别",
        "视频、下载和扫描数据的可见范围。",
        _terms(
            ("0", "所有人", "游客、登录用户和管理员均可访问。"),
            ("1", "登录用户", "登录用户和管理员可访问。"),
            ("2", "管理员", "仅管理员可访问。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "user_role",
        "账号角色",
        "账号的权限角色。",
        _terms(
            ("user", "普通用户", "使用收藏和个人下载权限。"),
            ("admin", "管理员", "拥有全部管理权限。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "session_revoke_reason",
        "会话撤销原因",
        "登录会话被归为失效的原因。",
        _terms(
            ("logout", "主动退出", "用户主动退出登录。"),
            ("password_changed", "密码变更", "账号密码变更后撤销其他会话。"),
            ("account_disabled", "账号停用", "账号被停用后撤销全部会话。"),
        ),
        is_open=True,
    ),
    ValueSet(
        "login_failure_code",
        "登录失败原因",
        "登录失败审计码。",
        _terms(
            ("invalid_credentials", "凭据无效", "用户名或密码错误。"),
        ),
        is_open=True,
    ),
    ValueSet(
        "audit_result",
        "审计结果",
        "账号审计操作的最终结果。",
        _terms(
            ("success", "成功", "操作已完成。"),
            ("denied", "拒绝", "权限或来源校验拒绝操作。"),
            ("failed", "失败", "操作执行过程中失败。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "open_code",
        "开放代码",
        "允许插件、站点或新功能扩展的字符串代码。",
        (),
        is_open=True,
    ),
    ValueSet(
        "media_origin",
        "视频媒体来源",
        "视频记录首次进入资料库的来源。",
        _terms(
            ("download", "下载", "由下载任务写入。"),
            ("local_scan", "本地扫描", "由本地目录扫描写入。"),
            ("external_scan", "外置扫描", "由外置目录扫描写入。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "video_metadata_status",
        "视频技术元数据状态",
        "探测时长、分辨率、编码等媒体信息的状态。",
        _terms(
            ("pending", "待解析", "等待媒体探测。"),
            ("processing", "解析中", "正在执行媒体探测。"),
            ("ready", "可用", "技术元数据已经成功写入。"),
            ("inherited", "继承", "使用剧集或季级共享元数据。"),
            ("unavailable", "不可用", "无法读取或生成媒体元数据。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "normalization_status",
        "播放标准化状态",
        "为浏览器兼容而执行容器或编码标准化的状态。",
        _terms(
            ("none", "无需处理", "当前文件无需播放标准化。"),
            ("processing", "处理中", "正在生成兼容播放文件。"),
            ("ready", "已完成", "播放标准化文件已经可用。"),
            ("failed", "失败", "播放标准化执行失败。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "cover_source",
        "封面来源",
        "视频封面图的生成方式。",
        _terms(
            ("auto", "自动截帧", "系统从视频自动生成封面。"),
            ("custom", "用户自定义", "用户上传或选择的封面。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "preview_status",
        "预览生成状态",
        "视频或扫描候选预览片段的生命周期。",
        _terms(
            ("pending", "等待生成", "已经排队但尚未开始。"),
            ("queued", "已入队", "导入任务已完成并等待预览 worker。"),
            ("processing", "生成中", "预览 worker 正在处理。"),
            ("ready", "可用", "预览文件已生成并可用。"),
            ("failed", "失败", "预览生成失败。"),
            ("none", "尚未安排", "当前没有预览任务。"),
            ("not_required", "不需要", "该记录不需要独立预览。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "download_metadata_status",
        "下载任务元数据状态",
        "下载完成后附加分类、演员等信息的处理状态。",
        _terms(
            ("pending", "待处理", "尚未开始附加元数据。"),
            ("success", "成功", "附加元数据写入成功。"),
            ("inherited", "继承", "从剧集或来源共享记录继承。"),
            ("failed", "失败", "附加元数据获取失败。"),
            ("not_required", "不需要", "该下载不执行附加元数据流程。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "episode_origin_type",
        "剧集来源",
        "剧集记录的产生方式。",
        _terms(
            ("download", "下载", "由下载批次产生。"),
            ("external_scan", "外置扫描", "由外置目录扫描产生。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "episode_status",
        "剧集状态",
        "剧集记录对应的文件可用状态。",
        _terms(
            ("planned", "已规划", "已经发现并建立剧集记录，尚未完成下载。"),
            ("ready", "已就绪", "剧集记录和文件信息已经就绪。"),
            ("available", "可用", "剧集文件已经可访问。"),
            ("deleted", "已删除", "关联视频被删除后剧集进入不可用状态。"),
            ("failed", "失败", "剧集文件或登记失败。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "category_external_type",
        "外部分类类型",
        "来源站点的分类对象类型。",
        _terms(
            ("tag", "标签", "来源站点标签。"),
            ("category", "分类", "来源站点正式分类。"),
            ("special", "特殊分类", "站点特殊或推荐分类。"),
            ("bilibili_style", "Bilibili 风格", "Bilibili 风格标签。"),
            ("bilibili_year", "Bilibili 年份", "Bilibili 年份分类。"),
        ),
        is_open=True,
    ),
    ValueSet(
        "category_source_kind",
        "分类关系来源",
        "视频与分类关系来自哪个站点解析器。",
        _terms(
            ("title", "标题推断", "从视频标题推断分类。"),
            ("xhamster", "xHamster", "xHamster 官方分类。"),
            ("xhamster_fallback", "xHamster 回退", "xHamster 次级元数据。"),
            ("xvideos_tag", "xVideos 标签", "xVideos 官方标签。"),
            ("xvideos_fallback", "xVideos 回退", "xVideos 次级元数据。"),
            ("bilibili_style", "Bilibili 风格", "Bilibili 风格标签。"),
            ("faphouse_category", "FapHouse 分类", "FapHouse 分类。"),
        ),
        is_open=True,
    ),
    ValueSet(
        "avatar_status",
        "演员头像状态",
        "演员头像下载与缓存状态。",
        _terms(
            ("pending", "待下载", "已经发现头像地址，等待下载。"),
            ("fetched", "已获取", "头像已经下载并登记。"),
            ("failed", "失败", "头像下载或校验失败。"),
            ("unavailable", "无头像", "来源没有可用头像。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "category_fetch_status",
        "分类抓取状态",
        "站点元数据抓取缓存结果。",
        _terms(
            ("fetched", "抓取成功", "分类和演员已经抓取并缓存。"),
            ("failed", "抓取失败", "站点抓取失败，可稍后重试。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "playback_preparation_mode",
        "播放准备模式",
        "选择原始文件、重封装或转码的方式。",
        _terms(
            ("legacy", "旧缓存", "旧版本生成且缺少决策信息的缓存。"),
            ("direct", "直接播放", "源文件可被浏览器直接读取。"),
            ("remux", "仅重封装", "不改变编码，生成浏览器可读容器。"),
            ("unsupported", "不支持", "需要转码，当前不自动处理。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "playback_decision_reason",
        "播放准备原因",
        "为什么需要或不需要播放缓存。",
        _terms(
            ("fragmented_mp4", "分片 MP4", "分片 MP4 需要重封装。"),
            ("unsupported_video_codec", "视频编码不支持", "浏览器不支持当前视频编码。"),
            ("unsupported_audio_codec", "音频编码不支持", "浏览器不支持当前音频编码。"),
            (
                "container_or_codec_requires_cache",
                "容器或编码需要缓存",
                "源容器或编码不能直接播放。",
            ),
            ("", "未记录", "旧缓存调用没有提供决策原因。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "playback_cache_status",
        "播放缓存状态",
        "播放缓存文件登记状态。",
        _terms(("ready", "可用", "缓存文件存在并已登记。")),
        is_strict=True,
    ),
    ValueSet(
        "download_status",
        "下载任务状态",
        "下载任务的生命周期状态。",
        _terms(
            ("pending", "等待下载", "任务在队列中等待。"),
            ("running", "下载中", "下载进程正在运行。"),
            ("pausing", "暂停中", "已请求暂停，正在保存断点。"),
            ("paused", "已暂停", "断点已保存，可继续下载。"),
            ("success", "成功", "下载和入库已经完成。"),
            ("failed", "失败", "任务执行失败。"),
            ("cancelled", "已取消", "任务被用户取消。"),
            ("deleting", "删除中", "正在删除任务及文件。"),
            ("deleted", "已删除", "任务文件已经删除。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "download_stage",
        "下载任务阶段",
        "下载任务当前执行步骤。",
        _terms(
            ("waiting", "等待", "等待 worker 或资源。"),
            ("opening_page", "打开页面", "打开来源页面。"),
            ("metadata_source", "获取来源信息", "读取来源页面元数据。"),
            ("discovering_media", "查找媒体", "查找媒体候选。"),
            ("downloading", "下载媒体", "正在下载媒体内容。"),
            ("recovering", "自动恢复", "自动恢复失败下载。"),
            ("refreshing_media", "刷新媒体地址", "媒体 URL 失效后重新解析。"),
            ("validating_resume", "校验断点", "校验断点与远端文件是否一致。"),
            ("resuming", "恢复下载", "正在从断点继续。"),
            ("pausing", "保存断点", "正在保存暂停状态。"),
            ("paused", "已暂停", "任务处于暂停状态。"),
            ("normalizing", "视频标准化", "执行容器或编码标准化。"),
            ("registering", "写入资料库", "将下载文件登记为视频。"),
            ("metadata", "元数据", "附加分类和演员信息。"),
            ("categories", "分类", "同步视频分类。"),
            ("actors", "演员", "同步视频演员。"),
            ("finalizing", "整理文件", "整理输出文件名和目录。"),
            ("finished", "完成", "任务流程结束。"),
            ("deleting", "正在删除", "正在删除任务文件。"),
            ("delete_failed", "删除失败", "任务文件删除失败。"),
            ("deleted", "已删除", "任务和文件已经删除。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "quality_selection",
        "视频质量选择",
        "下载和站点策略使用的画质目标。",
        _terms(
            ("site", "跟随站点", "使用站点策略默认画质。"),
            ("auto", "自动", "在最大高度约束下选择最高可用画质。"),
            ("2160p", "4K", "最高 2160p。"),
            ("1080p", "1080p", "目标高度 1080。"),
            ("720p", "720p", "目标高度 720。"),
            ("480p", "480p", "目标高度 480。"),
            ("360p", "360p", "目标高度 360。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "quality_target",
        "媒体质量目标",
        "站点媒体选择策略的目标质量。",
        _terms(
            ("auto", "自动", "在最小和最大高度之间选择最高质量。"),
            ("source_first", "来源优先", "优先选择正确媒体来源，再考虑画质。"),
            ("2160p", "4K", "目标 2160p。"),
            ("1080p", "1080p", "目标 1080p。"),
            ("720p", "720p", "目标 720p。"),
            ("480p", "480p", "目标 480p。"),
            ("360p", "360p", "目标 360p。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "quality_fallback",
        "画质兜底策略",
        "精确画质不可用时的降级方式。",
        _terms(
            ("nearest_below", "优先较低档", "从低于目标的可选画质中选择最高一档。"),
            ("best_effort", "最接近档位", "选择与目标差值最小的画质。"),
            ("strict", "严格匹配", "目标画质不可用时失败。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "transport_kind",
        "下载传输方式",
        "媒体实际使用的下载通道。",
        _terms(
            ("hls", "HLS", "分片播放列表下载。"),
            ("direct-range", "HTTP Range", "直接文件分段下载。"),
            ("yt-dlp", "yt-dlp", "使用 yt-dlp 下载器。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "media_kind",
        "媒体候选类型",
        "媒体候选文件的容器或传输类型。",
        _terms(
            ("file", "单文件", "普通 HTTP 文件。"),
            ("hls", "HLS", "HLS 分片播放列表。"),
            ("dash", "DASH", "DASH 音视频流。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "media_source",
        "媒体候选来源",
        "候选媒体如何被发现。",
        _terms(
            ("player-config", "播放器配置", "从播放器初始化配置读取。"),
            ("network", "网络请求", "从浏览器网络请求捕获。"),
            ("video-element", "视频元素", "从 video 元素读取。"),
            ("performance", "性能记录", "从 Performance API 读取。"),
            ("command-line", "命令行输出", "从下载器命令行输出解析。"),
        ),
        is_open=True,
    ),
    ValueSet(
        "file_name_mode",
        "剧集文件名模式",
        "批量下载剧集时的文件命名方式。",
        _terms(
            ("sequence", "按集号", "使用剧集顺序或集号命名。"),
            ("original", "保留原名", "尽可能保留来源原始标题。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "episode_batch_status",
        "剧集下载批次状态",
        "批量下载预览与确认生命周期。",
        _terms(
            ("preview", "预览", "发现结果尚未确认。"),
            ("confirmed", "已确认", "用户已确认并创建下载任务。"),
            ("cancelled", "已取消", "该批次已取消。"),
            ("failed", "失败", "批次创建或执行失败。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "episode_item_validation",
        "剧集条目校验状态",
        "剧集条目是否可创建下载任务。",
        _terms(
            ("ready", "可下载", "条目可创建下载任务。"),
            ("exists", "文件已存在", "目标文件已经存在。"),
            ("existing_episode", "剧集已入库", "同来源剧集已经入库。"),
            ("existing_task", "已有任务", "同来源存在下载任务。"),
            ("existing_task_elsewhere", "其他位置有任务", "其他批次已有同来源任务。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "episode_discovery_mode",
        "剧集发现模式",
        "连续剧发现结果的聚合方式。",
        _terms(
            ("continuous_grouped", "连续分组", "按 Tab、季或选集分组展示。"),
            ("continuous_aggregate", "连续聚合", "把多个来源条目聚合为一个连续列表。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "discovery_tab_status",
        "发现 Tab 状态",
        "发现结果中单个 Tab 的创建状态。",
        _terms(
            ("ready", "可用", "该 Tab 可以确认并创建任务。"),
            ("failed", "失败", "该 Tab 创建批次失败。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "identity_mode",
        "媒体身份校验模式",
        "候选媒体与页面视频身份的匹配严格程度。",
        _terms(
            ("site_default", "站点默认", "优先页面视频并使用站点默认规则。"),
            ("page_id", "严格页面身份", "候选必须匹配页面视频身份。"),
            ("loose", "宽松", "允许特殊站点的宽松媒体匹配。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "format_strategy",
        "媒体格式策略",
        "媒体候选是单文件还是分离音视频。",
        _terms(
            ("auto", "自动", "根据候选格式自动判断。"),
            ("combined", "单文件", "优先使用音视频合一的文件。"),
            ("separate_av", "分离音视频", "分别选择视频和音频后合并。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "merge_output_format",
        "合并输出容器",
        "分离音视频合并后的容器格式。",
        _terms(
            ("mp4", "MP4", "输出 MP4 容器。"),
            ("mkv", "MKV", "输出 MKV 容器。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "media_source_order_element",
        "媒体来源顺序元素",
        "source_order_json 数组中允许出现的来源。",
        _terms(
            ("player-config", "播放器配置", "优先播放器配置。"),
            ("network", "网络请求", "优先网络请求。"),
            ("video-element", "视频元素", "优先 video 元素。"),
            ("performance", "性能记录", "优先性能记录。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "media_kind_order_element",
        "媒体类型顺序元素",
        "kind_order_json 数组中允许出现的媒体类型。",
        _terms(
            ("hls", "HLS", "优先 HLS。"),
            ("dash", "DASH", "优先 DASH。"),
            ("file", "单文件", "优先普通文件。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "transport_mode",
        "站点传输模式",
        "站点策略选择下载通道的方式。",
        _terms(
            ("auto", "自动", "根据站点生命周期自动选择。"),
            ("hls", "HLS", "强制使用 HLS。"),
            ("direct-range", "HTTP Range", "强制使用分段直连。"),
            ("yt-dlp", "yt-dlp", "强制使用 yt-dlp。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "lifecycle_state",
        "站点生命周期",
        "站点下载能力的验证等级。",
        _terms(
            ("UNKNOWN", "未知", "尚未验证。"),
            ("TASK_QUALIFIED", "单任务验证", "仅单个任务验证成功。"),
            ("CANDIDATE", "候选", "具备提升为已验证站点的条件。"),
            ("VERIFIED", "已验证", "站点策略已经验证通过。"),
            ("QUARANTINED", "隔离", "站点存在问题，暂停使用。"),
            ("DISABLED", "禁用", "站点策略被禁用。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "tls_backend",
        "TLS 后端",
        "下载请求使用的 TLS 实现。",
        _terms(
            ("auto", "自动", "运行时自动选择。"),
            ("httpx", "httpx", "使用 httpx TLS。"),
            ("curl_cffi", "curl_cffi", "使用 curl_cffi 浏览器 TLS。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "breaker_state",
        "熔断状态",
        "站点下载熔断器状态。",
        _terms(
            ("CLOSED", "关闭", "请求正常。"),
            ("OPEN", "打开", "暂停尝试并等待冷却。"),
            ("HALF_OPEN", "半开", "允许少量试探请求。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "event_severity",
        "事件严重级别",
        "站点事件日志严重程度。",
        _terms(
            ("info", "信息", "一般运行信息。"),
            ("warning", "警告", "需要关注但未中断运行。"),
            ("error", "错误", "发生错误。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "storage_identity_source",
        "存储卷身份来源",
        "外置卷身份是如何识别的。",
        _terms(
            ("marker", "标记文件", "通过站点标记文件识别。"),
            ("volume_uuid", "磁盘 UUID", "通过磁盘 UUID 识别。"),
            ("device_label", "设备名称回退", "标记文件和 UUID 都不可用时，按挂载设备名称生成稳定身份。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "storage_access_mode",
        "存储卷访问模式",
        "存储卷是否可写。",
        _terms(
            ("read_write", "读写", "可读取和写入。"),
            ("read_only", "只读", "只允许读取。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "storage_online_state",
        "存储卷在线状态",
        "最近一次探针结果。",
        _terms(
            ("unknown", "未知", "尚未完成探针。"),
            ("online", "在线", "存储卷当前可用。"),
            ("offline", "离线", "存储卷当前不可用。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "storage_kind",
        "存储位置类型",
        "文件位于本地目录还是外置卷。",
        _terms(
            ("local", "本地", "位于配置的本地目录。"),
            ("external", "外置", "位于受管理的外置卷。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "verification_mode",
        "文件校验模式",
        "复制或迁移文件后的完整性校验方式。",
        _terms(
            ("sha256", "SHA-256", "比较源文件和目标文件完整哈希。"),
            ("size_mtime", "大小与时间", "比较文件大小和修改时间。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "migration_status",
        "文件迁移状态",
        "视频在存储位置间迁移的生命周期。",
        _terms(
            ("pending", "等待迁移", "任务在队列中。"),
            ("running", "迁移中", "正在复制或提交。"),
            ("success", "迁移成功", "迁移和校验完成。"),
            ("failed", "迁移失败", "迁移执行失败。"),
            ("cancelled", "已取消", "迁移被取消。"),
            ("needs_cleanup", "等待清理", "目标已提交，旧副本等待清理。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "migration_stage",
        "文件迁移阶段",
        "迁移任务当前执行步骤。",
        _terms(
            ("waiting", "等待", "等待 worker。"),
            ("preflight", "检查环境", "检查源、目标和空间。"),
            ("copying", "复制视频", "复制文件内容。"),
            ("verifying", "校验完整性", "校验目标文件。"),
            ("committing", "更新位置", "更新视频数据库位置。"),
            ("cleanup", "清理旧副本", "删除旧位置文件。"),
            ("finished", "完成", "任务结束。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "migration_mode",
        "迁移模式",
        "迁移是用户手动创建还是自动策略创建。",
        _terms(
            ("manual", "手动", "用户手动创建。"),
            ("auto", "自动", "系统按策略创建。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "migration_direction",
        "迁移方向",
        "视频存储位置移动方向。",
        _terms(
            ("to_external", "迁往外置卷", "从本地移动到外置卷。"),
            ("to_local", "迁回本地", "从外置卷移回本地。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "import_status",
        "导入状态",
        "外部文件导入任务生命周期。",
        _terms(
            ("pending", "等待导入", "任务在队列中。"),
            ("running", "正在导入", "正在复制和登记。"),
            ("success", "导入成功", "复制、校验和登记完成。"),
            ("failed", "导入失败", "导入执行失败。"),
            ("cancelled", "已取消", "导入被取消。"),
            ("needs_cleanup", "等待清理", "源文件等待清理。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "import_stage",
        "导入阶段",
        "导入任务当前执行步骤。",
        _terms(
            ("waiting", "等待", "等待 worker。"),
            ("preflight", "检查环境", "检查源文件、目标和空间。"),
            ("copying", "复制文件", "复制源文件。"),
            ("preview_queued", "预览已排队", "等待生成预览。"),
            ("metadata", "元数据", "执行技术元数据探测。"),
            ("committing_video", "登记视频", "将文件登记到视频库。"),
            ("verifying", "校验", "校验复制结果。"),
            ("cleanup", "清理源文件", "按选择删除源文件。"),
            ("finished", "完成", "任务结束。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "import_metadata_status",
        "导入元数据状态",
        "导入后技术元数据生成状态。",
        _terms(
            ("pending", "待处理", "尚未执行。"),
            ("ready", "可用", "元数据已经生成。"),
            ("unavailable", "不可用", "无法读取元数据。"),
            ("failed", "失败", "元数据流程异常。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "import_thumbnail_status",
        "导入缩略图状态",
        "导入后缩略图生成状态。",
        _terms(
            ("pending", "待处理", "尚未执行。"),
            ("ready", "可用", "缩略图已经生成。"),
            ("failed", "失败", "缩略图生成失败。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "scan_status",
        "扫描批次状态",
        "外置目录扫描任务生命周期。",
        _terms(
            ("pending", "等待扫描", "扫描在队列中。"),
            ("running", "扫描中", "正在发现、探测或生成资源。"),
            ("review", "等待审核", "扫描结果等待用户审核。"),
            ("committing", "正在入库", "正在写入视频库。"),
            ("completed", "已完成", "扫描和入库完成。"),
            ("partial", "部分完成", "部分条目成功，部分失败。"),
            ("failed", "失败", "扫描失败。"),
            ("cancelled", "已取消", "扫描被取消。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "scan_stage",
        "扫描阶段",
        "扫描批次当前执行步骤。",
        _terms(
            ("queued", "等待处理", "扫描任务已排队。"),
            ("discovering", "发现视频", "查找候选文件。"),
            ("probing", "解析媒体", "读取技术元数据。"),
            ("generating_assets", "生成资源", "生成缩略图和预览。"),
            ("waiting_review", "等待审核", "等待用户确认。"),
            ("committing", "写入视频库", "登记视频和剧集。"),
            ("finished", "完成", "扫描流程结束。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "scan_source_type",
        "扫描来源类型",
        "扫描输入是文件还是目录。",
        _terms(
            ("file", "单文件", "扫描一个视频文件。"),
            ("directory", "目录", "扫描一个目录。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "scan_mode",
        "扫描模式",
        "扫描结果的批次组织方式。",
        _terms(
            ("file", "单文件", "仅处理一个文件。"),
            ("batch", "批量", "处理目录下的独立文件。"),
            ("collection", "合集", "按季和集识别一个剧集目录。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "scan_grouping_mode",
        "扫描分组模式",
        "合集扫描的季识别方式。",
        _terms(
            ("single_season", "单季", "全部结果作为一个季。"),
            ("multi_season", "多季", "按目录识别多个季。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "scan_item_status",
        "扫描条目状态",
        "单个扫描候选的处理状态。",
        _terms(
            ("pending", "等待解析", "尚未探测。"),
            ("probing", "解析中", "正在读取媒体信息。"),
            ("ready", "可入库", "准备完成，可提交入库。"),
            ("needs_review", "需要处理", "存在命名、重复或元数据问题。"),
            ("selected", "已选择", "用户已选择该条目。"),
            ("rejected", "已忽略", "用户忽略该条目。"),
            ("imported", "已入库", "该条目已写入视频库。"),
            ("duplicate", "已存在", "资料库中已有相同视频。"),
            ("failed", "失败", "该条目处理失败。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "rename_status",
        "重命名日志状态",
        "文件重命名事务日志状态。",
        _terms(
            ("prepared", "已准备", "数据库日志已创建，文件操作待完成。"),
            ("db_committed", "数据库已提交", "文件与数据库变更均已提交。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "trim_status",
        "剪辑任务状态",
        "视频片头剪辑任务生命周期。",
        _terms(
            ("pending", "等待中", "任务在队列中。"),
            ("running", "进行中", "正在剪辑或校验。"),
            ("success", "成功", "剪辑完成。"),
            ("failed", "失败", "剪辑失败。"),
            ("cancelled", "已取消", "剪辑被取消。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "trim_stage",
        "剪辑阶段",
        "剪辑任务当前执行步骤。",
        _terms(
            ("waiting", "等待", "等待 worker。"),
            ("preflight", "检查文件", "检查源文件和裁剪位置。"),
            ("trimming", "正在剪辑", "执行 ffmpeg 裁剪。"),
            ("verifying", "验证输出", "校验输出时长和文件。"),
            ("committing", "提交文件", "替换源文件并更新数据库。"),
            ("finished", "完成", "任务结束。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "trim_mode",
        "剪辑方式",
        "片头裁剪落在哪个时间点。",
        _terms(
            ("after_keyframe", "后一关键帧", "使用目标时间之后的关键帧，速度快。"),
            ("before_keyframe", "前一关键帧", "使用目标时间之前的关键帧。"),
            ("precise", "精确裁剪", "重新编码以获得精确时间。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "format_status",
        "格式任务状态",
        "视频格式转换任务生命周期。",
        _terms(
            ("pending", "等待处理", "任务在队列中。"),
            ("running", "处理中", "正在封装或转码。"),
            ("success", "处理成功", "格式处理完成。"),
            ("failed", "处理失败", "格式处理失败。"),
            ("cancelled", "已取消", "格式处理被取消。"),
            ("needs_cleanup", "等待清理", "输出已提交，旧文件待清理。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "format_stage",
        "格式任务阶段",
        "格式任务当前执行步骤。",
        _terms(
            ("waiting", "等待 Worker", "等待处理。"),
            ("waiting_storage", "等待外盘连接", "目标外置卷离线。"),
            ("preflight", "检查媒体与空间", "检查编码、目标和空间。"),
            ("remuxing", "直接封装 MP4", "不改变编码进行封装。"),
            ("transcoding", "编码转译 MP4", "执行有损转码。"),
            ("finalizing", "写入 MP4 索引", "整理输出容器。"),
            ("verifying", "校验输出文件", "校验输出文件是否有效。"),
            (
                "extracting_subtitles",
                "提取字幕资产",
                "提取并转换字幕后继续提交视频。",
            ),
            ("committing", "更新视频数据", "更新路径和元数据。"),
            ("backing_up", "保留安全备份", "保留源文件备份。"),
            ("deleting_source", "清理原 MKV", "删除源文件。"),
            ("finished", "处理完成", "任务结束。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "format_operation",
        "格式处理方式",
        "视频改为 MP4 时采用的编码策略。",
        (
            ValueTerm(
                "container_remux",
                "容器封装 · 无损",
                "保持编码，仅改变容器。",
                0,
                True,
            ),
            ValueTerm(
                "standard_transcode",
                "编码转译 · 有损",
                "转码为兼容性更高的编码。",
                1,
            ),
            ValueTerm(
                "mkv_to_mp4_remux",
                "旧版无损封装值",
                "历史别名，仅用于兼容旧调用；新数据应使用 container_remux。",
                2,
                deprecated_at="2026-11-20",
            ),
        ),
        is_strict=True,
    ),
    ValueSet(
        "format_profile",
        "格式配置档",
        "具体编码配置。",
        _terms(
            ("copy", "直接复制", "不重新编码。"),
            ("h264_aac_crf20", "H.264/AAC CRF20", "标准兼容有损转码配置。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "format_subtitle_mode",
        "格式任务字幕模式",
        "MP4 转换时外挂字幕的选择方式。",
        _terms(
            ("auto", "自动选择", "按中英双语、中文、稳定兜底顺序提取文本字幕。"),
            ("manual", "手动选择", "仅提取管理员勾选的文本字幕。"),
            ("none", "不提取", "转换为 MP4 时不生成外挂字幕。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "target_container",
        "目标容器",
        "格式任务的目标文件容器。",
        _terms(("mp4", "MP4", "输出 MP4 文件。")),
        is_strict=True,
    ),
    ValueSet(
        "compatibility_class",
        "格式兼容分类",
        "源编码是否适合直接封装 MP4。",
        _terms(
            ("standard", "标准兼容", "可直接封装并通常有广泛浏览器支持。"),
            ("conditional", "有条件兼容", "可封装，但浏览器支持取决于编码。"),
            ("unsupported", "不支持", "编码不适合直接封装 MP4。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "backup_status",
        "安全备份状态",
        "有损处理前的源文件备份状态。",
        _terms(
            ("none", "无备份", "没有需要保留的备份。"),
            ("available", "可用", "备份存在且可用于恢复。"),
            ("missing", "缺失", "备份记录存在，但文件找不到。"),
            ("deleted", "已删除", "用户已删除备份。"),
            ("restored", "已恢复", "备份已经恢复为当前视频文件。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "restore_status",
        "备份恢复状态",
        "备份恢复操作状态。",
        _terms(
            ("preparing", "准备中", "正在准备恢复。"),
            ("success", "成功", "备份已经恢复。"),
            ("failed", "失败", "备份恢复失败。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "delete_status",
        "删除任务状态",
        "视频、季或集的删除任务生命周期。",
        _terms(
            ("pending", "等待执行", "等待依赖或 worker。"),
            ("blocked", "等待下级任务", "必须先完成子任务。"),
            ("running", "执行中", "正在删除。"),
            ("success", "成功", "删除完成。"),
            ("failed", "失败", "删除失败。"),
            ("cancelled", "已取消", "删除被取消。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "delete_stage",
        "删除阶段",
        "删除任务当前执行步骤。",
        _terms(
            ("waiting_dependencies", "等待依赖", "等待下级删除任务。"),
            ("preflight", "检查删除条件", "确认密码、任务和目标。"),
            ("deleting_files", "删除视频文件", "删除物理文件。"),
            ("deleting_derivatives", "删除派生资源", "删除缩略图、预览和缓存。"),
            ("soft_deleting_video", "软删除视频", "更新视频删除标记。"),
            ("verifying_children", "校验下级数据", "确认季或集的子任务已完成。"),
            ("soft_deleting_season", "软删除视频季", "更新季删除标记。"),
            ("soft_deleting_series", "软删除视频集", "更新集删除标记。"),
            ("finished", "完成", "任务结束。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "delete_scope",
        "删除范围",
        "删除目标是视频、季还是视频集。",
        _terms(
            ("video", "视频", "删除一个视频。"),
            ("season", "视频季", "删除一个季及其视频。"),
            ("series", "视频集", "删除整个系列。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "delete_source_action",
        "删除入口",
        "用户从哪个管理页面发起删除。",
        _terms(
            ("video_management", "视频管理", "从视频管理页发起。"),
            ("season_management", "视频季管理", "从季管理页发起。"),
            ("series_management", "视频集管理", "从系列管理页发起。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "delete_verification_method",
        "删除校验方式",
        "删除前使用的安全校验方式。",
        _terms(("password", "删除密码", "通过删除密码确认操作。")),
        is_strict=True,
    ),
    ValueSet(
        "integrity_job_status",
        "完整性任务状态",
        "后台完整性检查任务生命周期。",
        _terms(
            ("pending", "等待", "任务在队列中。"),
            ("running", "检查中", "正在检查数据库完整性。"),
            ("success", "成功", "检查完成并已保存报告。"),
            ("failed", "失败", "检查任务异常。"),
        ),
        is_strict=True,
    ),
    ValueSet(
        "integrity_run_status",
        "完整性检查结果",
        "完整性扫描的结论。",
        _terms(
            ("healthy", "健康", "未发现问题。"),
            ("degraded", "降级", "发现一个或多个数据问题。"),
        ),
        is_strict=True,
    ),
)


DEFAULT_OVERRIDES = {
    ("users", "is_active"): "1",
    ("users", "must_change_password"): "0",
    ("auth_login_attempts", "success"): "0",
    ("videos", "missing"): "0",
    ("videos", "is_deleted"): "0",
    ("video_series", "is_deleted"): "0",
    ("video_seasons", "is_deleted"): "0",
    ("download_tasks", "index_into_library"): "1",
    ("download_tasks", "pause_requested"): "0",
    ("download_tasks", "delete_requested"): "0",
    ("episode_download_items", "selected"): "0",
    ("episode_download_discoveries", "aggregate_single_season"): "0",
    ("episode_download_discoveries", "preserve_original_name"): "0",
    ("episode_download_discoveries", "complete"): "1",
    ("site_media_policies", "allow_below_minimum"): "1",
    ("site_media_policies", "allow_page_fallback"): "1",
    ("site_media_policies", "prefer_h264"): "0",
    ("site_media_policies", "manual_override"): "1",
    ("transport_site_policies", "manual_override"): "0",
    ("transport_site_policies", "is_builtin"): "0",
    ("transport_site_policies", "qualification_enabled"): "1",
    ("video_storage_volumes", "is_active"): "1",
    ("video_migrations", "cancel_requested"): "0",
    ("video_import_jobs", "delete_source"): "0",
    ("video_import_jobs", "cancel_requested"): "0",
    ("video_scan_batches", "review_required"): "0",
    ("video_scan_batches", "cancel_requested"): "0",
    ("video_scan_items", "selected"): "1",
    ("video_trim_jobs", "cancel_requested"): "0",
    ("video_format_jobs", "lossy"): "0",
    ("video_format_jobs", "cancel_requested"): "0",
    ("download_tasks", "requested_quality"): "auto",
    ("site_media_policies", "preset_key"): "generic",
    ("video_format_jobs", "operation"): "mkv_to_mp4_remux",
}


def _columns(
    set_key: str,
    entries: Iterable[Tuple[str, str, str]],
    *,
    enforcement: str = "trigger",
    nullable: bool = False,
    null_meaning: str = "",
    sql_type: str = "TEXT",
    default_value: Optional[str] = None,
) -> Tuple[ColumnVocabulary, ...]:
    return tuple(
        ColumnVocabulary(
            table_name,
            column_name,
            set_key,
            sql_type,
            nullable,
            null_meaning,
            DEFAULT_OVERRIDES.get(
                (table_name, column_name),
                default_value,
            ),
            description,
            enforcement,
        )
        for table_name, column_name, description in entries
    )


COLUMNS: Tuple[ColumnVocabulary, ...] = (
    *_columns(
        "boolean",
        (
            ("users", "is_active", "账号是否可登录。"),
            ("users", "must_change_password", "账号下次登录时是否必须修改密码。"),
            ("auth_login_attempts", "success", "本次登录尝试是否成功。"),
            ("videos", "missing", "最近一次扫描是否未找到视频文件。"),
            ("videos", "is_deleted", "视频是否已软删除。"),
            ("video_series", "is_deleted", "视频集是否已软删除。"),
            ("video_seasons", "is_deleted", "视频季是否已软删除。"),
            ("download_tasks", "index_into_library", "下载成功后是否写入视频资料库。"),
            ("download_tasks", "pause_requested", "是否已请求暂停下载。"),
            ("download_tasks", "delete_requested", "是否已请求删除下载。"),
            ("episode_download_items", "selected", "该剧集条目是否被选中下载。"),
            ("episode_download_discoveries", "aggregate_single_season", "是否聚合为单个季。"),
            ("episode_download_discoveries", "preserve_original_name", "是否保留原始文件名。"),
            ("episode_download_discoveries", "complete", "剧集发现结果是否完整。"),
            ("site_media_policies", "allow_below_minimum", "低于最小高度时是否允许兜底。"),
            ("site_media_policies", "allow_page_fallback", "是否允许页面级媒体回退。"),
            ("site_media_policies", "prefer_h264", "是否优先 H.264。"),
            ("site_media_policies", "manual_override", "配置是否由用户手动覆盖。"),
            ("transport_site_policies", "manual_override", "站点策略是否被用户手动覆盖。"),
            ("transport_site_policies", "is_builtin", "站点策略是否为内置策略。"),
            ("transport_site_policies", "qualification_enabled", "是否启用传输资格验证。"),
            ("video_storage_volumes", "is_active", "存储卷是否启用。"),
            ("video_migrations", "cancel_requested", "是否已请求取消迁移。"),
            ("video_import_jobs", "delete_source", "导入成功后是否删除源文件。"),
            ("video_import_jobs", "cancel_requested", "是否已请求取消导入。"),
            ("video_scan_batches", "review_required", "扫描结果是否需要人工审核。"),
            ("video_scan_batches", "cancel_requested", "是否已请求取消扫描。"),
            ("video_scan_items", "selected", "扫描候选是否被选中入库。"),
            ("video_trim_jobs", "cancel_requested", "是否已请求取消剪辑。"),
            ("video_format_jobs", "lossy", "格式处理是否为有损转码。"),
            ("video_format_jobs", "cancel_requested", "是否已请求取消格式处理。"),
        ),
        sql_type="INTEGER",
        default_value="0",
    ),
    *_columns(
        "access_level",
        (
            ("videos", "access_level", "视频访问级别。"),
            ("download_tasks", "access_level", "下载任务默认写入的视频访问级别。"),
            ("episode_download_batches", "access_level", "剧集批次写入的视频访问级别。"),
            ("episode_download_discoveries", "access_level", "剧集发现结果写入的视频访问级别。"),
            ("video_import_jobs", "access_level", "导入视频的访问级别。"),
            ("video_scan_batches", "access_level", "扫描批次导入视频的访问级别。"),
            ("video_scan_items", "access_level", "扫描条目导入视频的访问级别。"),
        ),
        sql_type="INTEGER",
        default_value="0",
    ),
    *_columns(
        "user_role",
        (("users", "role", "账号角色，决定可访问的管理功能。"),),
        default_value="user",
    ),
    *_columns(
        "session_revoke_reason",
        (("user_sessions", "revoke_reason", "登录会话失效原因。"),),
        enforcement="none",
        nullable=True,
        null_meaning="会话仍有效。",
    ),
    *_columns(
        "login_failure_code",
        (("auth_login_attempts", "failure_code", "登录失败原因代码。"),),
        enforcement="none",
        nullable=True,
        null_meaning="登录成功，或没有记录具体失败码。",
    ),
    *_columns(
        "open_code",
        (
            ("auth_audit_log", "action", "账号审计动作，允许后续功能增加。"),
            ("site_media_policies", "preset_key", "站点媒体策略预设键。"),
            ("transport_site_events", "event_type", "站点事件类型，允许后续新增。"),
            ("transport_policy_audit", "action", "站点策略审计动作，允许后续新增。"),
        ),
        enforcement="none",
    ),
    *_columns(
        "open_code",
        (
            ("auth_audit_log", "target_type", "账号审计对象类型，允许后续增加。"),
            ("download_tasks", "media_strategy_key", "下载时使用的媒体策略键。"),
            ("transport_site_events", "reason_code", "站点事件原因代码，允许后续新增。"),
        ),
        enforcement="none",
        nullable=True,
        null_meaning="没有可用的开放代码值。",
    ),
    *_columns(
        "audit_result",
        (("auth_audit_log", "result", "账号审计操作的最终结果。"),),
    ),
    *_columns(
        "media_origin",
        (("videos", "media_origin", "视频首次写入资料库的来源。"),),
        default_value="download",
    ),
    *_columns(
        "video_metadata_status",
        (("video_media_info", "metadata_status", "视频技术元数据的处理状态。"),),
        default_value="pending",
    ),
    *_columns(
        "normalization_status",
        (("video_media_info", "normalization_status", "播放标准化处理状态。"),),
        default_value="none",
    ),
    *_columns(
        "cover_source",
        (("video_assets", "cover_source", "视频封面来源。"),),
        default_value="auto",
    ),
    *_columns(
        "preview_status",
        (
            ("video_assets", "preview_status", "视频预览生成状态。"),
            ("video_seasons", "preview_status", "季预览生成状态。"),
            ("video_import_jobs", "preview_status", "导入任务预览生成状态。"),
            ("video_scan_items", "preview_status", "扫描条目预览生成状态。"),
        ),
        default_value="pending",
    ),
    *_columns(
        "video_metadata_status",
        (("video_seasons", "metadata_status", "季级共享元数据状态。"),),
        default_value="pending",
    ),
    *_columns(
        "episode_origin_type",
        (("video_episodes", "origin_type", "剧集记录的产生来源。"),),
        default_value="download",
    ),
    *_columns(
        "episode_status",
        (("video_episodes", "status", "剧集文件可用状态。"),),
        default_value="planned",
    ),
    *_columns(
        "category_external_type",
        (("categories", "external_type", "来源站点的分类对象类型。"),),
        enforcement="none",
        nullable=True,
        null_meaning="该分类不是从外部站点直接获取。",
    ),
    *_columns(
        "category_source_kind",
        (("video_categories", "source_kind", "分类关系来自哪个站点解析器。"),),
        enforcement="none",
        default_value="title",
    ),
    *_columns(
        "avatar_status",
        (("actors", "avatar_status", "演员头像缓存状态。"),),
        default_value="pending",
    ),
    *_columns(
        "category_fetch_status",
        (("category_fetch_cache", "status", "站点分类和演员抓取结果。"),),
    ),
    *_columns(
        "playback_preparation_mode",
        (("playback_cache_entries", "preparation_mode", "播放缓存的文件准备方式。"),),
        default_value="legacy",
    ),
    *_columns(
        "playback_decision_reason",
        (("playback_cache_entries", "decision_reason", "需要播放缓存的原因。"),),
        nullable=True,
        null_meaning="旧缓存没有记录决策原因。",
    ),
    *_columns(
        "playback_cache_status",
        (("playback_cache_entries", "status", "播放缓存文件状态。"),),
        default_value="ready",
    ),
    *_columns(
        "download_metadata_status",
        (("download_tasks", "metadata_status", "下载任务附加元数据状态。"),),
        default_value="pending",
    ),
    *_columns(
        "download_status",
        (("download_tasks", "status", "下载任务生命周期状态。"),),
        default_value="pending",
    ),
    *_columns(
        "download_stage",
        (("download_tasks", "stage", "下载任务当前执行阶段。"),),
        default_value="waiting",
    ),
    *_columns(
        "quality_selection",
        (
            ("download_tasks", "requested_quality", "下载任务请求的目标画质。"),
            ("episode_download_batches", "quality", "剧集批次的请求画质。"),
            ("episode_download_discoveries", "quality", "剧集发现结果的请求画质。"),
        ),
        default_value="site",
    ),
    *_columns(
        "transport_kind",
        (("download_tasks", "transport_kind", "实际使用的下载传输通道。"),),
        nullable=True,
        null_meaning="任务尚未识别传输通道。",
    ),
    *_columns(
        "media_kind",
        (("download_tasks", "selected_candidate_kind", "最终选择的媒体候选类型。"),),
        nullable=True,
        null_meaning="尚未选择媒体候选。",
    ),
    *_columns(
        "media_source",
        (("download_tasks", "selected_candidate_source", "最终媒体候选的发现来源。"),),
        enforcement="none",
        nullable=True,
        null_meaning="尚未选择媒体候选。",
    ),
    *_columns(
        "file_name_mode",
        (
            ("episode_download_batches", "file_name_mode", "剧集批次的文件命名方式。"),
            ("episode_download_discoveries", "file_name_mode", "发现批次的文件命名方式。"),
        ),
        default_value="sequence",
    ),
    *_columns(
        "episode_batch_status",
        (
            ("episode_download_batches", "status", "剧集下载批次状态。"),
            ("episode_download_discoveries", "status", "剧集发现批次状态。"),
        ),
        default_value="preview",
    ),
    *_columns(
        "episode_item_validation",
        (("episode_download_items", "validation_status", "剧集条目下载前校验状态。"),),
        default_value="ready",
    ),
    *_columns(
        "episode_discovery_mode",
        (("episode_download_discoveries", "mode", "剧集发现结果的聚合模式。"),),
        default_value="continuous_grouped",
    ),
    *_columns(
        "discovery_tab_status",
        (("episode_download_discovery_tabs", "status", "发现结果 Tab 的创建状态。"),),
        default_value="ready",
    ),
    *_columns(
        "identity_mode",
        (("site_media_policies", "identity_mode", "媒体候选身份匹配模式。"),),
        default_value="site_default",
    ),
    *_columns(
        "quality_target",
        (("site_media_policies", "quality_target", "站点媒体策略目标画质。"),),
        default_value="auto",
    ),
    *_columns(
        "quality_fallback",
        (("site_media_policies", "quality_fallback", "目标画质不可用时的兜底方式。"),),
        default_value="nearest_below",
    ),
    *_columns(
        "format_strategy",
        (("site_media_policies", "format_strategy", "媒体候选格式选择策略。"),),
        default_value="auto",
    ),
    *_columns(
        "merge_output_format",
        (("site_media_policies", "merge_output_format", "分离音视频合并后的容器。"),),
        default_value="mp4",
    ),
    *_columns(
        "media_source_order_element",
        (("site_media_policies", "source_order_json", "媒体来源优先级数组的元素。"),),
        enforcement="application",
    ),
    *_columns(
        "media_kind_order_element",
        (("site_media_policies", "kind_order_json", "媒体类型优先级数组的元素。"),),
        enforcement="application",
    ),
    *_columns(
        "transport_mode",
        (("transport_site_policies", "transport_mode", "站点下载传输模式。"),),
        default_value="auto",
    ),
    *_columns(
        "lifecycle_state",
        (("transport_site_policies", "lifecycle_state", "站点传输能力生命周期状态。"),),
        default_value="UNKNOWN",
    ),
    *_columns(
        "tls_backend",
        (("transport_site_policies", "tls_backend", "下载请求 TLS 后端。"),),
        default_value="auto",
    ),
    *_columns(
        "breaker_state",
        (("transport_site_policies", "breaker_state", "站点传输熔断器状态。"),),
        default_value="CLOSED",
    ),
    *_columns(
        "event_severity",
        (("transport_site_events", "severity", "站点事件严重级别。"),),
        default_value="info",
    ),
    *_columns(
        "storage_identity_source",
        (("video_storage_volumes", "identity_source", "外置卷身份识别方式。"),),
        default_value="marker",
    ),
    *_columns(
        "storage_access_mode",
        (("video_storage_volumes", "access_mode", "存储卷读写权限。"),),
        default_value="read_write",
    ),
    *_columns(
        "storage_online_state",
        (("video_storage_volumes", "online_state", "存储卷最近一次在线状态。"),),
        default_value="unknown",
    ),
    *_columns(
        "storage_kind",
        (
            ("video_migrations", "source_storage_kind", "迁移源存储位置类型。"),
            ("video_migrations", "destination_storage_kind", "迁移目标存储位置类型。"),
            ("video_format_jobs", "source_storage_kind", "格式处理源存储位置类型。"),
            ("video_format_jobs", "target_storage_kind", "格式处理目标存储位置类型。"),
        ),
    ),
    *_columns(
        "verification_mode",
        (
            ("video_migrations", "verification_mode", "文件迁移完整性校验方式。"),
            ("video_import_jobs", "verification_mode", "文件导入完整性校验方式。"),
        ),
        default_value="sha256",
    ),
    *_columns(
        "migration_status",
        (("video_migrations", "status", "文件迁移任务状态。"),),
    ),
    *_columns(
        "migration_stage",
        (("video_migrations", "stage", "文件迁移任务当前阶段。"),),
    ),
    *_columns(
        "migration_mode",
        (("video_migrations", "mode", "文件迁移创建模式。"),),
    ),
    *_columns(
        "migration_direction",
        (("video_migrations", "direction", "文件迁移方向。"),),
    ),
    *_columns(
        "import_status",
        (("video_import_jobs", "status", "外部文件导入任务状态。"),),
        default_value="pending",
    ),
    *_columns(
        "import_stage",
        (("video_import_jobs", "stage", "外部文件导入任务阶段。"),),
        default_value="waiting",
    ),
    *_columns(
        "import_metadata_status",
        (("video_import_jobs", "metadata_status", "导入后技术元数据状态。"),),
        default_value="pending",
    ),
    *_columns(
        "import_thumbnail_status",
        (("video_import_jobs", "thumbnail_status", "导入后缩略图状态。"),),
        default_value="pending",
    ),
    *_columns(
        "scan_status",
        (("video_scan_batches", "status", "外置目录扫描批次状态。"),),
        default_value="pending",
    ),
    *_columns(
        "scan_stage",
        (("video_scan_batches", "stage", "外置目录扫描批次阶段。"),),
        default_value="queued",
    ),
    *_columns(
        "scan_source_type",
        (("video_scan_batches", "source_type", "扫描输入类型。"),),
    ),
    *_columns(
        "scan_mode",
        (("video_scan_batches", "scan_mode", "扫描结果组织模式。"),),
    ),
    *_columns(
        "scan_grouping_mode",
        (("video_scan_batches", "grouping_mode", "合集扫描分组方式。"),),
        default_value="single_season",
    ),
    *_columns(
        "scan_item_status",
        (("video_scan_items", "status", "扫描候选条目状态。"),),
        default_value="pending",
    ),
    *_columns(
        "rename_status",
        (("video_rename_operations", "status", "文件重命名事务状态。"),),
    ),
    *_columns(
        "trim_status",
        (("video_trim_jobs", "status", "视频剪辑任务状态。"),),
        default_value="pending",
    ),
    *_columns(
        "trim_stage",
        (("video_trim_jobs", "stage", "视频剪辑任务阶段。"),),
        default_value="waiting",
    ),
    *_columns(
        "trim_mode",
        (("video_trim_jobs", "cut_mode", "片头裁剪的时间点模式。"),),
    ),
    *_columns(
        "backup_status",
        (
            ("video_trim_jobs", "backup_status", "剪辑源文件备份状态。"),
            ("video_format_jobs", "backup_status", "格式处理源文件备份状态。"),
        ),
        default_value="none",
    ),
    *_columns(
        "restore_status",
        (
            ("video_trim_jobs", "restore_status", "剪辑备份恢复状态。"),
            ("video_format_jobs", "restore_status", "格式处理备份恢复状态。"),
        ),
        nullable=True,
        null_meaning="尚未执行备份恢复。",
    ),
    *_columns(
        "format_status",
        (("video_format_jobs", "status", "视频格式处理任务状态。"),),
        default_value="pending",
    ),
    *_columns(
        "format_stage",
        (("video_format_jobs", "stage", "视频格式处理任务阶段。"),),
        default_value="waiting",
    ),
    *_columns(
        "format_operation",
        (("video_format_jobs", "operation", "视频格式处理方式。"),),
        default_value="container_remux",
    ),
    *_columns(
        "format_profile",
        (("video_format_jobs", "profile_key", "格式处理的编码配置档。"),),
        default_value="copy",
    ),
    *_columns(
        "format_subtitle_mode",
        (
            (
                "video_format_jobs",
                "subtitle_mode",
                "格式任务的外挂字幕提取模式。",
            ),
        ),
        default_value="auto",
    ),
    *_columns(
        "target_container",
        (("video_format_jobs", "target_container", "格式处理输出容器。"),),
        default_value="mp4",
    ),
    *_columns(
        "compatibility_class",
        (("video_format_jobs", "compatibility_class", "源文件与 MP4 兼容性分类。"),),
        nullable=True,
        null_meaning="尚未完成兼容性检查。",
    ),
    *_columns(
        "delete_status",
        (("video_delete_jobs", "status", "视频、季或集删除任务状态。"),),
        default_value="pending",
    ),
    *_columns(
        "delete_stage",
        (("video_delete_jobs", "stage", "删除任务当前阶段。"),),
        default_value="waiting_dependencies",
    ),
    *_columns(
        "delete_scope",
        (("video_delete_jobs", "scope_type", "删除目标范围。"),),
    ),
    *_columns(
        "delete_source_action",
        (("video_delete_jobs", "source_action", "删除操作发起页面。"),),
    ),
    *_columns(
        "delete_verification_method",
        (("video_delete_jobs", "verification_method", "删除前安全校验方式。"),),
        nullable=True,
        null_meaning="尚未执行删除校验。",
    ),
    *_columns(
        "integrity_job_status",
        (("runtime_integrity_jobs", "status", "后台完整性检查任务状态。"),),
        default_value="pending",
    ),
    *_columns(
        "integrity_run_status",
        (("runtime_integrity_runs", "status", "完整性检查结论。"),),
    ),
)


_ENUM_COLUMN_RE = re.compile(
    r"(?:"
    r"^is_|_status$|^status$|^stage$|_state$|^state$|_kind$|^kind$|"
    r"_mode$|^mode$|_role$|^role$|_level$|^level$|_severity$|^severity$|"
    r"_origin$|^origin$|_origin_type$|^origin_type$|_direction$|^direction$|"
    r"_operation$|^operation$|_scope_type$|^scope_type$|"
    r"_source_action$|_verification_mode$|_file_name_mode$|_grouping_mode$|"
    r"_cut_mode$|_identity_source$|_access_mode$|_online_state$|"
    r"_transport_kind$|_media_origin$|_external_type$|_source_kind$|"
    r"^source_action$|^verification_mode$|^file_name_mode$|^grouping_mode$|"
    r"^cut_mode$|^identity_source$|^access_mode$|^online_state$|"
    r"^transport_kind$|^media_origin$|^external_type$|^source_kind$|"
    r"_preparation_mode$|^preparation_mode$|_decision_reason$|"
    r"^decision_reason$|_compatibility_class$|^compatibility_class$|"
    r"_profile_key$|^profile_key$|_target_container$|^target_container$|"
    r"_tls_backend$|^tls_backend$|_lifecycle_state$|^lifecycle_state$|"
    r"_breaker_state$|^missing$|^success$|^selected$|^complete$|^lossy$|"
    r"^review_required$|^cancel_requested$|^delete_requested$|"
    r"^delete_source$|^pause_requested$|^index_into_library$|"
    r"^must_change_password$|"
    r"^manual_override$|^is_builtin$|^qualification_enabled$|"
    r"^prefer_h264$|^allow_|^aggregate_single_season$|"
    r"^preserve_original_name$|^event_type$|^target_type$|^action$|"
    r"^result$|^preset_key$|^media_strategy_key$|^cover_source$|"
    r"^selected_candidate_source$|^revoke_reason$|^failure_code$|"
    r"^reason_code$|^quality_target$|^quality_fallback$|"
    r"^format_strategy$|^merge_output_format$|^requested_quality$|"
    r"^quality$|^source_type$|^scan_mode$|^scope_type$|"
    r"^source_action$|^verification_method$|^source_order_json$|"
    r"^kind_order_json$"
    r")",
    re.IGNORECASE,
)


def is_enum_candidate(column_name: str) -> bool:
    name = str(column_name or "")
    if name.casefold() == "http_status":
        return False
    return bool(_ENUM_COLUMN_RE.search(name))


def value_sets_by_key() -> dict:
    return {item.key: item for item in VALUE_SETS}


def columns_by_key() -> dict:
    return {
        (item.table_name, item.column_name): item
        for item in COLUMNS
    }
