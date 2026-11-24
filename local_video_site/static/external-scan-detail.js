(() => {
  const root = document.getElementById("external-scan-detail");
  if (!root) return;
  const scanId = root.dataset.scanId;
  const targetSeasonId = Number(root.dataset.targetSeasonId || 0);
  const status = document.getElementById("external-scan-status");
  const stage = document.getElementById("external-scan-stage");
  const processed = document.getElementById("external-scan-processed");
  const total = document.getElementById("external-scan-total");
  const ready = document.getElementById("external-scan-ready");
  const failed = document.getElementById("external-scan-failed");
  const imported = document.getElementById("external-scan-imported");
  const progressText = document.getElementById("external-scan-progress-text");
  const progressBar = document.getElementById("external-scan-progress-bar");
  const error = document.getElementById("external-scan-detail-error");
  const selectAll = document.getElementById("external-scan-select-all");
  let activeStatus = status?.textContent.trim() || "";
  let pollTimer = null;

  function detailMessage(payload, fallback) {
    const detail = payload?.detail || payload;
    return detail?.message || detail?.detail || detail || fallback;
  }

  function setError(message = "") {
    if (error) error.textContent = message;
  }

  function update(payload) {
    const nextStatus = payload.status_label || payload.status;
    if (status) {
      status.textContent = nextStatus;
      status.className = `migration-status status-${payload.status}`;
    }
    if (stage) stage.textContent = payload.stage_label || payload.stage;
    if (processed) processed.textContent = payload.processed_files;
    if (total) total.textContent = payload.total_files;
    if (ready) ready.textContent = payload.ready_files;
    if (failed) failed.textContent = payload.failed_files;
    if (imported) imported.textContent = payload.imported_files;
    if (progressText) {
      progressText.textContent = `${Number(payload.progress_percent || 0).toFixed(1)}%`;
    }
    if (progressBar) {
      progressBar.style.width = `${Number(payload.progress_percent || 0)}%`;
    }
    setError(payload.error_detail || "");
    if (nextStatus !== activeStatus) {
      window.location.reload();
      return;
    }
    if (["pending", "running", "committing"].includes(payload.status)) {
      pollTimer = setTimeout(poll, 1500);
    }
  }

  async function poll() {
    clearTimeout(pollTimer);
    try {
      const response = await fetch(
        `/api/manage/external-scans/${encodeURIComponent(scanId)}`,
        { cache: "no-store" },
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(detailMessage(payload, `HTTP ${response.status}`));
      }
      update(payload);
    } catch (pollError) {
      setError(pollError.message);
      pollTimer = setTimeout(poll, 3000);
    }
  }

  selectAll?.addEventListener("change", () => {
    document.querySelectorAll(".external-scan-item-select").forEach(
      (checkbox) => {
        checkbox.checked = selectAll.checked;
      },
    );
  });

  document.getElementById("commit-external-scan")?.addEventListener(
    "click",
    async (event) => {
      const button = event.currentTarget;
      const selected = Array.from(
        document.querySelectorAll(".external-scan-item-select:checked"),
      );
      if (!selected.length) {
        setError("请至少选择一个可入库视频。");
        return;
      }
      const selectable = Array.from(
        document.querySelectorAll(".external-scan-item-select"),
      );
      if (targetSeasonId && selected.length !== selectable.length) {
        setError("加入现有视频季时必须选择全部可入库视频。");
        return;
      }
      button.disabled = true;
      setError("");
      try {
        const items = selected.map((checkbox) => {
          const row = checkbox.closest("[data-scan-item]");
          const access = row.querySelector(".external-scan-item-access");
          const episode = row.querySelector(
            ".external-scan-episode-input",
          );
          if (targetSeasonId && !episode?.value.trim()) {
            const fileName = row.querySelector(
              ".management-path-cell strong",
            )?.textContent.trim() || "当前视频";
            throw new Error(`${fileName} 尚未填写集数。`);
          }
          return {
            item_id: Number(checkbox.value),
            access_level: Number(access?.value || 0),
            episode_number: targetSeasonId
              ? Number(episode.value)
              : null,
          };
        });
        const response = await fetch(
          `/api/manage/external-scans/${encodeURIComponent(scanId)}/commit`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ items }),
          },
        );
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(detailMessage(payload, `HTTP ${response.status}`));
        }
        window.location.reload();
      } catch (commitError) {
        setError(commitError.message);
        button.disabled = false;
      }
    },
  );

  document.getElementById("cancel-external-scan")?.addEventListener(
    "click",
    async () => {
      const response = await fetch(
        `/api/manage/external-scans/${encodeURIComponent(scanId)}/cancel`,
        { method: "POST" },
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        setError(detailMessage(payload, `HTTP ${response.status}`));
        return;
      }
      window.location.reload();
    },
  );

  document.getElementById("retry-external-scan")?.addEventListener(
    "click",
    async () => {
      const response = await fetch(
        `/api/manage/external-scans/${encodeURIComponent(scanId)}/retry`,
        { method: "POST" },
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        setError(detailMessage(payload, `HTTP ${response.status}`));
        return;
      }
      window.location.reload();
    },
  );

  document.querySelectorAll(".external-scan-preview-cell video").forEach(
    (video) => {
      video.addEventListener("mouseenter", () => video.play().catch(() => {}));
      video.addEventListener("mouseleave", () => {
        video.pause();
        video.currentTime = 0;
      });
    },
  );

  if (["等待扫描", "扫描中", "正在入库"].includes(activeStatus)) {
    pollTimer = setTimeout(poll, 1000);
  }
})();
