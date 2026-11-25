(() => {
  const dialog = document.getElementById("cover-dialog");
  const image = document.getElementById("cover-preview-image");
  const slider = document.getElementById("cover-time-slider");
  const title = document.getElementById("cover-dialog-title");
  const status = document.getElementById("cover-dialog-status");
  const timeLabel = document.getElementById("cover-time-label");
  const saveButton = document.getElementById("save-cover-button");
  const restoreButton = document.getElementById("restore-cover-button");
  const closeButton = dialog?.querySelector(".cover-dialog-close");
  if (!dialog || !image || !slider || !saveButton || !restoreButton) {
    return;
  }

  let currentVideoId = null;
  let currentShell = null;
  let triggerButton = null;
  let editor = null;
  let selectedTimestamp = null;
  let operationBusy = false;
  let previewBusy = false;
  let previewLoading = false;
  let pendingPreviewTimestamp = null;
  let previewController = null;
  let previewObjectUrl = null;
  let dialogGeneration = 0;

  function formatTime(value) {
    const total = Math.max(0, Math.floor(Number(value) || 0));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const seconds = total % 60;
    if (hours) {
      return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
    }
    return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }

  function detailMessage(detail) {
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) => detailMessage(item))
        .filter(Boolean)
        .join("；");
    }
    if (detail && typeof detail === "object") {
      return (
        detail.message
        || detail.msg
        || detail.error?.message
        || ""
      );
    }
    return "";
  }

  async function readError(response, fallback) {
    const payload = await response.json().catch(() => ({}));
    return (
      detailMessage(payload.detail)
      || detailMessage(payload.message)
      || detailMessage(payload.error)
      || fallback
    );
  }

  function updateActionState() {
    const unavailable = (
      operationBusy
      || previewLoading
      || !editor
      || selectedTimestamp === null
    );
    saveButton.disabled = unavailable;
    restoreButton.disabled = operationBusy || !editor;
    slider.disabled = operationBusy || !editor;
  }

  function setOperationBusy(value, message = "") {
    operationBusy = value;
    if (message) status.textContent = message;
    updateActionState();
  }

  function setPreviewLoading(value, message = "") {
    previewLoading = value;
    status.textContent = message;
    updateActionState();
  }

  function updateTimeLabel(value) {
    const duration = Number(editor?.duration || 0);
    const suffix = duration > 0 ? ` / ${formatTime(duration)}` : "";
    timeLabel.textContent = `当前画面：${formatTime(value)}${suffix}`;
  }

  function releasePreviewUrl() {
    if (previewObjectUrl) {
      URL.revokeObjectURL(previewObjectUrl);
      previewObjectUrl = null;
    }
  }

  async function fetchPreview(timestamp) {
    if (!currentVideoId || !editor) return;
    const generation = dialogGeneration;
    const videoId = currentVideoId;
    const step = Number(editor.preview_step_seconds || 0.5);
    const normalized = Math.max(
      0,
      Math.round(Number(timestamp || 0) / step) * step,
    );
    setPreviewLoading(true, "正在提取画面…");
    previewController = new AbortController();
    try {
      const query = new URLSearchParams({
        timestamp: normalized.toFixed(1),
        v: String(editor.source_version || ""),
      });
      const response = await fetch(
        `/api/videos/${currentVideoId}/cover-preview?${query}`,
        {
          signal: previewController.signal,
        },
      );
      if (!response.ok) {
        throw new Error(await readError(response, "画面提取失败"));
      }
      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      const candidate = new Image();
      candidate.src = objectUrl;
      try {
        await candidate.decode();
      } catch (error) {
        URL.revokeObjectURL(objectUrl);
        throw new Error("提取到的画面无法显示");
      }
      if (
        generation !== dialogGeneration
        || videoId !== currentVideoId
      ) {
        URL.revokeObjectURL(objectUrl);
        return;
      }
      releasePreviewUrl();
      previewObjectUrl = objectUrl;
      image.src = objectUrl;
      image.hidden = false;
      selectedTimestamp = Number(
        response.headers.get("x-cover-timestamp") || normalized,
      );
      slider.value = String(selectedTimestamp);
      updateTimeLabel(selectedTimestamp);
      status.textContent = "";
    } finally {
      previewController = null;
      setPreviewLoading(false);
    }
  }

  async function queuePreview(timestamp) {
    pendingPreviewTimestamp = Number(timestamp) || 0;
    if (previewBusy) return;
    previewBusy = true;
    try {
      while (
        pendingPreviewTimestamp !== null
        && currentVideoId !== null
      ) {
        const target = pendingPreviewTimestamp;
        pendingPreviewTimestamp = null;
        try {
          await fetchPreview(target);
        } catch (error) {
          if (error.name === "AbortError") return;
          status.textContent = `无法加载封面画面：${error.message}`;
        }
      }
    } finally {
      previewBusy = false;
    }
  }

  async function openDialog(button) {
    dialogGeneration += 1;
    triggerButton = button;
    currentVideoId = Number(button.dataset.videoId);
    currentShell = button.closest(".video-card-shell");
    editor = null;
    selectedTimestamp = null;
    pendingPreviewTimestamp = null;
    title.textContent = button.dataset.videoTitle
      ? `选择封面 · ${button.dataset.videoTitle}`
      : "选择视频封面";
    image.hidden = true;
    image.removeAttribute("src");
    slider.value = "0";
    slider.max = "0";
    updateTimeLabel(0);
    dialog.showModal();
    setOperationBusy(true, "正在读取视频信息…");
    try {
      const response = await fetch(
        `/api/videos/${currentVideoId}/cover-editor`,
        { cache: "no-store" },
      );
      if (!response.ok) {
        throw new Error(await readError(response, "读取视频信息失败"));
      }
      editor = await response.json();
      const duration = Math.max(0, Number(editor.duration) || 0);
      slider.min = "0";
      slider.max = String(Math.max(0, duration - 0.1));
      slider.step = String(editor.preview_step_seconds || 0.5);
      const initial = Math.min(
        Number(editor.default_timestamp) || 0,
        Number(slider.max),
      );
      slider.value = String(initial);
      updateTimeLabel(initial);
      setOperationBusy(false);
      await queuePreview(initial);
    } catch (error) {
      status.textContent = `无法打开封面选择：${error.message}`;
      setOperationBusy(false);
    }
  }

  function closeDialog() {
    if (dialog.open) dialog.close();
  }

  async function saveCurrentFrame() {
    if (operationBusy || previewLoading || !editor) return;
    if (selectedTimestamp === null) {
      status.textContent = "请先选择一个有效的画面";
      return;
    }
    setOperationBusy(true, "正在生成封面…");
    try {
      const response = await fetch(`/api/videos/${currentVideoId}/cover`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ timestamp: selectedTimestamp }),
      });
      if (!response.ok) {
        throw new Error(await readError(response, "封面生成失败"));
      }
      const result = await response.json();
      const preview = currentShell?.querySelector(".thumb img");
      if (preview) preview.src = result.thumbnail_url;
      status.textContent = "封面已更新";
      window.setTimeout(closeDialog, 180);
    } catch (error) {
      status.textContent = `封面生成失败：${error.message}`;
    } finally {
      setOperationBusy(false);
    }
  }

  async function restoreAutomaticCover() {
    if (operationBusy || !editor) return;
    setOperationBusy(true, "正在恢复自动封面…");
    try {
      const response = await fetch(`/api/videos/${currentVideoId}/cover`, {
        method: "DELETE",
      });
      if (!response.ok) {
        throw new Error(await readError(response, "自动封面恢复失败"));
      }
      const result = await response.json();
      const preview = currentShell?.querySelector(".thumb img");
      if (preview) preview.src = result.thumbnail_url;
      status.textContent = "已恢复自动封面";
      window.setTimeout(closeDialog, 180);
    } catch (error) {
      status.textContent = `恢复失败：${error.message}`;
    } finally {
      setOperationBusy(false);
    }
  }

  document.addEventListener("click", (event) => {
    const button = event.target.closest(".change-cover-button");
    if (!button) return;
    event.preventDefault();
    event.stopPropagation();
    openDialog(button);
  });

  slider.addEventListener("input", () => {
    const timestamp = Number(slider.value);
    updateTimeLabel(timestamp);
    queuePreview(timestamp);
  });

  saveButton.addEventListener("click", saveCurrentFrame);
  restoreButton.addEventListener("click", restoreAutomaticCover);
  closeButton?.addEventListener("click", closeDialog);

  dialog.addEventListener("close", () => {
    dialogGeneration += 1;
    previewController?.abort();
    previewController = null;
    pendingPreviewTimestamp = null;
    previewBusy = false;
    previewLoading = false;
    releasePreviewUrl();
    image.hidden = true;
    image.removeAttribute("src");
    slider.disabled = true;
    currentVideoId = null;
    currentShell = null;
    editor = null;
    selectedTimestamp = null;
    status.textContent = "";
    timeLabel.textContent = "当前画面：00:00";
    triggerButton?.focus();
    triggerButton = null;
    updateActionState();
  });
})();
