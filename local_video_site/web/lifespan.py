from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from local_video_site.core import config
from local_video_site import database
from local_video_site.catalog.actors import ActorAvatarWorker
from local_video_site.catalog.categories import refresh_site_metadata
from local_video_site.storage.deletion import delete_password_configured, ensure_delete_password
from local_video_site.identity.bootstrap import ensure_bootstrap_admin
from local_video_site.downloads.tasks import DownloadTaskWorker, backfill_download_task_media_info, backfill_download_task_transport_info
from local_video_site.ingestion.external_scan import ExternalScanWorker, recover_interrupted_scans
from local_video_site.jobs.integrity import latest_integrity_report
from local_video_site.jobs.integrity_tasks import IntegrityWorker, create_integrity_job, recover_interrupted_integrity_jobs
from local_video_site.storage.migration_tasks import MigrationWorker, recover_interrupted_migrations
from local_video_site.media.playback.engine import prune_playback_cache
from local_video_site.media.assets.previews import PreviewWorker
from local_video_site.media.editing.rename import recover_interrupted_renames
from local_video_site.ingestion.library_scan import SCAN_RUNTIME, ScanAlreadyRunning, ScanStats, backfill_episode_technical_metadata, process_pending_metadata, process_pending_until_empty, repair_missing_thumbnails, scan_library
from local_video_site.downloads.policies.transport import backfill_task_site_keys, rebuild_transport_stats_from_tasks
from local_video_site.storage.deletion_tasks import VideoDeleteWorker, recover_interrupted_delete_jobs
from local_video_site.media.editing.format import VideoFormatWorker, recover_interrupted_jobs as recover_interrupted_format_jobs
from local_video_site.ingestion.import_tasks import recover_interrupted_imports
from local_video_site.storage.volumes import StorageVolumeWorker
from local_video_site.media.editing.trim import VideoTrimWorker, reconcile_trim_backups, recover_interrupted_trim_jobs
from local_video_site.settings import get_runtime_settings


LOGGER = logging.getLogger("local_video_site")


def _metadata_worker(stop_event: threading.Event) -> None:
    try:
        process_pending_until_empty(logger=LOGGER, stop_event=stop_event)
    except Exception:
        LOGGER.exception("Background metadata indexing failed")


def _category_source_worker(stop_event: threading.Event) -> None:
    try:
        report = refresh_site_metadata(
            refresh=False,
            retry_failed=False,
            logger=LOGGER,
            stop_event=stop_event,
        )
        if report.get("succeeded"):
            LOGGER.info(
                "Site metadata refresh: succeeded=%s failed=%s "
                "categories=%s actors=%s",
                report.get("succeeded", 0),
                report.get("failed", 0),
                report.get("categories", 0),
                report.get("actors", 0),
            )
    except Exception:
        LOGGER.exception("Background category source refresh failed")


def _resource_finalize_worker(
    metadata_threads: list,
    stop_event: threading.Event,
) -> None:
    try:
        for thread in metadata_threads:
            thread.join()
        if stop_event.is_set():
            return
        repaired = repair_missing_thumbnails(
            logger=LOGGER,
            stop_event=stop_event,
        )
        if repaired:
            LOGGER.info("Missing thumbnails repaired: %s", repaired)
        SCAN_RUNTIME.resources_complete()
    except Exception:
        message = "Background resource finalization failed"
        SCAN_RUNTIME.fail(message)
        LOGGER.exception(message)


def _start_background_indexing(
    stop_event: threading.Event,
    workers: int = 2,
) -> list:
    threads = []
    for index in range(max(1, workers)):
        thread = threading.Thread(
            target=_metadata_worker,
            args=(stop_event,),
            name="video-metadata-indexer-{}".format(index + 1),
            daemon=True,
        )
        thread.start()
        threads.append(thread)
    if config.category_fetch_on_startup():
        category_thread = threading.Thread(
            target=_category_source_worker,
            args=(stop_event,),
            name="video-category-source",
            daemon=True,
        )
        category_thread.start()
        threads.append(category_thread)
    finalize_thread = threading.Thread(
        target=_resource_finalize_worker,
        args=(list(threads), stop_event),
        name="video-resource-finalizer",
        daemon=True,
    )
    finalize_thread.start()
    threads.append(finalize_thread)
    return threads


