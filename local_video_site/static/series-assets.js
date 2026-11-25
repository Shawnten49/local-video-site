(() => {
  function detailMessage(detail) {
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail.map(detailMessage).filter(Boolean).join("；");
    }
    if (detail && typeof detail === "object") {
      return detail.message || detail.msg || "";
    }
    return "";
  }

  async function responseError(response, fallback) {
    const payload = await response.json().catch(() => ({}));
    return (
      detailMessage(payload.detail)
      || detailMessage(payload.message)
      || fallback
    );
  }

  function formatTime(value) {
    const total = Math.max(0, Math.floor(Number(value) || 0));
    const minutes = Math.floor(total / 60);
    const seconds = total % 60;
    return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }

  const categoryDialog = document.getElementById("series-category-dialog");
  const categoryTitle = document.getElementById("series-category-title");
  const categorySearch = document.getElementById("series-category-search");
  const categoryOptions = document.getElementById("series-category-options");
  const categoryStatus = document.getElementById("series-category-status");
  const categorySave = document.getElementById("save-series-categories");
  let categorySelection = null;
  let categoryItems = [];

  function renderCategoryOptions() {
    if (!categorySelection) return;
    const query = categorySearch.value.trim().toLocaleLowerCase();
    categoryOptions.replaceChildren();
    for (const item of categoryItems) {
      const label = document.createElement("label");
      label.className = "series-category-option";
      label.hidden = Boolean(query) && !item.name.toLocaleLowerCase().includes(query);
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = String(item.id);
      checkbox.checked = Boolean(item.selected);
      const name = document.createElement("span");
      name.textContent = item.name;
      label.append(checkbox, name);
      if (item.is_system) {
        const badge = document.createElement("small");
        badge.textContent = "首页分类";
        label.append(badge);
      }
      categoryOptions.append(label);
    }
  }

  async function openCategoryDialog(button) {
    categorySelection = {
      seriesId: Number(button.dataset.seriesCategoryTarget),
      title: button.dataset.seriesCategoryTitle || "",
      updatedAt: button.dataset.seriesUpdatedAt || "",
    };
    categoryTitle.textContent = `分类管理 · ${categorySelection.title}`;
    categorySearch.value = "";
    categoryStatus.textContent = "";
    categoryOptions.replaceChildren();
    categorySave.disabled = true;
    categoryDialog.showModal();
    try {
      const response = await fetch(
        `/api/manage/series/${categorySelection.seriesId}/manual-category-options`,
        { cache: "no-store" },
      );
      if (!response.ok) {
        throw new Error(await responseError(response, "读取分类失败"));
      }
      const payload = await response.json();
      categoryItems = payload.items || [];
      categoryTitle.textContent = `分类管理 · ${payload.title || categorySelection.title}`;
      categorySelection.updatedAt = payload.updated_at || categorySelection.updatedAt;
      renderCategoryOptions();
      categorySave.disabled = false;
    } catch (error) {
      categoryStatus.textContent = error.message;
    }
  }

  categorySearch?.addEventListener("input", renderCategoryOptions);
  categorySave?.addEventListener("click", async () => {
    if (!categorySelection) return;
    const selected = [...categoryOptions.querySelectorAll("input:checked")]
      .map((input) => Number(input.value));
    categorySave.disabled = true;
    categoryStatus.textContent = "正在保存分类…";
    try {
      const response = await fetch(
        `/api/manage/series/${categorySelection.seriesId}/manual-categories`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            manual_category_ids: selected,
            expected_updated_at: categorySelection.updatedAt,
          }),
        },
      );
      if (!response.ok) {
        throw new Error(await responseError(response, "保存分类失败"));
      }
      window.location.reload();
    } catch (error) {
      categorySave.disabled = false;
      categoryStatus.textContent = error.message;
    }
  });

  function closeCategoryDialog() {
    categoryDialog?.close();
  }

  document.querySelectorAll("[data-close-series-category]").forEach((button) => {
    button.addEventListener("click", closeCategoryDialog);
  });

  const coverDialog = document.getElementById("series-cover-dialog");
  const coverTitle = document.getElementById("series-cover-title");
  const coverPreview = document.getElementById("series-cover-preview");
  const coverStatus = document.getElementById("series-cover-status");
  const coverSeason = document.getElementById("series-cover-season");
  const coverEpisode = document.getElementById("series-cover-episode");
  const coverTime = document.getElementById("series-cover-time");
  const coverTimeLabel = document.getElementById("series-cover-time-label");
  const seasonField = document.getElementById("series-cover-season-field");
  const episodeField = document.getElementById("series-cover-episode-field");
  const timeField = document.getElementById("series-cover-time-field");
  const coverSave = document.getElementById("save-series-cover");
  const coverRestore = document.getElementById("restore-series-cover");
  let coverSelection = null;
  let coverEditor = null;
  let coverPreviewSerial = 0;

  function selectedSource() {
    return document.querySelector(
      'input[name="series-cover-source"]:checked',
    )?.value || "auto";
  }

  function selectedSeason() {
    return coverEditor?.seasons?.find(
      (item) => String(item.season_id) === coverSeason.value,
    ) || null;
  }

  function selectedEpisode() {
    const season = selectedSeason();
    return season?.episodes?.find(
      (item) => String(item.video_id) === coverEpisode.value,
    ) || null;
  }

  function setCoverPreview(url) {
    if (url) {
      coverPreview.src = url;
    } else {
      coverPreview.removeAttribute("src");
    }
  }

  function syncCoverFields() {
    const source = selectedSource();
    seasonField.hidden = source === "auto";
    episodeField.hidden = source !== "video_frame";
    timeField.hidden = source !== "video_frame";
    if (source === "auto") {
      setCoverPreview(coverEditor?.cover_url || "");
    } else if (source === "season") {
      setCoverPreview(selectedSeason()?.cover_url || "");
    }
  }

  function populateEpisodes() {
    const season = selectedSeason();
    coverEpisode.replaceChildren();
    for (const episode of season?.episodes || []) {
      const option = document.createElement("option");
      option.value = String(episode.video_id);
      option.textContent = `${String(episode.episode_number).padStart(2, "0")} · ${episode.title}`;
      coverEpisode.append(option);
    }
    if (selectedSource() === "video_frame" && coverEpisode.options.length) {
      loadVideoFramePreview();
    }
  }

  async function loadVideoFramePreview() {
    const episode = selectedEpisode();
    if (!episode) return;
    const serial = ++coverPreviewSerial;
    coverStatus.textContent = "正在读取视频信息…";
    try {
      const response = await fetch(
        `/api/videos/${episode.video_id}/cover-editor`,
        { cache: "no-store" },
      );
      if (!response.ok) {
        throw new Error(await responseError(response, "读取视频信息失败"));
      }
      const payload = await response.json();
      if (serial !== coverPreviewSerial) return;
      coverTime.min = "0";
      coverTime.max = String(Math.max(0, Number(payload.duration) - 0.1));
      coverTime.step = String(payload.preview_step_seconds || 0.5);
      coverTime.value = String(payload.default_timestamp || 0);
      coverTimeLabel.textContent = formatTime(coverTime.value);
      await updateVideoFramePreview(Number(coverTime.value));
    } catch (error) {
      coverStatus.textContent = error.message;
    }
  }

  async function updateVideoFramePreview(timestamp) {
    const episode = selectedEpisode();
    if (!episode) return;
    const serial = ++coverPreviewSerial;
    coverStatus.textContent = "正在提取画面…";
    try {
      const query = new URLSearchParams({ timestamp: String(timestamp) });
      const response = await fetch(
        `/api/videos/${episode.video_id}/cover-preview?${query}`,
      );
      if (!response.ok) {
        throw new Error(await responseError(response, "画面提取失败"));
      }
      const blob = await response.blob();
      if (serial !== coverPreviewSerial) return;
      const url = URL.createObjectURL(blob);
      coverPreview.src = url;
      coverStatus.textContent = "";
    } catch (error) {
      coverStatus.textContent = error.message;
    }
  }

  async function openCoverDialog(button) {
    coverSelection = {
      seriesId: Number(button.dataset.seriesCoverTarget),
      title: button.dataset.seriesCoverTitle || "",
      updatedAt: button.dataset.seriesUpdatedAt || "",
    };
    coverEditor = null;
    coverPreviewSerial += 1;
    coverTitle.textContent = `视频封面 · ${coverSelection.title}`;
    coverStatus.textContent = "正在读取封面信息…";
    coverPreview.removeAttribute("src");
    coverSave.disabled = true;
    coverRestore.disabled = true;
    coverDialog.showModal();
    try {
      const response = await fetch(
        `/api/manage/series/${coverSelection.seriesId}/cover-editor`,
        { cache: "no-store" },
      );
      if (!response.ok) {
        throw new Error(await responseError(response, "读取封面信息失败"));
      }
      coverEditor = await response.json();
      coverSelection.updatedAt = coverEditor.updated_at || coverSelection.updatedAt;
      coverTitle.textContent = `视频封面 · ${coverEditor.title || coverSelection.title}`;
      coverSeason.replaceChildren();
      for (const season of coverEditor.seasons || []) {
        const option = document.createElement("option");
        option.value = String(season.season_id);
        option.textContent = season.title;
        coverSeason.append(option);
      }
      document.querySelector(
        'input[name="series-cover-source"][value="auto"]',
      ).checked = true;
      coverStatus.textContent = "";
      coverSave.disabled = false;
      coverRestore.disabled = false;
      syncCoverFields();
      populateEpisodes();
    } catch (error) {
      coverSave.disabled = true;
      coverRestore.disabled = false;
      coverStatus.textContent = error.message;
    }
  }

  document.querySelectorAll('input[name="series-cover-source"]').forEach((input) => {
    input.addEventListener("change", () => {
      syncCoverFields();
      if (selectedSource() === "video_frame") {
        loadVideoFramePreview();
      }
    });
  });
  coverSeason?.addEventListener("change", () => {
    populateEpisodes();
    syncCoverFields();
  });
  coverEpisode?.addEventListener("change", loadVideoFramePreview);
  coverTime?.addEventListener("input", () => {
    coverTimeLabel.textContent = formatTime(coverTime.value);
  });
  coverTime?.addEventListener("change", () => {
    updateVideoFramePreview(Number(coverTime.value));
  });

  coverSave?.addEventListener("click", async () => {
    if (!coverSelection) return;
    const source = selectedSource();
    const season = selectedSeason();
    const episode = selectedEpisode();
    const body = {
      source,
      expected_updated_at: coverSelection.updatedAt,
    };
    if (source === "season") {
      body.season_id = season?.season_id;
    } else if (source === "video_frame") {
      body.season_id = season?.season_id;
      body.video_id = episode?.video_id;
      body.timestamp = Number(coverTime.value);
    }
    coverSave.disabled = true;
    coverStatus.textContent = "正在生成并保存封面…";
    try {
      const response = await fetch(
        `/api/manage/series/${coverSelection.seriesId}/cover`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
      );
      if (!response.ok) {
        throw new Error(await responseError(response, "保存封面失败"));
      }
      window.location.reload();
    } catch (error) {
      coverSave.disabled = false;
      coverStatus.textContent = error.message;
    }
  });

  coverRestore?.addEventListener("click", async () => {
    if (!coverSelection) return;
    coverRestore.disabled = true;
    coverStatus.textContent = "正在恢复自动封面…";
    try {
      const response = await fetch(
        `/api/manage/series/${coverSelection.seriesId}/cover`,
        { method: "DELETE" },
      );
      if (!response.ok) {
        throw new Error(await responseError(response, "恢复封面失败"));
      }
      window.location.reload();
    } catch (error) {
      coverRestore.disabled = false;
      coverStatus.textContent = error.message;
    }
  });

  document.querySelectorAll("[data-close-series-cover]").forEach((button) => {
    button.addEventListener("click", () => coverDialog?.close());
  });

  document.addEventListener("click", (event) => {
    const categoryButton = event.target.closest("[data-series-category-target]");
    if (categoryButton) {
      openCategoryDialog(categoryButton);
      return;
    }
    const coverButton = event.target.closest("[data-series-cover-target]");
    if (coverButton) {
      openCoverDialog(coverButton);
    }
  });
})();
