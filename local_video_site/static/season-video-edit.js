(() => {
  const dialog = document.getElementById("episode-edit-dialog");
  const form = document.getElementById("episode-edit-form");
  if (!dialog || !form) return;

  const numberNode = document.getElementById("episode-edit-number");
  const videoNode = document.getElementById("episode-edit-video");
  const currentNode = document.getElementById("episode-edit-current");
  const titleInput = document.getElementById("episode-edit-title");
  const errorNode = document.getElementById("episode-edit-error");
  const submit = document.getElementById("episode-edit-submit");
  let selected = null;

  function errorMessage(payload, status) {
    const detail = payload?.detail;
    return detail?.message || detail || `HTTP ${status}`;
  }

  function clearError() {
    errorNode.textContent = "";
  }

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-edit-episode]");
    if (trigger) {
      selected = {
        seriesId: Number(trigger.dataset.editSeries),
        seasonId: Number(trigger.dataset.editSeason),
        episodeId: Number(trigger.dataset.editEpisode),
        episodeNumber: Number(trigger.dataset.editEpisodeNumber),
        episodeTitle: trigger.dataset.editEpisodeTitle || "",
        updatedAt: trigger.dataset.editEpisodeUpdatedAt || "",
      };
      numberNode.textContent = `第 ${selected.episodeNumber} 集`;
      videoNode.textContent = trigger.dataset.editVideoLabel || "无视频";
      currentNode.textContent = selected.episodeTitle || "未设置";
      titleInput.value = selected.episodeTitle;
      clearError();
      submit.disabled = !titleInput.value.trim();
      submit.textContent = "保存修改";
      dialog.showModal();
      titleInput.focus();
      titleInput.select();
      return;
    }
    if (event.target.closest("[data-cancel-episode-edit]")) {
      dialog.close();
    }
  });

  titleInput.addEventListener("input", () => {
    clearError();
    submit.disabled = !titleInput.value.trim();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!selected) return;
    const episodeTitle = titleInput.value.trim();
    if (!episodeTitle) {
      errorNode.textContent = "剧集标题不能为空";
      return;
    }
    submit.disabled = true;
    submit.textContent = "正在保存…";
    clearError();
    try {
      const response = await fetch(
        `/api/manage/series/${selected.seriesId}/seasons/${selected.seasonId}`
        + `/episodes/${selected.episodeId}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            episode_title: episodeTitle,
            expected_updated_at: selected.updatedAt,
          }),
        },
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(errorMessage(payload, response.status));
      }
      window.location.reload();
    } catch (requestError) {
      submit.disabled = false;
      submit.textContent = "保存修改";
      errorNode.textContent =
        requestError.message || String(requestError);
    }
  });

  dialog.addEventListener("close", () => {
    selected = null;
    clearError();
  });
})();
