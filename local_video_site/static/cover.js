(() => {
  const dialog = document.getElementById("cover-dialog");
  const player = document.getElementById("cover-preview-player");
  const title = document.getElementById("cover-dialog-title");
  const status = document.getElementById("cover-dialog-status");
  const timeLabel = document.getElementById("cover-time-label");
  const saveButton = document.getElementById("save-cover-button");
  const restoreButton = document.getElementById("restore-cover-button");
  const closeButton = dialog?.querySelector(".cover-dialog-close");
  if (!dialog || !player || !saveButton || !restoreButton) return;

  let currentVideoId = null;
  let currentShell = null;
  let triggerButton = null;
  let busy = false;

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

  function setBusy(value, message = "") {
    busy = value;
    saveButton.disabled = value;
    restoreButton.disabled = value;
    if (message) status.textContent = message;
  }

  async function readError(response, fallback) {
    const payload = await response.json().catch(() => ({}));
    return payload.detail || fallback;
  }

  async function loadVideo(videoId) {
    setBusy(true, "正在准备视频播放…");
    try {
      const response = await fetch(`/api/prepare/${videoId}`, {
        method: "POST",
      });
      if (!response.ok) {
        throw new Error(await readError(response, "视频准备失败"));
      }
      const result = await response.json();
      player.src = result.playback_url;
      player.load();
      status.textContent = "";
      player.play().catch(() => {});
    } catch (error) {
      status.textContent = `无法加载视频：${error.message}`;
    } finally {
      setBusy(false);
    }
  }

  async function openDialog(button) {
    triggerButton = button;
    currentVideoId = Number(button.dataset.videoId);
    currentShell = button.closest(".video-card-shell");
    title.textContent = button.dataset.videoTitle
      ? `选择封面 · ${button.dataset.videoTitle}`
      : "选择视频封面";
    timeLabel.textContent = "当前画面：00:00";
    status.textContent = "";
    dialog.showModal();
    await loadVideo(currentVideoId);
  }

  function closeDialog() {
    if (dialog.open) dialog.close();
  }

  async function saveCurrentFrame() {
    if (busy || !currentVideoId) return;
    const timestamp = Number(player.currentTime);
    if (!Number.isFinite(timestamp)) {
      status.textContent = "无法读取当前播放时间";
      return;
    }
    setBusy(true, "正在生成封面…");
    try {
      const response = await fetch(`/api/videos/${currentVideoId}/cover`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ timestamp }),
      });
      if (!response.ok) {
        throw new Error(await readError(response, "封面生成失败"));
      }
      const result = await response.json();
      const image = currentShell?.querySelector(".thumb img");
      if (image) image.src = result.thumbnail_url;
      status.textContent = "封面已更新";
      window.setTimeout(closeDialog, 180);
    } catch (error) {
      status.textContent = `封面生成失败：${error.message}`;
    } finally {
      setBusy(false);
    }
  }

  async function restoreAutomaticCover() {
    if (busy || !currentVideoId) return;
    setBusy(true, "正在恢复自动封面…");
    try {
      const response = await fetch(`/api/videos/${currentVideoId}/cover`, {
        method: "DELETE",
      });
      if (!response.ok) {
        throw new Error(await readError(response, "自动封面恢复失败"));
      }
      const result = await response.json();
      const image = currentShell?.querySelector(".thumb img");
      if (image) image.src = result.thumbnail_url;
      status.textContent = "已恢复自动封面";
      window.setTimeout(closeDialog, 180);
    } catch (error) {
      status.textContent = `恢复失败：${error.message}`;
    } finally {
      setBusy(false);
    }
  }

  document.addEventListener("click", (event) => {
    const button = event.target.closest(".change-cover-button");
    if (!button) return;
    event.preventDefault();
    event.stopPropagation();
    openDialog(button);
  });

  saveButton.addEventListener("click", saveCurrentFrame);
  restoreButton.addEventListener("click", restoreAutomaticCover);
  closeButton?.addEventListener("click", closeDialog);

  player.addEventListener("timeupdate", () => {
    timeLabel.textContent = `当前画面：${formatTime(player.currentTime)}`;
  });

  dialog.addEventListener("close", () => {
    player.pause();
    player.removeAttribute("src");
    player.load();
    currentVideoId = null;
    currentShell = null;
    status.textContent = "";
    triggerButton?.focus();
    triggerButton = null;
  });
})();