def _scan_index() -> ScanStats:
    roots = config.configured_roots()
    if not SCAN_RUNTIME.try_start(roots):
        raise ScanAlreadyRunning("A library scan is already running")
    LOGGER.info(
        "Library scan started: roots=%s",
        len(roots),
        extra={"operation": "scan.start"},
    )
    try:
        stats = scan_library(roots=roots)
    except Exception as exc:
        SCAN_RUNTIME.fail(str(exc))
        LOGGER.exception(
            "Library scan failed",
            extra={"operation": "scan.failed"},
        )
        raise
    SCAN_RUNTIME.index_complete(stats)
    LOGGER.info(
        "Library scan completed: discovered=%s added=%s changed=%s "
        "missing=%s categories_rebuilt=%s",
        stats.discovered,
        stats.added,
        stats.changed,
        stats.missing,
        stats.categories_rebuilt,
        extra={"operation": "scan.complete"},
    )
    return stats


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.lifespan_ready = False
    metadata_stop_event = threading.Event()
    app.state.metadata_stop_event = metadata_stop_event
    download_stop_event = threading.Event()
    app.state.download_stop_event = download_stop_event
    preview_stop_event = threading.Event()
    app.state.preview_stop_event = preview_stop_event
    actor_avatar_stop_event = threading.Event()
    app.state.actor_avatar_stop_event = actor_avatar_stop_event
    migration_stop_event = threading.Event()
    app.state.migration_stop_event = migration_stop_event
    external_scan_stop_event = threading.Event()
    app.state.external_scan_stop_event = external_scan_stop_event
    storage_volume_stop_event = threading.Event()
    app.state.storage_volume_stop_event = storage_volume_stop_event
    trim_stop_event = threading.Event()
    app.state.trim_stop_event = trim_stop_event
    delete_stop_event = threading.Event()
    app.state.delete_stop_event = delete_stop_event
    integrity_stop_event = threading.Event()
    app.state.integrity_stop_event = integrity_stop_event
    format_stop_event = threading.Event()
    app.state.format_stop_event = format_stop_event
    database.init_db()
    bootstrap = ensure_bootstrap_admin()
    app.state.bootstrap_admin = bootstrap
    if bootstrap["status"] == "created":
        LOGGER.warning(
            "Default administrator created username=%s "
            "must_change_password=true",
            bootstrap["username"],
            extra={"operation": "auth.bootstrap.default_admin"},
        )
    app.state.settings = get_runtime_settings()
    app.state.settings.refresh()
    try:
        recovered_trims = recover_interrupted_trim_jobs()
        if recovered_trims:
            LOGGER.warning(
                "Interrupted video trims recovered count=%s",
                recovered_trims,
                extra={"operation": "video.trim.recovery"},
            )
    except Exception:
        LOGGER.exception("Interrupted video trim recovery failed")
    try:
        reconciled_backups = reconcile_trim_backups()
        if reconciled_backups:
            LOGGER.info(
                "Video trim backups reconciled count=%s",
                reconciled_backups,
                extra={"operation": "video.trim.backup.reconcile"},
            )
    except Exception:
        LOGGER.exception("Video trim backup reconciliation failed")
    try:
        batch_selection_repair = (
            database.repair_confirmed_episode_batch_selections()
        )
        if (
            batch_selection_repair["selected_batches"]
            or batch_selection_repair["episodes_removed"]
        ):
            LOGGER.info(
                "Episode batch selections repaired batches=%s items=%s "
                "planned_episodes_removed=%s",
                batch_selection_repair["selected_batches"],
                batch_selection_repair["selected_items"],
                batch_selection_repair["episodes_removed"],
                extra={
                    "operation": "episode.batch.selection_repair"
                },
            )
    except Exception:
        LOGGER.exception("Episode batch selection repair failed")
    recover_interrupted_migrations()
    recover_interrupted_imports()
    recover_interrupted_scans()
    try:
        recovered_delete_jobs = recover_interrupted_delete_jobs()
        if recovered_delete_jobs:
            LOGGER.warning(
                "Interrupted delete jobs recovered count=%s",
                recovered_delete_jobs,
                extra={"operation": "video.delete.recovery"},
            )
    except Exception:
        LOGGER.exception("Interrupted video delete job recovery failed")
    try:
        recovered_format_jobs = recover_interrupted_format_jobs()
        if recovered_format_jobs:
            LOGGER.warning(
                "Interrupted video format jobs recovered count=%s",
                recovered_format_jobs,
                extra={"operation": "video.format.recovery"},
            )
    except Exception:
        LOGGER.exception("Interrupted video format job recovery failed")
    try:
        backfilled_sites = backfill_task_site_keys()
        if backfilled_sites:
            LOGGER.info(
                "Transport task sites backfilled count=%s",
                backfilled_sites,
                extra={"operation": "transport.site.backfill"},
            )
        transport_backfill = backfill_download_task_transport_info(LOGGER)
        if transport_backfill["tasks"]:
            LOGGER.info(
                "Transport kinds backfilled tasks=%s hls=%s direct_range=%s "
                "ytdlp=%s unknown=%s hls_progress=%s",
                transport_backfill["tasks"],
                transport_backfill["hls"],
                transport_backfill["direct-range"],
                transport_backfill["yt-dlp"],
                transport_backfill["unknown"],
                transport_backfill["hls_progress"],
                extra={"operation": "transport.kind.backfill"},
            )
        rebuilt_sites = rebuild_transport_stats_from_tasks()
        LOGGER.info(
            "Transport stats rebuilt sites=%s",
            rebuilt_sites,
            extra={"operation": "transport.stats.rebuild"},
        )
    except Exception:
        LOGGER.exception("Transport site backfill failed")
    ensure_delete_password()
    if not delete_password_configured():
        LOGGER.warning(
            "Delete verification is not configured; destructive actions "
            "will fail until an administrator sets it in parameter settings",
            extra={"operation": "delete_verification.unconfigured"},
        )
    database.recover_interrupted_metadata()
    database.recover_download_tasks()
    database.recover_interrupted_previews()
    try:
        repaired_episode_titles = (
            database.repair_generic_episode_titles_from_paths()
        )
        if repaired_episode_titles:
            LOGGER.info(
                "Generic episode titles repaired from file names count=%s",
                repaired_episode_titles,
                extra={"operation": "episode.title.path_repair"},
            )
    except Exception:
        LOGGER.exception("Episode title path repair failed")
    try:
        episode_technical = backfill_episode_technical_metadata(LOGGER)
        if episode_technical:
            LOGGER.info(
                "Episode technical metadata backfilled videos=%s",
                episode_technical,
                extra={"operation": "episode.technical.backfill"},
            )
    except Exception:
        LOGGER.exception("Episode technical metadata backfill failed")
    try:
        recovered_renames = recover_interrupted_renames(LOGGER)
        if recovered_renames:
            LOGGER.info(
                "Interrupted video renames recovered=%s",
                recovered_renames,
                extra={"operation": "video.rename.recovery"},
            )
    except Exception:
        LOGGER.exception("Interrupted video rename recovery failed")
    try:
        media_backfill = backfill_download_task_media_info(LOGGER)
        LOGGER.info(
            "Download task media backfill completed tasks=%s videos=%s "
            "summaries=%s ffprobe=%s unknown=%s",
            media_backfill["tasks"],
            media_backfill["from_videos"],
            media_backfill["from_summaries"],
            media_backfill["from_ffprobe"],
            media_backfill["unknown"],
            extra={"operation": "download.task.media_backfill"},
        )
    except Exception:
        LOGGER.exception("Download task media backfill failed")
    try:
        repaired_metadata_messages = (
            database.repair_inherited_metadata_task_messages()
        )
        if repaired_metadata_messages:
            LOGGER.info(
                "Inherited metadata task messages repaired count=%s",
                repaired_metadata_messages,
                extra={
                    "operation": "download.task.metadata.message_repair"
                },
            )
    except Exception:
        LOGGER.exception(
            "Inherited metadata task message repair failed"
        )
    app.state.metadata_threads = []
    if config.scan_on_startup():
        try:
            _scan_index()
            if config.scan_wait_for_resources():
                processed = process_pending_metadata(
                    logger=LOGGER,
                    max_workers=2,
                )
                repaired = repair_missing_thumbnails(logger=LOGGER)
                LOGGER.info(
                    "Startup resources completed: metadata=%s thumbnails_repaired=%s",
                    processed,
                    repaired,
                )
                SCAN_RUNTIME.resources_complete()
            else:
                app.state.metadata_threads = _start_background_indexing(
                    metadata_stop_event
                )
        except ScanAlreadyRunning:
            LOGGER.warning("Startup scan skipped because another scan is running")
        except Exception:
            LOGGER.exception("Startup scan failed; using the existing database")
    else:
        LOGGER.info("Startup scan disabled by LOCAL_VIDEO_SCAN_ON_STARTUP")
    try:
        cache_report = prune_playback_cache(logger=LOGGER)
        LOGGER.info(
            "Playback cache maintenance completed files=%s removed=%s "
            "bytes_after=%s",
            cache_report["files"],
            cache_report["removed_files"],
            cache_report["total_bytes_after"],
            extra={"operation": "playback.cache.maintenance"},
        )
    except Exception:
        LOGGER.exception("Playback cache maintenance failed")
    try:
        recovered_integrity_jobs = recover_interrupted_integrity_jobs()
        startup_integrity_job = create_integrity_job("startup")
        app.state.integrity_job_id = startup_integrity_job.get("job_id", "")
        app.state.integrity_report = latest_integrity_report()
        if recovered_integrity_jobs:
            LOGGER.warning(
                "Interrupted integrity jobs recovered count=%s",
                recovered_integrity_jobs,
                extra={"operation": "runtime.integrity.recovery"},
            )
    except Exception:
        app.state.integrity_report = {}
        app.state.integrity_job_id = ""
        LOGGER.exception("Could not queue startup integrity check")
    app.state.integrity_worker = None
    if config.integrity_worker_enabled():
        app.state.integrity_worker = IntegrityWorker(
            stop_event=integrity_stop_event,
            logger=LOGGER,
        ).start()
        LOGGER.info(
            "Runtime integrity worker started",
            extra={"operation": "runtime.integrity.worker.started"},
        )
    else:
        LOGGER.info(
            "Runtime integrity worker disabled by "
            "LOCAL_VIDEO_INTEGRITY_WORKER"
        )
    app.state.download_worker = None
    if config.download_worker_enabled():
        app.state.download_worker = DownloadTaskWorker(
            stop_event=download_stop_event,
            logger=LOGGER,
        ).start()
        if app.state.download_worker.thread is not None:
            LOGGER.info(
                "Download task worker started",
                extra={"operation": "download.worker.started"},
            )
    else:
        LOGGER.info(
            "Download task worker disabled by LOCAL_VIDEO_DOWNLOAD_WORKER"
        )
    app.state.preview_worker = PreviewWorker(
        stop_event=preview_stop_event,
        logger=LOGGER,
    ).start()
    app.state.actor_avatar_worker = None
    if config.actor_avatar_sync_enabled():
        app.state.actor_avatar_worker = ActorAvatarWorker(
            stop_event=actor_avatar_stop_event,
            logger=LOGGER,
        ).start()
        LOGGER.info(
            "Actor avatar worker started",
            extra={"operation": "actor.avatar.worker.started"},
        )
    else:
        LOGGER.info(
            "Actor avatar worker disabled by LOCAL_VIDEO_ACTOR_AVATAR_SYNC"
        )
    app.state.migration_worker = None
    if config.migration_worker_enabled():
        app.state.migration_worker = MigrationWorker(
            stop_event=migration_stop_event,
            logger=LOGGER,
        ).start()
        if app.state.migration_worker.thread is not None:
            LOGGER.info(
                "Video migration worker started",
                extra={"operation": "video.migration.worker.started"},
            )
    else:
        LOGGER.info(
            "Video migration worker disabled by LOCAL_VIDEO_MIGRATION_WORKER"
        )
    app.state.external_scan_worker = None
    if config.external_scan_worker_enabled():
        app.state.external_scan_worker = ExternalScanWorker(
            stop_event=external_scan_stop_event,
            logger=LOGGER,
        ).start()
        LOGGER.info(
            "External video scan worker started",
            extra={"operation": "external.scan.worker.started"},
        )
    else:
        LOGGER.info(
            "External video scan worker disabled by "
            "LOCAL_VIDEO_EXTERNAL_SCAN_WORKER"
        )
    app.state.storage_volume_worker = StorageVolumeWorker(
        stop_event=storage_volume_stop_event,
        logger=LOGGER,
    ).start()
    LOGGER.info(
        "Storage volume worker started",
        extra={"operation": "storage.volume.worker.started"},
    )
    app.state.trim_worker = None
    if config.video_trim_worker_enabled():
        app.state.trim_worker = VideoTrimWorker(
            stop_event=trim_stop_event,
            logger=LOGGER,
        ).start()
        LOGGER.info(
            "Video trim worker started",
            extra={"operation": "video.trim.worker.started"},
        )
    else:
        LOGGER.info(
            "Video trim worker disabled by LOCAL_VIDEO_TRIM_WORKER"
        )
    app.state.format_worker = None
    if config.video_format_worker_enabled():
        app.state.format_worker = VideoFormatWorker(
            stop_event=format_stop_event,
            logger=LOGGER,
        ).start()
        LOGGER.info(
            "Video format worker started",
            extra={"operation": "video.format.worker.started"},
        )
    else:
        LOGGER.info(
            "Video format worker disabled by LOCAL_VIDEO_FORMAT_WORKER"
        )
    app.state.delete_worker = None
    if config.video_delete_worker_enabled():
        app.state.delete_worker = VideoDeleteWorker(
            stop_event=delete_stop_event,
            logger=LOGGER,
        ).start()
        LOGGER.info(
            "Video delete worker started",
            extra={"operation": "video.delete.worker.started"},
        )
    else:
        LOGGER.info(
            "Video delete worker disabled by LOCAL_VIDEO_DELETE_WORKER"
        )
    app.state.lifespan_ready = True
    LOGGER.info(
        "Service ready host=%s mode=%s",
        config.configured_roots(),
        "lan" if config.lan_mode() else "local",
        extra={"operation": "service.ready"},
    )
    try:
        yield
    finally:
        app.state.lifespan_ready = False
        metadata_stop_event.set()
        download_stop_event.set()
        preview_stop_event.set()
        actor_avatar_stop_event.set()
        migration_stop_event.set()
        external_scan_stop_event.set()
        storage_volume_stop_event.set()
        trim_stop_event.set()
        format_stop_event.set()
        delete_stop_event.set()
        integrity_stop_event.set()
        worker = getattr(app.state, "download_worker", None)
        if worker is not None:
            worker.stop()
        preview_worker = getattr(app.state, "preview_worker", None)
        if preview_worker is not None:
            preview_worker.stop()
        actor_avatar_worker = getattr(app.state, "actor_avatar_worker", None)
        if actor_avatar_worker is not None:
            actor_avatar_worker.stop()
        migration_worker = getattr(app.state, "migration_worker", None)
        if migration_worker is not None:
            migration_worker.stop()
        external_scan_worker = getattr(
            app.state,
            "external_scan_worker",
            None,
        )
        if external_scan_worker is not None:
            external_scan_worker.stop()
        storage_volume_worker = getattr(
            app.state,
            "storage_volume_worker",
            None,
        )
        if storage_volume_worker is not None:
            storage_volume_worker.stop()
        trim_worker = getattr(app.state, "trim_worker", None)
        if trim_worker is not None:
            trim_worker.stop()
        format_worker = getattr(app.state, "format_worker", None)
        if format_worker is not None:
            format_worker.stop()
        delete_worker = getattr(app.state, "delete_worker", None)
        if delete_worker is not None:
            delete_worker.stop()
        integrity_worker = getattr(app.state, "integrity_worker", None)
        if integrity_worker is not None:
            integrity_worker.stop()
        LOGGER.info(
            "Service stopping",
            extra={"operation": "service.stop"},
        )
