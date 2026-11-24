from __future__ import annotations

import hashlib
import logging
import os
import random
import re
import threading
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlsplit

from local_video_site.integrations.profiles import list_site_profiles, resolve_profile_profile
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from local_video_site.integrations.browser import BrowserSession

from local_video_site.core import config
from local_video_site import database


LOGGER = logging.getLogger(__name__)


class AvatarDownloadError(RuntimeError):
    pass


def _hostname(value: str) -> str:
    try:
        return (urlsplit(value).hostname or "").lower()
    except ValueError:
        return ""


def validate_avatar_url(value: str) -> bool:
    return any(
        profile.matches_avatar_url(value)
        for profile in list_site_profiles()
    )


def validate_profile_url(value: str) -> bool:
    return any(
        profile.matches_profile_url(value)
        for profile in list_site_profiles()
    )


def avatar_url_expires_at(value: str) -> Optional[datetime]:
    try:
        query = urlsplit(str(value or "")).query
    except ValueError:
        return None
    match = re.search(
        r"(?:^|&)secure=[^&]*?(?:,|%2C)([0-9]{10})(?:&|$)",
        query,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    try:
        return datetime.fromtimestamp(int(match.group(1))).astimezone()
    except (OSError, ValueError):
        return None


def avatar_url_is_expired(value: str, leeway_seconds: int = 300) -> bool:
    expires_at = avatar_url_expires_at(value)
    if expires_at is None:
        return False
    return expires_at.timestamp() <= time.time() + max(0, leeway_seconds)


def _failed_avatar_retry_due(job: Dict, delay_seconds: Optional[int]) -> bool:
    if delay_seconds is None:
        return False
    try:
        updated_at = datetime.fromisoformat(str(job.get("updated_at") or ""))
        if updated_at.tzinfo is None:
            updated_at = updated_at.astimezone()
        return (
            datetime.now().astimezone() - updated_at
        ).total_seconds() >= max(0, int(delay_seconds))
    except (TypeError, ValueError):
        return True


def _normalized_actor_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return re.sub(r"[^a-z0-9]+", "", normalized)


def detect_image_extension(
    content: bytes,
    content_type: str = "",
) -> Optional[str]:
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return ".webp"
    if content.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    return {
        "image/webp": ".webp",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
    }.get(media_type)


def avatar_filename(actor_id: int, source_url: str, extension: str) -> str:
    digest = hashlib.sha1(source_url.encode("utf-8")).hexdigest()[:12]
    return "{}-{}{}".format(int(actor_id), digest, extension)


def avatar_file_path(relative_path: str) -> Path:
    return config.ACTOR_AVATAR_DIR / Path(relative_path).name


def store_avatar(
    actor_id: int,
    source_url: str,
    content: bytes,
    content_type: str = "",
) -> str:
    if not content:
        raise AvatarDownloadError("头像响应为空")
    maximum_bytes = config.avatar_max_bytes()
    if len(content) > maximum_bytes:
        raise AvatarDownloadError(
            "头像文件超过 {} 字节".format(maximum_bytes)
        )
    extension = detect_image_extension(content, content_type)
    if extension is None:
        raise AvatarDownloadError(
            "头像响应不是支持的 JPEG、PNG 或 WebP 图片"
        )
    filename = avatar_filename(actor_id, source_url, extension)
    target = avatar_file_path(filename)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return filename


class SiteActorAvatarDownloader(BrowserSession):
    def __init__(
        self,
        port: int = 9333,
        timeout_ms: int = 30000,
        logger: Optional[logging.Logger] = None,
        profile_dir: Optional[Path] = None,
    ) -> None:
        super().__init__(
            port=port,
            timeout_ms=timeout_ms,
            logger=logger,
            profile_dir=profile_dir,
        )
        self.page = None
        self.profile_page = None

    def __enter__(self):
        super().__enter__()
        self.page = self.new_page(block_images=False)
        self.profile_page = self.new_page(block_images=False)
        return self

    def discover_profile_avatar(self, profile_url: str) -> Optional[Dict]:
        if not validate_profile_url(profile_url):
            raise AvatarDownloadError("演员主页地址不合法")
        profile = resolve_profile_profile(profile_url)
        if profile.key == "generic":
            raise AvatarDownloadError("演员主页没有对应的站点适配器")
        response = self.profile_page.goto(
            profile_url,
            wait_until="domcontentloaded",
            timeout=self.timeout_ms,
        )
        if response and response.status >= 400:
            raise AvatarDownloadError(
                "演员主页返回 HTTP {}".format(response.status)
            )
        payload = self.profile_page.evaluate(
            """
            (policy) => {
              const readPath = (path) => path.reduce(
                (value, key) => value?.[key],
                window.initials
              );
              for (const path of policy.embeddedPaths || []) {
                const embedded = readPath(path);
                if (embedded?.thumbUrl) return embedded;
              }
              const script =
                document.querySelector('#initials-script')
                || Array.from(document.scripts || []).find(
                  node => /window\\.initials\\s*=/.test(
                    node.textContent || ''
                  )
                );
              if (script) {
                const match =
                  /window\\.initials\\s*=\\s*/.exec(
                    script.textContent || ''
                  );
                if (match) {
                  try {
                    const initials = JSON.parse(
                      script.textContent.slice(
                        match.index + match[0].length
                      )
                    );
                    for (const path of policy.embeddedPaths || []) {
                      let embedded = initials;
                      for (const key of path) embedded = embedded?.[key];
                      if (embedded?.thumbUrl) return embedded;
                    }
                  } catch (error) {}
                }
              }
              for (const selector of policy.directSelectors || []) {
                const image = document.querySelector(selector);
                if (
                  image
                  && !/profile_default_medium/.test(image.src || '')
                ) {
                  return {thumbUrl: image.src};
                }
              }
              for (const selector of policy.backgroundSelectors || []) {
                const node = document.querySelector(selector);
                const background = node
                  ? getComputedStyle(node).backgroundImage
                  : '';
                const backgroundMatch =
                  background.match(/url\\(["']?(.*?)["']?\\)/);
                if (backgroundMatch) return {thumbUrl: backgroundMatch[1]};
              }
              return null;
            }
            """,
            {
                "directSelectors": list(
                    profile.actor_direct_image_selectors
                ),
                "backgroundSelectors": list(
                    profile.actor_background_image_selectors
                ),
                "embeddedPaths": [
                    list(path) for path in profile.actor_embedded_image_paths
                ],
            },
        )
        if not payload:
            return None
        avatar_url = str(payload.get("thumbUrl") or "").strip()
        if not avatar_url or not validate_avatar_url(avatar_url):
            return None
        return payload

    def discover_xvideos_avatar(
        self,
        actor_name: str,
        source_profile_url: str,
    ) -> Optional[Dict]:
        if self.profile_page is None:
            return None
        try:
            slug = [
                part
                for part in urlsplit(source_profile_url).path.split("/")
                if part
            ][-1]
        except (IndexError, ValueError):
            return None
        name = _normalized_actor_name(actor_name)
        if not slug or not name:
            return None
        for profile_type in ("models", "profiles"):
            url = "https://www.xvideos.com/{}/{}".format(
                profile_type,
                slug,
            )
            try:
                response = self.profile_page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self.timeout_ms,
                )
                if response and response.status >= 400:
                    continue
                payload = self.profile_page.evaluate(
                    """
                    () => ({
                      title: document.title || '',
                      image: document.querySelector(
                        '.profile-pic img[src]'
                      )?.getAttribute('src') || ''
                    })
                    """
                )
            except (PlaywrightTimeoutError, PlaywrightError):
                continue
            image_url = str((payload or {}).get("image") or "").strip()
            title_name = _normalized_actor_name(
                str((payload or {}).get("title") or "")
            )
            if (
                not image_url
                or name not in title_name
                or "profile_default" in image_url
                or not validate_avatar_url(image_url)
            ):
                continue
            return {
                "thumbUrl": image_url,
                "profileUrl": url,
            }
        return None

    def download(self, source_url: str) -> tuple:
        if not validate_avatar_url(source_url):
            raise AvatarDownloadError("头像地址不是受信任的站点 CDN")
        response = self.page.goto(
            source_url,
            wait_until="commit",
            timeout=self.timeout_ms,
        )
        if response is None:
            raise AvatarDownloadError("头像请求没有响应")
        if response.status != 200:
            raise AvatarDownloadError(
                "头像请求返回 HTTP {}".format(response.status)
            )
        content = response.body()
        maximum_bytes = config.avatar_max_bytes()
        if len(content) > maximum_bytes:
            raise AvatarDownloadError(
                "头像文件超过 {} 字节".format(maximum_bytes)
            )
        extension = detect_image_extension(
            content,
            response.headers.get("content-type", ""),
        )
        if extension is None:
            raise AvatarDownloadError(
                "头像响应不是支持的 JPEG、PNG 或 WebP 图片"
            )
        return content, response.headers.get("content-type", "")


