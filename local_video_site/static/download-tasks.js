(() => {
  const dialog = document.getElementById("download-task-dialog");
  const form = document.getElementById("download-task-form");
  const openButton = document.getElementById("new-download-task");
  const errorNode = document.getElementById("download-task-error");
  const saveButton = document.getElementById("save-download-task");
  const noticeNode = document.getElementById("download-notice");
  const outputInput = document.getElementById("download-output-dir");
  const qualityInput = document.getElementById("download-quality");
  const accessLevelInput = document.getElementById("download-access-level");
  const downloadOnlyInput = document.getElementById(
    "download-download-only",
  );
  const normalInput = document.getElementById("download-urls");
  const sourceRows = document.getElementById("download-source-rows");
  const addSourceRowButton = document.getElementById("add-download-row");
  const modeTabs = Array.from(
    document.querySelectorAll("[data-download-mode]"),
  );
  const modePanels = Array.from(
    document.querySelectorAll("[data-download-panel]"),
  );
  const deleteDialog = document.getElementById("download-delete-dialog");
  const deleteForm = document.getElementById("download-delete-form");
  const deleteDescription = document.getElementById(
    "download-delete-description",
  );
  const deletePasswordLabel = document.getElementById(
    "download-delete-password-label",
  );
  const deletePasswordInput = document.getElementById(
    "download-delete-password",
  );
  const deleteErrorNode = document.getElementById("download-delete-error");
  const confirmDeleteButton = document.getElementById(
    "confirm-download-delete",
  );
  const recordDeleteDialog = document.getElementById(
    "download-record-delete-dialog",
  );
  const recordDeleteForm = document.getElementById(
    "download-record-delete-form",
  );
  const recordDeleteDescription = document.getElementById(
    "download-record-delete-description",
  );
  const recordDeleteError = document.getElementById(
    "download-record-delete-error",
  );
  const confirmRecordDeleteButton = document.getElementById(
    "confirm-record-delete",
  );

  if (
    !dialog
    || !form
    || !openButton
    || !normalInput
    || !qualityInput
    || !accessLevelInput
    || !downloadOnlyInput
    || !sourceRows
    || !addSourceRowButton
    || !modeTabs.length
    || !modePanels.length
    || !deleteDialog
    || !deleteForm
    || !confirmDeleteButton
    || !recordDeleteDialog
    || !recordDeleteForm
    || !confirmRecordDeleteButton
  ) return;

  let refreshTimer = null;
  let dialogOpen = false;
  let deleteDialogOpen = false;
  let recordDeleteDialogOpen = false;
  let submitting = false;
  let refreshing = false;
  let formDirty = false;
  let deleteTargetRow = null;
  let recordDeleteTargetRow = null;
  let activeMode = "normal";
  let liveEtag = "";
  let lastFullRefreshAt = Date.now();
  let liveFailureCount = 0;
  const defaultOutputDir = form.dataset.defaultOutputDir || outputInput.value;

  function formatElapsed(value) {
    const total = Math.max(0, Math.floor(Number(value) || 0));
    const days = Math.floor(total / 86400);
    const hours = Math.floor((total % 86400) / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const seconds = total % 60;
    if (days) {
      return `${days}天 ${String(hours).padStart(2, "0")}:`
        + `${String(minutes).padStart(2, "0")}:`
        + String(seconds).padStart(2, "0");
    }
    if (hours) {
      return `${String(hours).padStart(2, "0")}:`
        + `${String(minutes).padStart(2, "0")}:`
        + String(seconds).padStart(2, "0");
    }
    return `${String(minutes).padStart(2, "0")}:`
      + String(seconds).padStart(2, "0");
  }

  function tickLiveMetrics() {
    document.querySelectorAll("[data-task-metrics]").forEach((metrics) => {
      if (metrics.dataset.taskRunning !== "1") return;
      const elapsed = Math.max(
        0,
        Number(metrics.dataset.taskElapsed || 0) + 1,
      );
      metrics.dataset.taskElapsed = String(elapsed);
      const elapsedNode = metrics.querySelector("[data-metric-elapsed]");
      if (elapsedNode) {
        elapsedNode.textContent = formatElapsed(elapsed);
      }

      if (metrics.dataset.taskEta === "") return;
      const eta = Math.max(0, Number(metrics.dataset.taskEta || 0) - 1);
      metrics.dataset.taskEta = String(eta);
      const etaNode = metrics.querySelector("[data-metric-eta]");
      if (etaNode) {
        etaNode.textContent = formatElapsed(eta);
      }
    });
  }

  function clearRefreshTimer() {
    if (refreshTimer !== null) {
      window.clearTimeout(refreshTimer);
      refreshTimer = null;
    }
  }

  function showNotice(message, isError = false) {
    if (!noticeNode) return;
    noticeNode.textContent = message;
    noticeNode.classList.toggle("is-error", isError);
    noticeNode.hidden = !message;
  }

  function setDownloadPage(page) {
    const url = new URL(window.location.href);
    url.searchParams.set("page", String(Math.max(1, Number(page) || 1)));
    window.history.replaceState(null, "", url);
  }

  function synchronizeDownloadPage() {
    const pagination = document.getElementById("download-pagination");
    if (!pagination) return;
    const currentPage = Number(pagination.dataset.currentPage || 1);
    const url = new URL(window.location.href);
    const requestedPage = url.searchParams.get("page");
    if (
      (requestedPage === null && currentPage > 1)
      || (
        requestedPage !== null
        && Number(requestedPage) !== currentPage
      )
    ) {
      setDownloadPage(currentPage);
    }
  }

  function hasActiveTasks() {
    return Boolean(document.querySelector(
      '[data-task-status="pending"], '
      + '[data-task-status="running"], '
      + '[data-task-status="pausing"], '
      + '[data-task-status="deleting"]',
    ));
  }

  function liveTaskIds() {
    return Array.from(document.querySelectorAll(
      '[data-task-status="pending"], '
      + '[data-task-status="running"], '
      + '[data-task-status="pausing"], '
      + '[data-task-status="deleting"]',
    )).map(row => row.dataset.taskId).filter(Boolean);
  }

  function updateSummary(summary) {
    const summaryNode = document.getElementById("download-summary");
    if (!summaryNode || !summary) return;
    Object.entries(summary).forEach(([status, value]) => {
      const node = summaryNode.querySelector(
        `[data-summary-count="${status}"]`,
      );
      if (node) node.textContent = String(value);
    });
    const deleting = summaryNode.querySelector(
      "[data-summary-deleting]",
    );
    if (deleting) deleting.hidden = !Number(summary.deleting || 0);
  }

  function replaceTaskRow(row, html) {
    const current = document.querySelector(
      `[data-task-id="${row.id}"]`,
    );
    if (!current || !html) return false;
    if (current.dataset.taskUpdatedAt === row.updated_at) return true;
    const template = document.createElement("template");
    template.innerHTML = html.trim();
    const next = template.content.firstElementChild;
    if (!next) return false;
    current.replaceWith(next);
    return true;
  }

  function updateDirtyState() {
    formDirty = Boolean(
      outputInput.value.trim() !== defaultOutputDir
      || qualityInput.value !== "site"
      || downloadOnlyInput.checked
      || normalInput.value.trim()
      || Array.from(sourceRows.querySelectorAll("input")).some(
        input => input.value.trim(),
      ),
    );
  }

  function syncLibraryMode() {
    accessLevelInput.disabled = downloadOnlyInput.checked;
    if (downloadOnlyInput.checked) accessLevelInput.value = "0";
  }

  downloadOnlyInput.addEventListener("change", () => {
    syncLibraryMode();
    updateDirtyState();
  });
  syncLibraryMode();

  function setActiveMode(mode, focus = false) {
    activeMode = mode === "special" ? "special" : "normal";
    for (const tab of modeTabs) {
      const selected = tab.dataset.downloadMode === activeMode;
      tab.classList.toggle("is-active", selected);
      tab.setAttribute("aria-selected", selected ? "true" : "false");
      tab.tabIndex = selected ? 0 : -1;
      if (selected && focus) tab.focus();
    }
    for (const panel of modePanels) {
      const selected = panel.dataset.downloadPanel === activeMode;
      panel.hidden = !selected;
      panel.querySelectorAll("input, textarea, button").forEach((control) => {
        control.disabled = !selected;
      });
    }
  }

  function addSourceRow(metadataUrl = "", downloadUrl = "") {
    const row = document.createElement("div");
    row.className = "download-source-row";
    row.innerHTML = (
      '<input type="url" data-metadata-url '
      + 'placeholder="https://site-a.example/video/1" '
      + 'aria-label="数据来源地址">'
      + '<input type="url" data-download-url '
      + 'placeholder="留空表示与数据来源相同" '
      + 'aria-label="下载来源地址">'
      + '<button type="button" class="download-remove-row" '
      + 'data-remove-download-row aria-label="删除这一行">×</button>'
    );
    row.querySelector("[data-metadata-url]").value = metadataUrl;
    row.querySelector("[data-download-url]").value = downloadUrl;
    row.querySelectorAll("input, button").forEach((control) => {
      control.disabled = activeMode !== "special";
    });
    sourceRows.append(row);
    return row;
  }

  function resetForm() {
    outputInput.value = defaultOutputDir;
    qualityInput.value = "site";
    accessLevelInput.value = "0";
    downloadOnlyInput.checked = false;
    syncLibraryMode();
    normalInput.value = "";
    sourceRows.replaceChildren();
    addSourceRow();
    setActiveMode("normal");
    errorNode.textContent = "";
    formDirty = false;
  }

  function replaceSection(documentNode, id) {
    const current = document.getElementById(id);
    const next = documentNode.getElementById(id);
    if (current && next) current.replaceWith(next);
  }

  async function refreshDownloads(force = false) {
    if (refreshing) return;
    if (
      !force
      && (
        dialogOpen
        || deleteDialogOpen
        || recordDeleteDialogOpen
        || document.hidden
      )
    ) return;
    refreshing = true;
    const table = document.getElementById("download-table-section");
    const scrollLeft = table ? table.scrollLeft : 0;
    try {
      const response = await fetch(window.location.href, {
        cache: "no-store",
        headers: {"X-Download-Refresh": "1"},
      });
      if (!response.ok) throw new Error("刷新任务列表失败");
      const html = await response.text();
      const documentNode = new DOMParser().parseFromString(
        html,
        "text/html",
      );
      for (const id of [
        "download-summary",
        "download-table-section",
        "download-empty",
        "download-pagination",
      ]) {
        replaceSection(documentNode, id);
      }
      liveEtag = "";
      liveFailureCount = 0;
      lastFullRefreshAt = Date.now();
      synchronizeDownloadPage();
      const updatedTable = document.getElementById("download-table-section");
      if (updatedTable) updatedTable.scrollLeft = scrollLeft;
    } catch (error) {
      showNotice(error.message || String(error), true);
    } finally {
      refreshing = false;
      scheduleRefresh();
    }
  }

  async function refreshLiveDownloads(force = false) {
    if (refreshing) return;
    if (
      !force
      && (
        dialogOpen
        || deleteDialogOpen
        || recordDeleteDialogOpen
        || document.hidden
      )
    ) return;
    const ids = liveTaskIds();
    if (!ids.length) {
      updateDirtyState();
      scheduleRefresh();
      return;
    }
    refreshing = true;
    let fullRefreshRequired = false;
    try {
      const currentUrl = new URL(window.location.href);
      const url = new URL("/api/download-tasks/live", currentUrl.origin);
      url.searchParams.set("ids", ids.join(","));
      for (const key of ["q", "status"]) {
        const value = currentUrl.searchParams.get(key);
        if (value) url.searchParams.set(key, value);
      }
      const headers = {};
      if (liveEtag) headers["If-None-Match"] = liveEtag;
      const response = await fetch(url, {
        cache: "no-store",
        headers,
      });
      if (response.status === 304) {
        liveFailureCount = 0;
        scheduleRefresh();
        return;
      }
      if (!response.ok) throw new Error("刷新下载任务数据失败");
      const payload = await response.json();
      liveEtag = response.headers.get("ETag") || "";
      liveFailureCount = 0;
      updateSummary(payload.summary);
      for (const row of payload.rows || []) {
        const current = document.querySelector(
          `[data-task-id="${row.id}"]`,
        );
        if (!current) {
          fullRefreshRequired = true;
          continue;
        }
        if (!row.matches_filter) {
          current.remove();
          fullRefreshRequired = true;
          continue;
        }
        if (!replaceTaskRow(row, row.html)) {
          fullRefreshRequired = true;
        }
      }
      fullRefreshRequired = (
        fullRefreshRequired
        || Boolean(payload.full_refresh_required)
        || Boolean((payload.missing_ids || []).length)
      );
    } catch (error) {
      liveFailureCount += 1;
      if (liveFailureCount >= 2) {
        fullRefreshRequired = true;
      } else {
        showNotice(error.message || String(error), true);
      }
    } finally {
      refreshing = false;
    }
    if (fullRefreshRequired) {
      await refreshDownloads(true);
      return;
    }
    scheduleRefresh();
  }

  async function scheduledRefresh() {
    const fullRefreshDue = (
      Date.now() - lastFullRefreshAt >= 60000
    );
    if (fullRefreshDue) {
      await refreshDownloads();
    } else {
      await refreshLiveDownloads();
    }
  }

  function scheduleRefresh(delay = 5000) {
    clearRefreshTimer();
    if (
      dialogOpen
      || deleteDialogOpen
      || recordDeleteDialogOpen
      || submitting
      || document.hidden
      || !hasActiveTasks()
    ) {
      return;
    }
    refreshTimer = window.setTimeout(
      () => scheduledRefresh(),
      delay,
    );
  }

  function requestCloseDialog(force = false) {
    updateDirtyState();
    if (
      !force
      && formDirty
      && !window.confirm("表单尚未保存，确定关闭并丢弃输入吗？")
    ) {
      return false;
    }
    resetForm();
    dialog.close();
    return true;
  }

  openButton.addEventListener("click", () => {
    clearRefreshTimer();
    dialogOpen = true;
    errorNode.textContent = "";
    if (!sourceRows.children.length) addSourceRow();
    setActiveMode("normal");
    dialog.showModal();
    outputInput.focus();
  });

  addSourceRowButton.addEventListener("click", () => {
    const row = addSourceRow();
    row.querySelector("[data-metadata-url]").focus();
    updateDirtyState();
  });

  modeTabs.forEach((tab, index) => {
    tab.addEventListener("click", () => {
      setActiveMode(tab.dataset.downloadMode);
    });
    tab.addEventListener("keydown", (event) => {
      let targetIndex = null;
      if (event.key === "ArrowLeft") {
        targetIndex = (index - 1 + modeTabs.length) % modeTabs.length;
      } else if (event.key === "ArrowRight") {
        targetIndex = (index + 1) % modeTabs.length;
      } else if (event.key === "Home") {
        targetIndex = 0;
      } else if (event.key === "End") {
        targetIndex = modeTabs.length - 1;
      }
      if (targetIndex === null) return;
      event.preventDefault();
      setActiveMode(
        modeTabs[targetIndex].dataset.downloadMode,
        true,
      );
    });
  });

  sourceRows.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-download-row]");
    if (!button) return;
    const row = button.closest(".download-source-row");
    if (!row) return;
    if (sourceRows.children.length === 1) {
      row.querySelectorAll("input").forEach(input => {
        input.value = "";
      });
    } else {
      row.remove();
    }
    updateDirtyState();
  });

  function closeDeleteDialog() {
    if (deleteDialog.open) deleteDialog.close();
  }

  function closeRecordDeleteDialog() {
    if (recordDeleteDialog.open) recordDeleteDialog.close();
  }

  function openRecordDeleteDialog(row) {
    recordDeleteTargetRow = row;
    recordDeleteDescription.textContent = (
      `任务 #${row.dataset.taskId}：${row.dataset.taskTitle}\n`
      + `已下载：${row.dataset.taskDownloaded || "0 B"}`
    );
    recordDeleteError.textContent = "";
    confirmRecordDeleteButton.disabled = false;
    recordDeleteDialogOpen = true;
    clearRefreshTimer();
    recordDeleteDialog.showModal();
  }

  function openDeleteDialog(row) {
    deleteTargetRow = row;
    const requiresPassword = (
      row.dataset.deletePasswordRequired === "1"
    );
    deleteDescription.textContent = (
      `任务 #${row.dataset.taskId}：${row.dataset.taskTitle}\n`
      + `已下载：${row.dataset.taskDownloaded || "0 B"}`
    );
    deleteErrorNode.textContent = "";
    deletePasswordLabel.hidden = !requiresPassword;
    deletePasswordInput.hidden = !requiresPassword;
    deletePasswordInput.required = requiresPassword;
    deletePasswordInput.value = "";
    confirmDeleteButton.disabled = false;
    deleteDialogOpen = true;
    clearRefreshTimer();
    deleteDialog.showModal();
    if (requiresPassword) deletePasswordInput.focus();
  }

  dialog.querySelectorAll("[data-close-download-dialog]").forEach((button) => {
    button.addEventListener("click", () => requestCloseDialog());
  });

  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    requestCloseDialog();
  });

  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) requestCloseDialog();
  });

  dialog.addEventListener("close", () => {
    dialogOpen = false;
    if (formDirty) resetForm();
    scheduleRefresh(0);
  });

  form.addEventListener("input", updateDirtyState);
  form.addEventListener("change", updateDirtyState);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    submitting = true;
    saveButton.disabled = true;
    errorNode.textContent = "";
    let payload = null;
    if (activeMode === "normal") {
      const urls = normalInput.value
        .split(/\r?\n/)
        .map(value => value.trim())
        .filter(Boolean);
      if (!urls.length) {
        errorNode.textContent = "请至少输入一个网页地址";
        submitting = false;
        saveButton.disabled = false;
        return;
      }
      payload = {
        mode: "normal",
        output_dir: outputInput.value,
        quality: qualityInput.value,
        access_level: Number(accessLevelInput.value),
        index_into_library: !downloadOnlyInput.checked,
        urls,
      };
    } else {
      const items = Array.from(
        sourceRows.querySelectorAll(".download-source-row"),
      ).map((row) => ({
        metadata_url: row.querySelector(
          "[data-metadata-url]",
        ).value.trim(),
        download_url: row.querySelector(
          "[data-download-url]",
        ).value.trim(),
      })).filter(item => item.metadata_url || item.download_url);
      if (!items.length) {
        errorNode.textContent = "请至少填写一条数据来源地址";
        submitting = false;
        saveButton.disabled = false;
        return;
      }
      const missingMetadata = items.some(item => !item.metadata_url);
      if (missingMetadata) {
        errorNode.textContent = "每一行都必须填写数据来源地址";
        submitting = false;
        saveButton.disabled = false;
        return;
      }
      payload = {
        mode: "special",
        output_dir: outputInput.value,
        quality: qualityInput.value,
        access_level: Number(accessLevelInput.value),
        index_into_library: !downloadOnlyInput.checked,
        items,
      };
    }
    try {
      const response = await fetch("/api/download-tasks", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || "保存任务失败");
      }
      const result = await response.json();
      requestCloseDialog(true);
      setDownloadPage(1);
      showNotice(
        `已创建 ${result.created} 个任务，重复 ${result.duplicates} 个，`
        + `无效 ${result.invalid.length} 个`
        + (
          result.duplicate_items?.length
            ? `。重复数据来源：${result.duplicate_items
              .map(item => item.metadata_url)
              .join("、")}`
            : ""
        ),
      );
      await refreshDownloads(true);
    } catch (error) {
      errorNode.textContent = error.message || String(error);
    } finally {
      submitting = false;
      saveButton.disabled = false;
      scheduleRefresh();
    }
  });

  deleteDialog.querySelectorAll("[data-close-delete-dialog]").forEach((button) => {
    button.addEventListener("click", closeDeleteDialog);
  });

  deleteDialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeDeleteDialog();
  });

  deleteDialog.addEventListener("click", (event) => {
    if (event.target === deleteDialog) closeDeleteDialog();
  });

  deleteDialog.addEventListener("close", () => {
    deleteDialogOpen = false;
    deleteTargetRow = null;
    deletePasswordInput.value = "";
    deleteErrorNode.textContent = "";
    scheduleRefresh(0);
  });

  deleteForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!deleteTargetRow) return;
    confirmDeleteButton.disabled = true;
    deleteErrorNode.textContent = "";
    try {
      const response = await fetch(
        `/api/download-tasks/${deleteTargetRow.dataset.taskId}`,
        {
          method: "DELETE",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({password: deletePasswordInput.value}),
        },
      );
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || "删除任务失败");
      }
      closeDeleteDialog();
      showNotice("任务已进入删除队列，正在停止下载并清理文件");
      await refreshDownloads(true);
    } catch (error) {
      deleteErrorNode.textContent = error.message || String(error);
      confirmDeleteButton.disabled = false;
    }
  });

  recordDeleteDialog.querySelectorAll("[data-close-record-delete]").forEach(
    (button) => {
      button.addEventListener("click", closeRecordDeleteDialog);
    },
  );
  recordDeleteDialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeRecordDeleteDialog();
  });
  recordDeleteDialog.addEventListener("click", (event) => {
    if (event.target === recordDeleteDialog) closeRecordDeleteDialog();
  });
  recordDeleteDialog.addEventListener("close", () => {
    recordDeleteDialogOpen = false;
    recordDeleteTargetRow = null;
    recordDeleteError.textContent = "";
    scheduleRefresh(0);
  });
  recordDeleteForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!recordDeleteTargetRow) return;
    confirmRecordDeleteButton.disabled = true;
    recordDeleteError.textContent = "";
    try {
      const response = await fetch(
        `/api/download-tasks/${recordDeleteTargetRow.dataset.taskId}/record`,
        {method: "DELETE"},
      );
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || "删除记录失败");
      }
      closeRecordDeleteDialog();
      showNotice("下载记录已删除，视频和文件未做任何改动");
      await refreshDownloads(true);
    } catch (error) {
      recordDeleteError.textContent = error.message || String(error);
      confirmRecordDeleteButton.disabled = false;
    }
  });

  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-action]");
    if (!button) return;
    const row = button.closest("[data-task-id]");
    if (!row) return;
    const action = button.dataset.action;
    if (action === "delete-record") {
      openRecordDeleteDialog(row);
      return;
    }
    if (action === "delete") {
      openDeleteDialog(row);
      return;
    }
    if (
      action === "cancel"
      && row.dataset.taskStatus === "paused"
      && !window.confirm("取消后将删除已保存的断点文件，确定继续吗？")
    ) {
      return;
    }
    button.disabled = true;
    try {
      const response = await fetch(
        `/api/download-tasks/${row.dataset.taskId}/${action}`,
        {method: "POST"},
      );
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || "操作失败");
      }
      await refreshDownloads(true);
    } catch (error) {
      window.alert(error.message || String(error));
      button.disabled = false;
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      clearRefreshTimer();
    } else {
      scheduleRefresh(0);
    }
  });

  scheduleRefresh();
  window.setInterval(tickLiveMetrics, 1000);
})();
