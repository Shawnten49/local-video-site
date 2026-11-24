# 第二阶段架构

## 领域目录

`local_video_site` 第一层只保留兼容入口：

- `app.py`：FastAPI 兼容入口。
- `__main__.py`：极薄的 CLI 启动入口。
- `database.py`：数据库兼容门面。

业务模块按领域组织：

- `core/`：配置、日志、网络和搜索。
- `identity/`：账号、认证与权限。
- `catalog/`：分类、演员和原始链接。
- `episodes/`：剧集命名、目录、批量任务、视频集管理和恢复。
- `downloads/`：任务创建、Worker、进度解析和策略。
- `ingestion/`：本地扫描、外盘扫描和导入。
- `media/`：媒体信息、播放、封面、预览、改名、裁剪和格式处理。
- `storage/`：存储卷、外盘迁移和删除任务。
- `jobs/`：通用任务运行时、租约、完整性和就绪检查。
- `integrations/`：站点适配和元数据来源。
- `cli/`：命令解析、分发和各命令实现。

## 路由

`local_video_site/app.py` 只创建模块级 FastAPI 实例。应用装配位于
`local_video_site/web/application.py`：

- 创建 `FastAPI`。
- 注册中间件和领域 APIRouter。
- 挂载带缓存策略的静态资源。

启动恢复和 Worker 生命周期位于 `local_video_site/web/lifespan.py`。请求模型位于
`local_video_site/web/models/`，HTTP 展示层按领域拆分：

- `local_video_site/web/routes/system.py`：健康、就绪和运行时完整性。
- `local_video_site/web/routes/videos.py`：首页、视频管理和视频裁剪。
- `local_video_site/web/routes/playback.py`：播放页、媒体响应和播放准备。
- `local_video_site/web/routes/media.py`：收藏、改名、封面和预览。
- `local_video_site/web/routes/categories.py`：分类和人工标签。
- `local_video_site/web/routes/actors.py`：演员和头像。
- `local_video_site/web/routes/series.py`：视频集、视频季、剧集编辑和重新上线。
- `local_video_site/web/routes/delete_tasks.py`：删除页面、删除任务和重试 API。
- `local_video_site/web/routes/accounts.py`：登录、账号和用户管理。
- `local_video_site/web/routes/downloads.py`：下载、连续解析和下载任务操作。
- `local_video_site/web/routes/migrations.py`：外盘迁移和格式处理。
- `local_video_site/web/routes/imports.py`：外部视频导入。
- `local_video_site/web/routes/external_scans.py`：外盘扫描和本地扫描。
- `local_video_site/web/routes/transport.py`：下载站点和媒体策略。
- `local_video_site/web/routes/tv.py`：Android TV 只读 API。

`app.py` 不再包含模型、路由实现、中间件注册或生命周期逻辑。

## 数据库

- `local_video_site/db/connection.py`：连接生命周期、事务和关闭。
- `local_video_site/db/transaction.py`：统一时间戳和写事务入口。
- `local_video_site/db/schema.py`：冻结的 Schema 基线及兼容升级函数。
- `local_video_site/db/migrations/`：版本化迁移文件、锁和执行器。
- `local_video_site/repositories/`：按视频、媒体资源、目录、剧集、下载、
  账号、分类、演员、迁移、存储、任务和完整性拆分的 Repository。
- `local_video_site/repositories/collections.py`：视频季/视频集软删除 UnitOfWork。
- `local_video_site/database.py`：仅重新导出兼容接口，不再实现业务 SQL。

迁移版本记录在 `schema_migrations`：

1. 旧 Schema 基线。
2. 运行时完整性检查记录。
3. 后台完整性任务和 Worker 租约。

已经发布的迁移不得修改名称或迁移逻辑；需要继续增加新版本。

迁移 checksum 来自实际 SQL 和 baseline 函数源码，不是版本名称。迁移执行
由 `schema-migrations.lock` 跨进程串行化，避免两个服务进程同时检查和执行。
旧版 identifier-only checksum 会在首次升级时安全迁移到源码 checksum。

Baseline 函数集合由 `local_video_site/db/schema.py` 固定。任何 Schema 变化都
必须增加新迁移，不能继续修改 baseline。

## 后台任务

`local_video_site/jobs/runtime.py` 提供：

- `JobStateMachine`：统一任务状态转换校验。
- `JobResult` / `JobOutcome`：任务处理器显式报告成功、失败、重试或取消。
- `JobRunner`：claim、process、失败记录、轮询和停止。

预览、迁移、外部扫描、格式处理、剪辑和删除复用了该运行时。下载任务和
头像同步仍保留各自的进程级锁与专用循环。

`needs_cleanup` 属于统一的活动状态。Worker 指标会通过 `/api/ready` 暴露，
包括处理数、成功数、失败数、重试数、取消数和最后结果。

## 运行时完整性

`local_video_site/jobs/integrity.py` 执行只读关系检查，并把结果保存到
`runtime_integrity_runs`。检查不会自动删除或修复数据。

完整性检查通过 `runtime_integrity_jobs` 排队，由独立
`runtime-integrity-worker` 异步执行，HTTP 请求只负责创建和查询任务。

## 心跳与租约

`worker_leases` 保存当前 Worker 的任务租约。JobRunner 在任务开始前申请租约，
执行期间异步续租，任务结束后释放。就绪接口同时报告 Worker 心跳年龄、当前任务
持续时间、成功/失败/重试/取消计数。

## 质量门禁

`./scripts/check.sh` 默认执行：

- Shell、Python、JavaScript 语法检查
- Ruff 未定义符号与致命错误检查
- mypy 新架构模块类型检查
- 完整测试
- coverage 分支覆盖率，当前门槛为 55%

管理员接口：

```text
GET  /api/manage/integrity
POST /api/manage/integrity/run
```