XHamsterAvatarDownloader = SiteActorAvatarDownloader


def sync_actor_avatars(
    limit: Optional[int] = None,
    refresh: bool = False,
    retry_failed: bool = False,
    failed_retry_after_seconds: Optional[int] = None,
    actor_id: Optional[int] = None,
    workers: int = 1,
    logger: Optional[logging.Logger] = None,
    downloader_factory=None,
) -> Dict:
    logger = logger or LOGGER
    jobs = database.list_actor_avatar_jobs(actor_id=actor_id)
    candidates = []
    skipped = 0
    for job in jobs:
        source = str(job.get("avatar_source_url") or "").strip()
        path = avatar_file_path(job.get("avatar_path") or "")
        file_ready = bool(job.get("avatar_path") and path.is_file())
        if (
            not refresh
            and job.get("avatar_status") == "unavailable"
        ):
            skipped += 1
            continue
        if (
            not refresh
            and job.get("avatar_status") == "failed"
            and not retry_failed
            and not _failed_avatar_retry_due(
                job,
                failed_retry_after_seconds,
            )
        ):
            skipped += 1
            continue
        if not refresh and file_ready:
            skipped += 1
            continue
        if not source and not validate_profile_url(
            str(job.get("profile_url") or "")
        ):
            skipped += 1
            continue
        candidates.append(job)
        if limit is not None and len(candidates) >= max(0, int(limit)):
            break

    report = {
        "actors": len(jobs),
        "candidates": len(candidates),
        "succeeded": 0,
        "failed": 0,
        "skipped": skipped,
        "workers": 1,
        "requested_workers": max(1, min(2, int(workers))),
        "errors": [],
    }
    if not candidates:
        return report

    factory = downloader_factory or SiteActorAvatarDownloader
    with factory(logger=logger) as downloader:
        for index, job in enumerate(candidates, start=1):
            source = str(job.get("avatar_source_url") or "").strip()
            profile_url = str(job.get("profile_url") or "").strip()
            attempts = 0
            last_error = ""
            discovered = False
            if source and avatar_url_is_expired(source):
                source = ""
            if not source or refresh or avatar_url_is_expired(source):
                try:
                    profile = downloader.discover_profile_avatar(profile_url)
                    discovered = profile is not None
                    if profile:
                        new_source = str(profile.get("thumbUrl") or "").strip()
                        if validate_avatar_url(new_source):
                            source = new_source
                            database.save_actor_metadata(
                                actor_id=job["id"],
                                external_id=job.get("external_id"),
                                avatar_source_url=source,
                            )
                except (
                    AvatarDownloadError,
                    PlaywrightError,
                    PlaywrightTimeoutError,
                ) as exc:
                    last_error = str(exc)

            if (
                not source
                and str(job.get("source_domain") or "") == "xhamster.com"
                and hasattr(downloader, "discover_xvideos_avatar")
            ):
                try:
                    profile = downloader.discover_xvideos_avatar(
                        str(job.get("name") or ""),
                        profile_url,
                    )
                    if profile:
                        new_source = str(
                            profile.get("thumbUrl") or ""
                        ).strip()
                        if validate_avatar_url(new_source):
                            source = new_source
                            discovered = True
                            database.save_actor_metadata(
                                actor_id=job["id"],
                                external_id=job.get("external_id"),
                                avatar_source_url=source,
                            )
                except (
                    AvatarDownloadError,
                    PlaywrightError,
                    PlaywrightTimeoutError,
                ) as exc:
                    last_error = str(exc)

            if source:
                retries = config.avatar_retries()
                for attempt in range(1, retries + 1):
                    attempts += 1
                    try:
                        content, content_type = downloader.download(source)
                        relative_path = store_avatar(
                            actor_id=job["id"],
                            source_url=source,
                            content=content,
                            content_type=content_type,
                        )
                        database.save_actor_avatar_result(
                            actor_id=job["id"],
                            status="fetched",
                            avatar_path=relative_path,
                            attempts=attempts,
                        )
                        report["succeeded"] += 1
                        logger.info(
                            "Actor avatar %s/%s actor=%s name=%s status=fetched size=%s",
                            index,
                            len(candidates),
                            job["id"],
                            job["name"],
                            len(content),
                            extra={"operation": "actor.avatar.fetched"},
                        )
                        last_error = ""
                        break
                    except (
                        AvatarDownloadError,
                        PlaywrightError,
                        PlaywrightTimeoutError,
                        OSError,
                    ) as exc:
                        last_error = str(exc)
                        logger.warning(
                            "Actor avatar attempt failed actor=%s attempt=%s error=%s",
                            job["id"],
                            attempt,
                            last_error,
                            extra={"operation": "actor.avatar.retry"},
                        )
                        if attempt < retries:
                            time.sleep(random.uniform(0.35, 0.9) * attempt)

                if not discovered and profile_url and last_error:
                    try:
                        profile = downloader.discover_profile_avatar(
                            profile_url
                        )
                        refreshed_source = str(
                            (profile or {}).get("thumbUrl") or ""
                        ).strip()
                        if (
                            refreshed_source
                            and refreshed_source != source
                            and validate_avatar_url(refreshed_source)
                        ):
                            source = refreshed_source
                            database.save_actor_metadata(
                                actor_id=job["id"],
                                external_id=job.get("external_id"),
                                avatar_source_url=source,
                            )
                            retries = config.avatar_retries()
                            for attempt in range(1, retries + 1):
                                attempts += 1
                                try:
                                    content, content_type = (
                                        downloader.download(source)
                                    )
                                    relative_path = store_avatar(
                                        actor_id=job["id"],
                                        source_url=source,
                                        content=content,
                                        content_type=content_type,
                                    )
                                    database.save_actor_avatar_result(
                                        actor_id=job["id"],
                                        status="fetched",
                                        avatar_path=relative_path,
                                        attempts=attempts,
                                    )
                                    report["succeeded"] += 1
                                    last_error = ""
                                    break
                                except (
                                    AvatarDownloadError,
                                    PlaywrightError,
                                    PlaywrightTimeoutError,
                                    OSError,
                                ) as exc:
                                    last_error = str(exc)
                                    if attempt < retries:
                                        time.sleep(
                                            random.uniform(0.35, 0.9)
                                            * attempt
                                        )
                    except (
                        AvatarDownloadError,
                        PlaywrightError,
                        PlaywrightTimeoutError,
                    ) as exc:
                        last_error = str(exc)

            if last_error or not source:
                status = (
                    "unavailable"
                    if not source and not last_error
                    else "failed"
                )
                if status == "unavailable":
                    last_error = "源站未提供演员头像"
                database.save_actor_avatar_result(
                    actor_id=job["id"],
                    status=status,
                    error=last_error[:1000],
                    attempts=max(1, attempts),
                )
                if status == "failed":
                    report["failed"] += 1
                    report["errors"].append({
                        "actor_id": job["id"],
                        "name": job["name"],
                        "profile_url": profile_url,
                        "source_url": source or None,
                        "error": last_error,
                    })
                    logger.warning(
                        "Actor avatar %s/%s actor=%s name=%s "
                        "status=failed error=%s",
                        index,
                        len(candidates),
                        job["id"],
                        job["name"],
                        last_error,
                        extra={"operation": "actor.avatar.failed"},
                    )
                else:
                    logger.info(
                        "Actor avatar %s/%s actor=%s name=%s "
                        "status=unavailable",
                        index,
                        len(candidates),
                        job["id"],
                        job["name"],
                        extra={"operation": "actor.avatar.unavailable"},
                    )
            time.sleep(random.uniform(0.05, 0.18))
    return report


