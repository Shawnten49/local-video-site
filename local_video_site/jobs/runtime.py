from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from local_video_site.core import config
from local_video_site.jobs.leases import job_lease_manager


ACTIVE_STATUSES = frozenset(
    {"pending", "running", "blocked", "needs_cleanup"}
)
TERMINAL_STATUSES = frozenset({"success", "failed", "cancelled"})


class InvalidJobTransition(ValueError):
    pass


class JobOutcome(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    RETRY = "retry"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class JobResult:
    outcome: JobOutcome
    message: str = ""

    @classmethod
    def from_status(cls, status: str) -> "JobResult":
        normalized = str(status or "").lower()
        if normalized == "success":
            return cls(JobOutcome.SUCCESS)
        if normalized == "failed":
            return cls(JobOutcome.FAILED)
        if normalized in {"pending", "blocked", "needs_cleanup"}:
            return cls(JobOutcome.RETRY)
        if normalized == "cancelled":
            return cls(JobOutcome.CANCELLED)
        return cls(JobOutcome.SUCCESS)


class JobStateMachine:
    _TRANSITIONS = {
        "pending": {"running", "blocked", "failed", "cancelled"},
        "blocked": {"pending", "failed", "cancelled"},
        "running": {
            "success",
            "failed",
            "cancelled",
            "pending",
            "needs_cleanup",
        },
        "needs_cleanup": {
            "pending",
            "running",
            "success",
            "failed",
            "cancelled",
        },
        "failed": {"pending"},
        "cancelled": {"pending"},
        "success": set(),
    }

    @classmethod
    def validate(cls, current: str, target: str) -> None:
        current = str(current or "pending")
        target = str(target or "")
        allowed = cls._TRANSITIONS.get(current)
        if allowed is None:
            raise InvalidJobTransition(
                "Unknown job state: {}".format(current)
            )
        if target not in allowed and target != current:
            raise InvalidJobTransition(
                "Invalid job transition: {} -> {}".format(current, target)
            )

    @classmethod
    def is_active(cls, status: str) -> bool:
        return str(status or "") in ACTIVE_STATUSES

    @classmethod
    def is_terminal(cls, status: str) -> bool:
        return str(status or "") in TERMINAL_STATUSES


@dataclass
class JobRunnerSnapshot:
    name: str
    running: bool
    processed: int
    succeeded: int
    failures: int
    retries: int
    cancelled: int
    last_job_id: str
    last_outcome: str
    last_heartbeat_at: float
    current_job_age_seconds: float


class JobRunner:
    def __init__(
        self,
        *,
        name: str,
        stop_event: threading.Event,
        claim_job: Callable,
        process_job: Callable,
        poll_seconds: Callable[[], float],
        logger: Optional[logging.Logger] = None,
        lease_manager=None,
        enable_leases: bool = True,
    ) -> None:
        self.name = str(name)
        self.stop_event = stop_event
        self.claim_job = claim_job
        self.process_job = process_job
        self.poll_seconds = poll_seconds
        self.logger = logger or logging.getLogger(__name__)
        self.lease_manager = (
            lease_manager
            if lease_manager is not None
            else job_lease_manager if enable_leases else None
        )
        self.thread: Optional[threading.Thread] = None
        self._metrics_lock = threading.Lock()
        self._processed = 0
        self._succeeded = 0
        self._failures = 0
        self._retries = 0
        self._cancelled = 0
        self._last_job_id = ""
        self._last_outcome = ""
        self._last_heartbeat = time.monotonic()
        self._current_job_started = 0.0

    def start(self) -> "JobRunner":
        self.thread = threading.Thread(
            target=self.run,
            name=self.name,
            daemon=True,
        )
        self.thread.start()
        return self

    def run(self) -> None:
        while not self.stop_event.is_set():
            self._heartbeat()
            job = self.claim_job()
            if job is None:
                self._heartbeat()
                self.stop_event.wait(max(0.05, float(self.poll_seconds())))
                continue
            identity_job = (
                job.get("payload")
                if isinstance(job, dict)
                and isinstance(job.get("payload"), dict)
                else job
            )
            if not isinstance(identity_job, dict):
                identity_job = {}
            job_id = str(
                identity_job.get("job_id")
                or identity_job.get("scan_id")
                or identity_job.get("id")
                or identity_job.get("migration_id")
                or ""
            )
            with self._metrics_lock:
                self._last_job_id = job_id
                self._current_job_started = time.monotonic()
            lease = None
            lease_stop = None
            lease_manager = self.lease_manager
            if lease_manager is not None and job_id:
                if not lease_manager.acquire(self.name, job_id):
                    self.logger.warning(
                        "Job lease unavailable name=%s job_id=%s",
                        self.name,
                        job_id,
                    )
                    with self._metrics_lock:
                        self._current_job_started = 0.0
                    self._heartbeat()
                    continue
                lease = job_id
                lease_stop = threading.Event()
                threading.Thread(
                    target=self._lease_heartbeat,
                    args=(job_id, lease_stop),
                    name="{}-lease-heartbeat".format(self.name),
                    daemon=True,
                ).start()
            try:
                result = self.process_job(job)
            except Exception:
                with self._metrics_lock:
                    self._processed += 1
                    self._failures += 1
                    self._last_outcome = JobOutcome.FAILED.value
                    self._current_job_started = 0.0
                    self._last_heartbeat = time.monotonic()
                self.logger.exception(
                    "Job runner failed name=%s job_id=%s",
                    self.name,
                    job_id,
                )
                continue
            finally:
                if lease_stop is not None:
                    lease_stop.set()
                if lease is not None and lease_manager is not None:
                    lease_manager.release(self.name, lease)
            normalized_result = (
                result
                if isinstance(result, JobResult)
                else JobResult(JobOutcome.SUCCESS)
            )
            self._record_result(job_id, normalized_result)

    def _heartbeat(self) -> None:
        with self._metrics_lock:
            self._last_heartbeat = time.monotonic()

    def _lease_heartbeat(
        self,
        job_id: str,
        stop_event: threading.Event,
    ) -> None:
        interval = max(1.0, min(30.0, config.job_lease_seconds() / 3.0))
        lease_manager = self.lease_manager
        if lease_manager is None:
            return
        while not stop_event.wait(interval):
            if not lease_manager.renew(self.name, job_id):
                self.logger.warning(
                    "Job lease renewal failed name=%s job_id=%s",
                    self.name,
                    job_id,
                )
                return
            self._heartbeat()

    def _record_result(
        self,
        job_id: str,
        result: JobResult,
    ) -> None:
        with self._metrics_lock:
            self._processed += 1
            self._last_outcome = result.outcome.value
            self._current_job_started = 0.0
            if result.outcome == JobOutcome.SUCCESS:
                self._succeeded += 1
            elif result.outcome == JobOutcome.FAILED:
                self._failures += 1
            elif result.outcome == JobOutcome.RETRY:
                self._retries += 1
            elif result.outcome == JobOutcome.CANCELLED:
                self._cancelled += 1
        if result.outcome in {JobOutcome.FAILED, JobOutcome.RETRY}:
            self.logger.warning(
                "Job runner outcome name=%s job_id=%s outcome=%s message=%s",
                self.name,
                job_id,
                result.outcome.value,
                result.message,
            )

    def stop(self, timeout: float = 15.0) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=max(0.0, float(timeout)))

    def snapshot(self) -> JobRunnerSnapshot:
        thread = self.thread
        with self._metrics_lock:
            return JobRunnerSnapshot(
                name=self.name,
                running=bool(thread and thread.is_alive()),
                processed=self._processed,
                succeeded=self._succeeded,
                failures=self._failures,
                retries=self._retries,
                cancelled=self._cancelled,
                last_job_id=self._last_job_id,
                last_outcome=self._last_outcome,
                last_heartbeat_at=self._last_heartbeat,
                current_job_age_seconds=(
                    max(0.0, time.monotonic() - self._current_job_started)
                    if self._current_job_started
                    else 0.0
                ),
            )
