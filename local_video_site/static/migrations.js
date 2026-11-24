(() => {
  const notice = document.getElementById("migration-notice");
  const tabs = Array.from(document.querySelectorAll(".storage-task-tab"));

  function showNotice(message, error = false) {
    if (!notice) return;
    notice.hidden = false;
    notice.textContent = message;
    notice.classList.toggle("is-error", error);
  }

  async function post(url) {
    const response = await fetch(url, { method: "POST" });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }
    return response.json();
  }

  tabs.forEach((tab, index) => {
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
        return;
      }
      event.preventDefault();
      let targetIndex = index;
      if (event.key === "ArrowLeft") {
        targetIndex = (index - 1 + tabs.length) % tabs.length;
      } else if (event.key === "ArrowRight") {
        targetIndex = (index + 1) % tabs.length;
      } else if (event.key === "Home") {
        targetIndex = 0;
      } else if (event.key === "End") {
        targetIndex = tabs.length - 1;
      }
      tabs[targetIndex]?.focus();
      tabs[targetIndex]?.click();
    });
  });

  document.addEventListener("click", async (event) => {
    const retry = event.target.closest("[data-retry-migration]");
    const cancel = event.target.closest("[data-cancel-migration]");
    const formatRetry = event.target.closest("[data-retry-format-job]");
    const formatCancel = event.target.closest("[data-cancel-format-job]");
    const restoreFormatBackup = event.target.closest(
      "[data-restore-format-backup]",
    );
    const deleteFormatBackup = event.target.closest(
      "[data-delete-format-backup]",
    );
    if (retry) {
      try {
        await post(`/api/manage/migrations/${retry.dataset.retryMigration}/retry`);
        showNotice("任务已重新排队");
        window.setTimeout(() => window.location.reload(), 500);
      } catch (error) {
        showNotice(error.message, true);
      }
      return;
    }
    if (cancel) {
      try {
        await post(`/api/manage/migrations/${cancel.dataset.cancelMigration}/cancel`);
        showNotice("已请求取消迁移任务");
        window.setTimeout(() => window.location.reload(), 500);
      } catch (error) {
        showNotice(error.message, true);
      }
      return;
    }
    if (formatRetry) {
      try {
        await post(
          `/api/manage/format-jobs/${formatRetry.dataset.retryFormatJob}/retry`,
        );
        showNotice("格式任务已重新排队");
        window.setTimeout(() => window.location.reload(), 500);
      } catch (error) {
        showNotice(error.message, true);
      }
      return;
    }
    if (restoreFormatBackup) {
      if (!window.confirm("使用安全备份恢复原始 MKV？当前 MP4 将被移除。")) {
        return;
      }
      try {
        await post(
          `/api/manage/format-jobs/${restoreFormatBackup.dataset.restoreFormatBackup}/restore`,
        );
        showNotice("原始 MKV 已恢复");
        window.setTimeout(() => window.location.reload(), 500);
      } catch (error) {
        showNotice(error.message, true);
      }
      return;
    }
    if (deleteFormatBackup) {
      if (!window.confirm("永久删除安全备份？删除后无法恢复原始 MKV。")) {
        return;
      }
      try {
        await post(
          `/api/manage/format-jobs/${deleteFormatBackup.dataset.deleteFormatBackup}/backup/delete`,
        );
        showNotice("安全备份已删除");
        window.setTimeout(() => window.location.reload(), 500);
      } catch (error) {
        showNotice(error.message, true);
      }
      return;
    }
    if (formatCancel) {
      try {
        await post(
          `/api/manage/format-jobs/${formatCancel.dataset.cancelFormatJob}/cancel`,
        );
        showNotice(
          formatCancel.closest("tr")?.dataset.formatActive === "1"
            ? "已请求取消格式任务"
            : "格式任务已取消",
        );
        window.setTimeout(() => window.location.reload(), 500);
      } catch (error) {
        showNotice(error.message, true);
      }
    }
  });

  const liveMigrationRows = [
    ...document.querySelectorAll('[data-migration-row][data-live="1"]'),
  ];
  if (liveMigrationRows.length) {
    async function refreshMigrations() {
      if (document.visibilityState === "hidden") return;
      const ids = liveMigrationRows
        .filter((row) => row.dataset.live === "1")
        .map((row) => row.dataset.migrationRow)
        .join(",");
      if (!ids) return;
      try {
        const response = await fetch(
          `/api/manage/migrations/live?ids=${encodeURIComponent(ids)}`,
          { cache: "no-store" },
        );
        if (!response.ok) return;
        const payload = await response.json();
        for (const item of payload.items) {
          const row = document.querySelector(
            `[data-migration-row="${item.id}"]`,
          );
          if (!row) continue;
          const status = row.querySelector('[data-field="status"]');
          const stage = row.querySelector('[data-field="stage"]');
          const bar = row.querySelector('[data-field="progress-bar"]');
          const text = row.querySelector('[data-field="progress-text"]');
          if (status) {
            status.textContent = item.status_label;
            status.className = `migration-status status-${item.status}`;
          }
          if (stage) stage.textContent = item.stage_label;
          if (bar) bar.style.width = `${item.progress_percent}%`;
          if (text) {
            text.textContent = `${item.progress_percent.toFixed(1)}%`;
          }
          if (["pending", "running", "needs_cleanup"].includes(item.status)) {
            row.dataset.live = "1";
          } else {
            row.dataset.live = "0";
            window.setTimeout(() => window.location.reload(), 700);
          }
        }
      } catch (_) {
        // Retry on the next interval without replacing the visible table.
      }
    }
    refreshMigrations();
    window.setInterval(refreshMigrations, 2500);
  }

  const liveImportRows = [
    ...document.querySelectorAll('[data-import-job-row][data-import-active="1"]'),
  ];
  if (liveImportRows.length) {
    async function refreshImports() {
      if (document.visibilityState === "hidden") return;
      const ids = liveImportRows
        .filter((row) => row.dataset.importActive === "1")
        .map((row) => row.dataset.importJobRow)
        .join(",");
      if (!ids) return;
      try {
        const response = await fetch(
          `/api/manage/import/jobs/live?ids=${encodeURIComponent(ids)}`,
          { cache: "no-store" },
        );
        if (!response.ok) return;
        const payload = await response.json();
        for (const item of payload.items) {
          const row = document.querySelector(
            `[data-import-job-row="${item.id}"]`,
          );
          if (!row) continue;
          const status = row.querySelector('[data-field="import-status"]');
          const stage = row.querySelector('[data-field="import-stage"]');
          const error = row.querySelector('[data-field="import-error"]');
          const metadata = row.querySelector('[data-field="import-metadata"]');
          const thumbnail = row.querySelector('[data-field="import-thumbnail"]');
          const preview = row.querySelector('[data-field="import-preview"]');
          const video = row.querySelector('[data-field="import-video"]');
          if (status) {
            status.textContent = item.status_label;
            status.className = `migration-status status-${item.status}`;
          }
          if (stage) {
            stage.textContent =
              `${Number(item.progress_percent || 0).toFixed(1)}% · ${item.stage}`;
          }
          if (error) {
            error.textContent = item.error_code || "";
            error.title = item.error_detail || "";
            error.classList.toggle("is-hidden", !item.error_detail);
          }
          if (metadata) metadata.textContent = item.metadata_status;
          if (thumbnail) thumbnail.textContent = item.thumbnail_status;
          if (preview) {
            preview.textContent =
              item.video_preview_status || item.preview_status;
          }
          if (video) {
            if (item.video_id) {
              const link = document.createElement("a");
              link.href = `/manage/videos?q=${encodeURIComponent(item.video_id)}`;
              link.textContent =
                `#${item.video_id} ${item.video_title || ""}`.trim();
              video.replaceChildren(link);
            } else {
              video.textContent = "—";
            }
          }
          if (
            ["pending", "running", "needs_cleanup"].includes(item.status)
          ) {
            row.dataset.importActive = "1";
          } else {
            row.dataset.importActive = "0";
            window.setTimeout(() => window.location.reload(), 700);
          }
        }
      } catch (_) {
        // Keep the last rendered state and retry later.
      }
    }
    refreshImports();
    window.setInterval(refreshImports, 2500);
  }

  const liveFormatRows = [
    ...document.querySelectorAll(
      '[data-format-job-row][data-format-active="1"]',
    ),
  ];
  if (liveFormatRows.length) {
    async function refreshFormatJobs() {
      if (document.visibilityState === "hidden") return;
      const ids = liveFormatRows
        .filter((row) => row.dataset.formatActive === "1")
        .map((row) => row.dataset.formatJobRow)
        .join(",");
      if (!ids) return;
      try {
        const response = await fetch(
          `/api/manage/format-jobs/live?ids=${encodeURIComponent(ids)}`,
          { cache: "no-store" },
        );
        if (!response.ok) return;
        const payload = await response.json();
        for (const item of payload.items) {
          const row = document.querySelector(
            `[data-format-job-row="${item.id}"]`,
          );
          if (!row) continue;
          const status = row.querySelector('[data-field="format-status"]');
          const stage = row.querySelector('[data-field="format-stage"]');
          const error = row.querySelector('[data-field="format-error"]');
          const bar = row.querySelector('[data-field="format-progress-bar"]');
          const text = row.querySelector('[data-field="format-progress-text"]');
          if (status) {
            status.textContent = item.status_label;
            status.className = `migration-status status-${item.status}`;
          }
          if (stage) stage.textContent = item.stage_label;
          if (error) {
            error.textContent = item.error_code || "";
            error.title = item.error_detail || "";
            error.classList.toggle("is-hidden", !item.error_detail);
          }
          if (bar) bar.style.width = `${item.progress_percent}%`;
          if (text) {
            text.textContent =
              `${Number(item.progress_percent || 0).toFixed(1)}%`;
          }
          if (
            ["pending", "running", "needs_cleanup"].includes(item.status)
          ) {
            row.dataset.formatActive = "1";
          } else {
            row.dataset.formatActive = "0";
            window.setTimeout(() => window.location.reload(), 700);
          }
        }
      } catch (_) {
        // Keep the last rendered state and retry later.
      }
    }
    refreshFormatJobs();
    window.setInterval(refreshFormatJobs, 2500);
  }
})();
