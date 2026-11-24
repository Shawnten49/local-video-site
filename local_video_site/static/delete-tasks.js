(() => {
  document.addEventListener("click", async (event) => {
    const retryBatch = event.target.closest("[data-retry-delete-batch]");
    if (retryBatch) {
      retryBatch.disabled = true;
      try {
        const response = await fetch(
          `/api/manage/delete-batches/${encodeURIComponent(
            retryBatch.dataset.retryDeleteBatch,
          )}/retry-failed`,
          { method: "POST" },
        );
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          throw new Error(payload.detail || `HTTP ${response.status}`);
        }
        window.location.reload();
      } catch (error) {
        retryBatch.disabled = false;
        window.alert(error.message || String(error));
      }
      return;
    }
    const retry = event.target.closest("[data-retry-delete-job]");
    if (!retry) return;
    retry.disabled = true;
    try {
      const response = await fetch(
        `/api/manage/delete-jobs/${retry.dataset.retryDeleteJob}/retry`,
        { method: "POST" },
      );
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || `HTTP ${response.status}`);
      }
      window.location.reload();
    } catch (error) {
      retry.disabled = false;
      window.alert(error.message || String(error));
    }
  });

  const liveRows = [
    ...document.querySelectorAll(
      '[data-delete-task-row][data-delete-task-active="1"]',
    ),
  ];
  if (!liveRows.length) return;

  async function refresh() {
    if (document.visibilityState === "hidden") return;
    const ids = liveRows
      .filter((row) => row.dataset.deleteTaskActive === "1")
      .map((row) => row.dataset.deleteTaskRow)
      .join(",");
    if (!ids) return;
    try {
      const response = await fetch(
        `/api/manage/delete-jobs/live?ids=${encodeURIComponent(ids)}`,
        { cache: "no-store" },
      );
      if (!response.ok) return;
      const payload = await response.json();
      for (const item of payload.items) {
        const row = document.querySelector(
          `[data-delete-task-row="${item.id}"]`,
        );
        if (!row) continue;
        const status = row.querySelector('[data-field="delete-status"]');
        const stage = row.querySelector('[data-field="delete-stage"]');
        const progress = row.querySelector('[data-field="delete-progress"]');
        if (status) {
          status.textContent = item.status_label;
          status.className = `migration-status status-${item.status}`;
        }
        if (stage) stage.textContent = item.stage_label;
        if (progress) progress.style.width = `${item.progress_percent}%`;
        if (!["pending", "running", "blocked"].includes(item.status)) {
          row.dataset.deleteTaskActive = "0";
          window.setTimeout(() => window.location.reload(), 700);
        }
      }
    } catch (_) {
      // Retry on the next interval.
    }
  }

  refresh();
  window.setInterval(refresh, 2500);
})();