class ActorAvatarWorker:
    def __init__(
        self,
        stop_event: threading.Event,
        logger: Optional[logging.Logger] = None,
        batch_size: Optional[int] = None,
        interval_seconds: Optional[int] = None,
        failed_retry_after_seconds: Optional[int] = None,
    ) -> None:
        self.stop_event = stop_event
        self.logger = logger or LOGGER
        self.batch_size = max(
            1,
            int(
                batch_size
                if batch_size is not None
                else config.actor_avatar_sync_batch_size()
            ),
        )
        self.interval_seconds = max(
            5,
            int(
                interval_seconds
                if interval_seconds is not None
                else config.actor_avatar_sync_interval_seconds()
            ),
        )
        self.failed_retry_after_seconds = max(
            60,
            int(
                failed_retry_after_seconds
                if failed_retry_after_seconds is not None
                else config.actor_avatar_failed_retry_seconds()
            ),
        )
        self.thread: Optional[threading.Thread] = None

    def start(self) -> "ActorAvatarWorker":
        self.thread = threading.Thread(
            target=self._loop,
            name="actor-avatar-worker",
            daemon=True,
        )
        self.thread.start()
        return self

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=20)

    def run_once(self) -> Dict:
        return sync_actor_avatars(
            limit=self.batch_size,
            failed_retry_after_seconds=self.failed_retry_after_seconds,
            workers=1,
            logger=self.logger,
        )

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                report = self.run_once()
                self.logger.info(
                    "Actor avatar sync batch candidates=%s succeeded=%s "
                    "failed=%s skipped=%s",
                    report["candidates"],
                    report["succeeded"],
                    report["failed"],
                    report["skipped"],
                    extra={"operation": "actor.avatar.batch"},
                )
                delay = (
                    2
                    if report["candidates"] >= self.batch_size
                    else self.interval_seconds
                )
            except Exception:
                self.logger.exception(
                    "Actor avatar sync worker failed",
                    extra={"operation": "actor.avatar.worker_failed"},
                )
                delay = self.interval_seconds
            self.stop_event.wait(delay)
