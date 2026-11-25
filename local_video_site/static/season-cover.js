(() => {
  const dialog = document.getElementById("season-cover-dialog");
  if (!dialog) return;
  const title = document.getElementById("season-cover-title");
  const preview = document.getElementById("season-cover-preview");
  const status = document.getElementById("season-cover-status");
  const episodeSelect = document.getElementById("season-cover-episode");
  const timeInput = document.getElementById("season-cover-time");
  const timeLabel = document.getElementById("season-cover-time-label");
  const episodeField = document.getElementById("season-cover-episode-field");
  const timeField = document.getElementById("season-cover-time-field");
  const saveButton = document.getElementById("save-season-cover");
  const restoreButton = document.getElementById("restore-season-cover");
  let selection = null;
  let editor = null;
  let previewSerial = 0;

  function message(detail) {
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail.map(message).filter(Boolean).join("；");
    }
    if (detail && typeof detail === "object") {
      return detail.message || detail.msg || "";
    }
    return "";
  }

  async function responseError(response, fallback) {
    const payload = await response.json().catch(() => ({}));
    return message(payload.detail) || message(payload.message) || fallback;
  }

  function formatTime(value) {
    const total = Math.max(0, Math.floor(Number(value) || 0));
    return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
  }

  function source() {
    return document.querySelector(
      'input[name="season-cover-source"]:checked',
    )?.value || "inherited";
  }

  function selectedEpisode() {
    return editor?.episodes?.find(
      (item) => String(item.video_id) === episodeSelect.value,
    ) || null;
  }

  function syncFields() {
    const useFrame = source() === "video_frame";
    episodeField.hidden = !useFrame;
    timeField.hidden = !useFrame;
    if (!useFrame) {
      if (editor?.cover_url) preview.src = editor.cover_url;
      else preview.removeAttribute("src");
    }
  }

  async function updateFrame(timestamp) {
    const episode = selectedEpisode();
    if (!episode) return;
    const serial = ++previewSerial;
    status.textContent = "正在提取画面…";
    try {
      const response = await fetch(
        `/api/videos/${episode.video_id}/cover-preview?timestamp=${encodeURIComponent(timestamp)}`,
      );
      if (!response.ok) {
        throw new Error(await responseError(response, "画面提取失败"));
      }
      const blob = await response.blob();
      if (serial !== previewSerial) return;
      preview.src = URL.createObjectURL(blob);
      status.textContent = "";
    } catch (error) {
      status.textContent = error.message;
    }
  }

  async function loadEpisode() {
    const episode = selectedEpisode();
    if (!episode) return;
    status.textContent = "正在读取视频信息…";
    try {
      const response = await fetch(
        `/api/videos/${episode.video_id}/cover-editor`,
        { cache: "no-store" },
      );
      if (!response.ok) {
        throw new Error(await responseError(response, "读取视频信息失败"));
      }
      const payload = await response.json();
      timeInput.max = String(Math.max(0, Number(payload.duration) - 0.1));
      timeInput.step = String(payload.preview_step_seconds || 0.5);
      timeInput.value = String(payload.default_timestamp || 0);
      timeLabel.textContent = formatTime(timeInput.value);
      await updateFrame(Number(timeInput.value));
    } catch (error) {
      status.textContent = error.message;
    }
  }

  async function openDialog(button) {
    selection = {
      seriesId: Number(button.dataset.seasonCoverSeries),
      seasonId: Number(button.dataset.seasonCoverSeason),
      title: button.dataset.seasonCoverTitle || "",
      updatedAt: button.dataset.seasonCoverUpdatedAt || "",
    };
    editor = null;
    previewSerial += 1;
    title.textContent = `视频季封面 · ${selection.title}`;
    status.textContent = "正在读取封面信息…";
    preview.removeAttribute("src");
    saveButton.disabled = true;
    restoreButton.disabled = true;
    dialog.showModal();
    try {
      const response = await fetch(
        `/api/manage/series/${selection.seriesId}/seasons/${selection.seasonId}/cover-editor`,
        { cache: "no-store" },
      );
      if (!response.ok) {
        throw new Error(await responseError(response, "读取封面失败"));
      }
      editor = await response.json();
      selection.updatedAt = editor.updated_at || selection.updatedAt;
      title.textContent = `视频季封面 · ${editor.title || selection.title}`;
      episodeSelect.replaceChildren();
      for (const episode of editor.episodes || []) {
        const option = document.createElement("option");
        option.value = String(episode.video_id);
        option.textContent = `${String(episode.episode_number).padStart(2, "0")} · ${episode.title}`;
        episodeSelect.append(option);
      }
      const sourceValue = editor.cover_source === "video_frame"
        ? "video_frame"
        : "inherited";
      document.querySelector(
        `input[name="season-cover-source"][value="${sourceValue}"]`,
      ).checked = true;
      syncFields();
      saveButton.disabled = false;
      restoreButton.disabled = false;
      status.textContent = "";
      if (sourceValue === "video_frame") await loadEpisode();
    } catch (error) {
      status.textContent = error.message;
      restoreButton.disabled = false;
    }
  }

  document.querySelectorAll('input[name="season-cover-source"]').forEach((input) => {
    input.addEventListener("change", () => {
      syncFields();
      if (source() === "video_frame") loadEpisode();
    });
  });
  episodeSelect?.addEventListener("change", loadEpisode);
  timeInput?.addEventListener("input", () => {
    timeLabel.textContent = formatTime(timeInput.value);
  });
  timeInput?.addEventListener("change", () => {
    updateFrame(Number(timeInput.value));
  });

  saveButton?.addEventListener("click", async () => {
    if (!selection) return;
    const payload = {
      source: source(),
      expected_updated_at: selection.updatedAt,
    };
    if (source() === "video_frame") {
      payload.video_id = Number(episodeSelect.value);
      payload.timestamp = Number(timeInput.value);
    }
    saveButton.disabled = true;
    status.textContent = "正在保存封面…";
    try {
      const response = await fetch(
        `/api/manage/series/${selection.seriesId}/seasons/${selection.seasonId}/cover`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
      );
      if (!response.ok) {
        throw new Error(await responseError(response, "保存封面失败"));
      }
      window.location.reload();
    } catch (error) {
      saveButton.disabled = false;
      status.textContent = error.message;
    }
  });

  restoreButton?.addEventListener("click", async () => {
    if (!selection) return;
    restoreButton.disabled = true;
    status.textContent = "正在恢复自动封面…";
    try {
      const response = await fetch(
        `/api/manage/series/${selection.seriesId}/seasons/${selection.seasonId}/cover`,
        { method: "DELETE" },
      );
      if (!response.ok) {
        throw new Error(await responseError(response, "恢复封面失败"));
      }
      window.location.reload();
    } catch (error) {
      restoreButton.disabled = false;
      status.textContent = error.message;
    }
  });

  document.querySelectorAll("[data-close-season-cover]").forEach((button) => {
    button.addEventListener("click", () => dialog.close());
  });
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-season-cover-season]");
    if (button) openDialog(button);
  });
})();
