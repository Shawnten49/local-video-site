(() => {
  const refreshStorage = document.getElementById("refresh-storage-state");
  const detailDialog = document.getElementById("video-detail-dialog");
  const detailTitle = document.getElementById("video-detail-title");
  const detailContent = document.getElementById("video-detail-content");
  const detailError = document.getElementById("video-detail-error");
  const editDialog = document.getElementById("edit-video-dialog");
  const editDialogTitle = document.getElementById("edit-video-dialog-title");
  const displayTitleForm = document.getElementById(
    "edit-display-title-form",
  );
  const displayTitleInput = document.getElementById(
    "edit-display-title-input",
  );
  const displayTitleError = document.getElementById(
    "edit-display-title-error",
  );
  const restoreDisplayTitle = document.getElementById(
    "restore-default-display-title",
  );
  const fileNameForm = document.getElementById("edit-file-name-form");
  const fileNameInput = document.getElementById("edit-file-name-input");
  const fileExtension = document.getElementById("edit-file-extension");
  const currentFilePath = document.getElementById(
    "edit-file-current-path",
  );
  const fileNameHint = document.getElementById("edit-file-name-hint");
  const fileNameError = document.getElementById("edit-file-name-error");
  const accessLevelForm = document.getElementById(
    "edit-access-level-form",
  );
  const accessLevelSelect = document.getElementById(
    "edit-access-level-select",
  );
  const accessLevelError = document.getElementById(
    "edit-access-level-error",
  );
  const editOriginalUrl = document.getElementById("edit-original-url");
  const trimDialog = document.getElementById("trim-video-dialog");
  const trimDialogTitle = document.getElementById("trim-video-title");
  const trimForm = document.getElementById("trim-video-form");
  const trimStartInput = document.getElementById("trim-start-input");
  const analyzeTrimButton = document.getElementById("analyze-trim");
  const trimAnalysis = document.getElementById("trim-analysis");
  const trimAnalysisSummary = document.getElementById(
    "trim-analysis-summary",
  );
  const trimAfterHint = document.getElementById(
    "trim-after-keyframe-hint",
  );
  const trimBeforeHint = document.getElementById(
    "trim-before-keyframe-hint",
  );
  const trimProgressWrap = document.getElementById(
    "trim-progress-wrap",
  );
  const trimProgress = document.getElementById("trim-progress");
  const trimProgressText = document.getElementById("trim-progress-text");
  const trimError = document.getElementById("trim-video-error");
  const startTrimButton = document.getElementById("start-trim-video");
  const deleteDialog = document.getElementById("managed-delete-dialog");
  const deleteStep = document.getElementById("managed-delete-confirm-step");
  const deleteForm = document.getElementById("managed-delete-form");
  const deleteTitle = document.getElementById("managed-delete-title");
  const deletePassword = document.getElementById("managed-delete-password");
  const deleteSubmit = document.getElementById("managed-delete-submit");
  const deleteError = document.getElementById("managed-delete-error");
  const formatDialog = document.getElementById("format-mp4-dialog");
  const formatForm = document.getElementById("format-mp4-form");
  const formatTitle = document.getElementById("format-mp4-title");
  const formatPath = document.getElementById("format-mp4-path");
  const formatCodecs = document.getElementById("format-mp4-codecs");
  const formatWarning = document.getElementById("format-mp4-warning");
  const formatError = document.getElementById("format-mp4-error");
  const formatConfirm = document.getElementById("confirm-format-mp4");
  const formatRemuxRadio = formatForm?.querySelector(
    'input[value="container_remux"]',
  );
  let formattingVideoId = null;
  let deletingId = null;
  let deleteBusy = false;
  let editingVideo = null;
  let trimmingVideo = null;
  let trimOptions = null;
  let trimPollTimer = null;
  const shownKeys = new Set();

  function label(key) {
    const labels = {
      id: "ID",
      title: "名称",
      default_title: "默认标题",
      file_name: "文件名称",
      display_title_override: "展示标题覆盖值",
      path: "数据库路径",
      resolved_path: "当前完整路径",
      directory: "目录",
      extension: "格式",
      size: "文件大小",
      duration: "时长",
      width: "宽度",
      height: "高度",
      video_codec: "视频编码",
      audio_codec: "音频编码",
      access_level: "访问级别",
      access_level_label: "访问级别",
      storage_state_label: "存储状态",
      storage_relative_path: "存储相对路径",
      original_url: "原始网页",
      download_source_url: "下载来源",
      thumbnail_path: "缩略图",
      preview_path: "预览文件",
      preview_status: "预览状态",
      metadata_status: "元数据状态",
      metadata_error: "元数据错误",
      normalization_status: "标准化状态",
      normalization_error: "标准化错误",
      missing: "缺失标记",
      is_deleted: "已软删除",
      created_at: "创建时间",
      updated_at: "更新时间",
      storage_changed_at: "存储位置变更时间",
    };
    return labels[key] || key;
  }

  function makeValue(value) {
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }

  function renderSection(title, entries) {
    const section = document.createElement("section");
    section.className = "management-detail-section";
    const heading = document.createElement("h3");
    heading.textContent = title;
    section.appendChild(heading);
    const table = document.createElement("dl");
    table.className = "management-detail-list";
    for (const [key, value] of entries) {
      if (value === undefined) continue;
      shownKeys.add(key);
      const row = document.createElement("div");
      const term = document.createElement("dt");
      const description = document.createElement("dd");
      term.textContent = label(key);
      description.textContent = makeValue(value);
      row.append(term, description);
      table.appendChild(row);
    }
    section.appendChild(table);
    detailContent.appendChild(section);
  }

  async function openDetail(videoId) {
    detailContent.replaceChildren();
    detailError.textContent = "";
    detailTitle.textContent = `视频 #${videoId}`;
    detailDialog.showModal();
    try {
      const response = await fetch(`/api/manage/videos/${videoId}`);
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || `HTTP ${response.status}`);
      }
      const data = await response.json();
      const video = data.video;
      detailTitle.textContent = `#${video.id} ${video.title}`;
      renderSection("基本信息", [
        ["id", video.id],
        ["title", video.title],
        ["default_title", video.default_title],
        ["file_name", data.display.file_name],
        ["size", `${data.display.size} (${video.size} bytes)`],
        ["extension", video.extension],
        ["directory", video.directory],
        ["access_level_label", video.access_level_label],
        ["path", video.path],
        ["resolved_path", video.resolved_path],
        ["original_url", video.original_url],
        ["download_source_url", video.download_source_url],
      ]);
      renderSection("视频属性", [
        ["duration", data.display.duration],
        ["width", video.width],
        ["height", video.height],
        ["resolution", data.display.resolution],
        ["video_codec", video.video_codec],
        ["audio_codec", video.audio_codec],
      ]);
      renderSection("存储信息", [
        ["storage_state_label", video.storage_state_label],
        ["storage_relative_path", video.storage_relative_path],
        ["storage_changed_at", video.storage_changed_at],
        ["missing", video.missing ? "是" : "否"],
        ["is_deleted", video.is_deleted ? "是" : "否"],
      ]);
      renderSection("生成状态", [
        ["thumbnail_path", video.thumbnail_path],
        ["preview_path", video.preview_path],
        ["preview_status", video.preview_status],
        ["metadata_status", video.metadata_status],
        ["metadata_error", video.metadata_error],
        ["normalization_status", video.normalization_status],
        ["normalization_error", video.normalization_error],
      ]);
      renderSection("关联信息", [
        ["收藏", data.favorite ? "是" : "否"],
        ["分类", data.categories.map((item) => item.name).join("、")],
        ["人工标签", data.manual_categories.map((item) => item.name).join("、")],
        ["演员", data.actors.map((item) => item.name).join("、")],
        ["下载任务", data.download_tasks.map((item) => `#${item.id} ${item.status}`).join("、")],
        ["迁移记录", data.migrations.map((item) => `#${item.id} ${item.direction} ${item.status}`).join("、")],
      ]);
      renderSection("时间", [
        ["created_at", video.created_at],
        ["updated_at", video.updated_at],
      ]);
      const remaining = Object.entries(video).filter(
        ([key]) => !shownKeys.has(key),
      );
      if (remaining.length) {
        renderSection("其他属性", remaining);
      }
    } catch (error) {
      detailError.textContent = error.message;
    }
  }

  function resetDelete() {
    deletingId = null;
    deleteBusy = false;
    deleteStep.hidden = false;
    deleteForm.hidden = true;
    deletePassword.value = "";
    deleteSubmit.disabled = true;
    deleteSubmit.textContent = "确认删除";
    deleteError.textContent = "";
  }

  function openDelete(button) {
    resetDelete();
    deletingId = Number(button.dataset.deleteManagedVideo);
    deleteTitle.textContent = button.dataset.videoTitle || `视频 #${deletingId}`;
    deleteDialog.showModal();
  }

  function fileValidationError(value) {
    const title = String(value || "").normalize("NFKC")
      .replace(/\s+/g, " ").trim();
    if (!title) return "请输入文件名称";
    if (title === "." || title === ".." || title.startsWith(".")) {
      return "文件名不能以点开头，也不能是 . 或 ..";
    }
    if (title.endsWith(" ") || title.endsWith(".")) {
      return "文件名不能以空格或点结尾";
    }
    if (/[\/\\\0]/.test(title)) {
      return "文件名不能包含 /、\\ 或空字符";
    }
    if (/[\u0000-\u001f]/.test(title)) {
      return "文件名不能包含控制字符";
    }
    if (new TextEncoder().encode(title).length > 180) {
      return "文件名过长";
    }
    return "";
  }

  function fileRenameBlockedReason(video) {
    if (video.is_deleted) return "视频已删除，不能修改文件名";
    if (video.missing || video.storage_state === "file_missing") {
      return "视频文件缺失，不能修改文件名";
    }
    if (video.storage_state === "external_offline") {
      return "外置盘当前离线，不能修改文件名";
    }
    if (video.storage_state === "migrating") {
      return "视频正在迁移，不能修改文件名";
    }
    return "";
  }

  function syncFileFormState() {
    if (!editingVideo) return;
    const reason = fileRenameBlockedReason(editingVideo.video);
    const disabled = Boolean(reason);
    fileNameInput.disabled = disabled;
    fileNameForm.querySelector("[type=submit]").disabled = disabled;
    fileNameHint.textContent = reason || (
      "只修改实际文件，展示标题保持不变。"
    );
  }

  async function openEdit(videoId) {
    editDialogTitle.textContent = `修改视频 #${videoId}`;
    displayTitleError.textContent = "";
    fileNameError.textContent = "";
    accessLevelError.textContent = "";
    displayTitleInput.value = "";
    fileNameInput.value = "";
    currentFilePath.textContent = "";
    editOriginalUrl.textContent = "";
    editDialog.showModal();
    try {
      const response = await fetch(`/api/manage/videos/${videoId}`);
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || `HTTP ${response.status}`);
      }
      const data = await response.json();
      editingVideo = data;
      const video = data.video;
      editDialogTitle.textContent = `#${video.id} ${video.title}`;
      displayTitleInput.value = video.title || "";
      fileNameInput.value = data.display.file_stem || "";
      fileExtension.textContent = video.extension || "";
      currentFilePath.textContent = video.resolved_path || video.path || "";
      editOriginalUrl.textContent = video.original_url || "未记录";
      accessLevelSelect.value = String(video.access_level ?? 0);
      syncFileFormState();
      displayTitleInput.focus();
      displayTitleInput.select();
    } catch (error) {
      displayTitleError.textContent = error.message;
    }
  }

  async function saveDisplayTitle(displayTitle) {
    if (!editingVideo || !editingVideo.video) return;
    displayTitleError.textContent = "";
    try {
      const response = await fetch(
        `/api/manage/videos/${editingVideo.video.id}/display-title`,
        {
          method: "PATCH",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            display_title: displayTitle,
            expected_updated_at: editingVideo.video.updated_at || "",
          }),
        },
      );
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || `HTTP ${response.status}`);
      }
      window.location.reload();
    } catch (error) {
      displayTitleError.textContent = error.message || String(error);
    }
  }

  function parseTrimSeconds(value) {
    const raw = String(value || "").trim();
    if (!raw) return NaN;
    if (/^\d+(?:\.\d+)?$/.test(raw)) return Number(raw);
    const parts = raw.split(":");
    if (parts.length !== 3) return NaN;
    const hours = Number(parts[0]);
    const minutes = Number(parts[1]);
    const seconds = Number(parts[2]);
    if (
      !Number.isFinite(hours)
      || !Number.isFinite(minutes)
      || !Number.isFinite(seconds)
      || hours < 0
      || minutes < 0
      || minutes >= 60
      || seconds < 0
      || seconds >= 60
    ) return NaN;
    return hours * 3600 + minutes * 60 + seconds;
  }

  function formatTrimSeconds(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number.toFixed(3) : "—";
  }

  function selectedTrimMode() {
    return trimForm.querySelector(
      'input[name="trim-mode"]:checked',
    )?.value || "";
  }

  function resetTrimDialog(video) {
    trimmingVideo = video;
    trimOptions = null;
    trimStartInput.value = "";
    trimAnalysis.hidden = true;
    trimProgressWrap.hidden = true;
    trimProgress.value = 0;
    trimProgressText.textContent = "等待开始";
    trimError.textContent = "";
    startTrimButton.disabled = true;
    startTrimButton.textContent = "开始剪辑";
    trimDialogTitle.textContent = `#${video.id} ${video.title}`;
  }

  async function openTrim(videoId) {
    if (trimPollTimer) window.clearTimeout(trimPollTimer);
    trimError.textContent = "";
    try {
      const response = await fetch(`/api/manage/videos/${videoId}`);
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || `HTTP ${response.status}`);
      }
      const data = await response.json();
      resetTrimDialog(data.video);
      trimDialog.showModal();
      trimStartInput.focus();
    } catch (error) {
      window.alert(error.message || String(error));
    }
  }

  async function analyzeTrim() {
    if (!trimmingVideo) return;
    const startSeconds = parseTrimSeconds(trimStartInput.value);
    if (!Number.isFinite(startSeconds) || startSeconds <= 0) {
      trimError.textContent = "请输入有效的片头结束时间";
      trimAnalysis.hidden = true;
      startTrimButton.disabled = true;
      return;
    }
    analyzeTrimButton.disabled = true;
    analyzeTrimButton.textContent = "分析中…";
    trimError.textContent = "";
    try {
      const response = await fetch(
        `/api/manage/videos/${trimmingVideo.id}/trim/preview`,
        {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({start_seconds: startSeconds}),
        },
      );
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || `HTTP ${response.status}`);
      }
      trimOptions = await response.json();
      const previous = trimOptions.previous_keyframe;
      const following = trimOptions.next_keyframe;
      trimAnalysisSummary.textContent = (
        `视频时长 ${formatTrimSeconds(trimOptions.duration)} 秒 · `
        + `前关键帧 ${formatTrimSeconds(previous)} 秒 · `
        + `后关键帧 ${formatTrimSeconds(following)} 秒`
      );
      trimAfterHint.textContent = following == null
        ? "目标后没有可用关键帧"
        : `从 ${formatTrimSeconds(following)} 秒开始，可能多切约 `
          + `${formatTrimSeconds(Math.max(0, following - startSeconds))} 秒正片。`;
      trimBeforeHint.textContent = previous == null
        ? "目标前没有可用关键帧"
        : `从 ${formatTrimSeconds(previous)} 秒开始，可能保留约 `
          + `${formatTrimSeconds(Math.max(0, startSeconds - previous))} 秒广告。`;
      const afterRadio = trimForm.querySelector(
        'input[name="trim-mode"][value="after_keyframe"]',
      );
      const beforeRadio = trimForm.querySelector(
        'input[name="trim-mode"][value="before_keyframe"]',
      );
      afterRadio.disabled = following == null;
      beforeRadio.disabled = previous == null;
      const preferred = trimOptions.recommended_mode;
      const selected = trimForm.querySelector(
        `input[name="trim-mode"][value="${preferred}"]`,
      );
      if (selected && !selected.disabled) selected.checked = true;
      trimAnalysis.hidden = false;
      startTrimButton.disabled = false;
    } catch (error) {
      trimOptions = null;
      trimAnalysis.hidden = true;
      startTrimButton.disabled = true;
      trimError.textContent = error.message || String(error);
    } finally {
      analyzeTrimButton.disabled = false;
      analyzeTrimButton.textContent = "分析关键帧";
    }
  }

  function renderTrimJob(job) {
    const progress = Math.max(0, Math.min(100, Number(job.progress_percent || 0)));
    trimProgressWrap.hidden = false;
    trimProgress.value = progress;
    trimProgressText.textContent = (
      `${job.stage || job.status || "处理中"} · ${progress.toFixed(0)}%`
    );
    if (job.status === "success") {
      window.location.reload();
      return;
    }
    if (job.status === "failed" || job.status === "cancelled") {
      trimError.textContent = (
        job.error_detail
        || (job.status === "cancelled" ? "剪辑已取消" : "片头剪辑失败")
      );
      startTrimButton.disabled = false;
      startTrimButton.textContent = "开始剪辑";
      return;
    }
    trimPollTimer = window.setTimeout(() => pollTrimJob(job.job_id), 1200);
  }

  async function pollTrimJob(jobId) {
    try {
      const response = await fetch(`/api/video-trim-jobs/${jobId}`);
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || `HTTP ${response.status}`);
      }
      renderTrimJob(await response.json());
    } catch (error) {
      trimError.textContent = error.message || String(error);
      startTrimButton.disabled = false;
      startTrimButton.textContent = "开始剪辑";
    }
  }

  async function saveAccessLevel(button) {
    const videoId = Number(button.dataset.saveAccessLevel);
    const select = document.querySelector(
      `[data-access-level="${videoId}"]`,
    );
    if (!videoId || !select) return;
    button.disabled = true;
    try {
      const response = await fetch(
        `/api/manage/videos/${videoId}/access-level`,
        {
          method: "PATCH",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            access_level: Number(select.value),
          }),
        },
      );
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || `HTTP ${response.status}`);
      }
      const result = await response.json();
      const message = result.affected_count > 1
        ? `访问级别已更新，并同步 ${result.affected_count} 集`
        : "访问级别已更新";
      window.alert(message);
      window.location.reload();
    } catch (error) {
      window.alert(error.message || String(error));
      button.disabled = false;
    }
  }

  document.addEventListener("click", (event) => {
    const view = event.target.closest("[data-view-video]");
    if (view) {
      openDetail(Number(view.dataset.viewVideo));
      return;
    }
    const edit = event.target.closest("[data-edit-video]");
    if (edit) {
      openEdit(Number(edit.dataset.editVideo));
      return;
    }
    const trim = event.target.closest("[data-trim-video]");
    if (trim) {
      openTrim(Number(trim.dataset.trimVideo));
      return;
    }
    const formatMp4 = event.target.closest("[data-format-mp4]");
    if (formatMp4) {
      openFormatDialog(formatMp4);
      return;
    }
    const remove = event.target.closest("[data-delete-managed-video]");
    if (remove) {
      openDelete(remove);
      return;
    }
    const saveAccess = event.target.closest("[data-save-access-level]");
    if (saveAccess) {
      saveAccessLevel(saveAccess);
    }
  });

  function selectedFormatOperation() {
    return formatForm?.querySelector(
      'input[name="format-operation"]:checked',
    )?.value || "container_remux";
  }

  function updateFormatMethod() {
    const transcode = selectedFormatOperation() === "standard_transcode";
    if (formatWarning) formatWarning.hidden = !transcode;
    if (formatConfirm) {
      formatConfirm.textContent = transcode
        ? "确认有损编码转译"
        : "确认创建容器封装任务";
      formatConfirm.classList.toggle("danger-button", transcode);
      formatConfirm.classList.toggle("download-primary-button", !transcode);
    }
  }

  function openFormatDialog(button) {
    formattingVideoId = Number(button.dataset.formatMp4);
    const remuxSupported =
      button.dataset.formatRemuxSupported === "1";
    if (formatTitle) formatTitle.textContent =
      `#${formattingVideoId} ${button.dataset.formatTitle || ""}`.trim();
    if (formatPath) formatPath.textContent = button.dataset.formatPath || "";
    if (formatCodecs) formatCodecs.textContent =
      button.dataset.formatCodecs || "";
    if (formatError) formatError.textContent = "";
    formatForm?.querySelectorAll(
      'input[name="format-operation"]',
    ).forEach((input) => {
      input.disabled =
        input.value === "container_remux" && !remuxSupported;
      input.checked = remuxSupported
        ? input.value === "container_remux"
        : input.value === "standard_transcode";
    });
    formatRemuxRadio?.closest(".format-mp4-method")?.classList.toggle(
      "is-unavailable",
      !remuxSupported,
    );
    if (formatWarning) {
      formatWarning.textContent = remuxSupported
        ? "编码转译会重新编码视频和音频，画质和音质可能发生变化，处理时间较长。"
        : "RMVB 不能进行无损容器封装，必须选择编码转译为 H.264/AAC。";
    }
    if (formatConfirm) formatConfirm.disabled = false;
    formatDialog?.showModal();
    updateFormatMethod();
  }

  formatForm?.querySelectorAll(
    'input[name="format-operation"]',
  ).forEach((input) => {
    input.addEventListener("change", updateFormatMethod);
  });

  formatForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!formattingVideoId) return;
    const operation = selectedFormatOperation();
    formatConfirm.disabled = true;
    formatConfirm.textContent = "正在创建任务…";
    if (formatError) formatError.textContent = "";
    try {
      const response = await fetch(
        `/api/manage/videos/${formattingVideoId}/format-job`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            operation,
            confirmed: true,
            confirm_lossy: operation === "standard_transcode",
            confirmation_version: 1,
          }),
        },
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = payload.detail;
        throw new Error(
          detail?.message || detail || `HTTP ${response.status}`,
        );
      }
      formatDialog?.close();
      window.setTimeout(() => window.location.reload(), 500);
    } catch (error) {
      formatConfirm.disabled = false;
      if (formatError) {
        formatError.textContent = error.message || String(error);
      }
      updateFormatMethod();
    }
  });

  displayTitleForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    const title = String(displayTitleInput.value || "")
      .replace(/\s+/g, " ").trim();
    if (!title) {
      displayTitleError.textContent = "请输入展示标题";
      return;
    }
    saveDisplayTitle(title);
  });
  restoreDisplayTitle?.addEventListener("click", () => {
    saveDisplayTitle(null);
  });
  fileNameForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!editingVideo || !editingVideo.video) return;
    const fileStem = String(fileNameInput.value || "")
      .normalize("NFKC").replace(/\s+/g, " ").trim();
    const error = fileValidationError(fileStem);
    if (error) {
      fileNameError.textContent = error;
      return;
    }
    fileNameError.textContent = "";
    const submit = fileNameForm.querySelector("[type=submit]");
    submit.disabled = true;
    submit.textContent = "保存中…";
    try {
      const response = await fetch(
        `/api/manage/videos/${editingVideo.video.id}/file-name`,
        {
          method: "PATCH",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            file_stem: fileStem,
            expected_path: (
              editingVideo.video.resolved_path
              || editingVideo.video.path
              || ""
            ),
          }),
        },
      );
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || `HTTP ${response.status}`);
      }
      window.location.reload();
    } catch (requestError) {
      fileNameError.textContent = requestError.message || String(requestError);
      submit.disabled = false;
      submit.textContent = "保存文件名";
    }
  });
  accessLevelForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!editingVideo || !editingVideo.video) return;
    const submit = document.getElementById("save-edit-access-level");
    submit.disabled = true;
    submit.textContent = "保存中…";
    accessLevelError.textContent = "";
    try {
      const response = await fetch(
        `/api/manage/videos/${editingVideo.video.id}/access-level`,
        {
          method: "PATCH",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            access_level: Number(accessLevelSelect.value),
          }),
        },
      );
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || `HTTP ${response.status}`);
      }
      window.location.reload();
    } catch (error) {
      accessLevelError.textContent = error.message || String(error);
      submit.disabled = false;
      submit.textContent = "保存访问级别";
    }
  });
  analyzeTrimButton?.addEventListener("click", analyzeTrim);
  trimStartInput?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      analyzeTrim();
    }
  });
  trimForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!trimmingVideo || !trimOptions) {
      trimError.textContent = "请先分析关键帧并选择剪辑方式";
      return;
    }
    const mode = selectedTrimMode();
    if (!mode) {
      trimError.textContent = "请选择剪辑方式";
      return;
    }
    if (
      mode === "precise"
      && !window.confirm(
        "精确剪辑需要重新编码剩余视频，可能耗时较长并造成轻微画质变化。继续吗？",
      )
    ) return;
    const startSeconds = parseTrimSeconds(trimStartInput.value);
    startTrimButton.disabled = true;
    startTrimButton.textContent = "正在创建任务…";
    trimError.textContent = "";
    try {
      const response = await fetch(
        `/api/manage/videos/${trimmingVideo.id}/trim`,
        {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            start_seconds: startSeconds,
            mode,
            expected_path: trimmingVideo.resolved_path || trimmingVideo.path,
          }),
        },
      );
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || `HTTP ${response.status}`);
      }
      renderTrimJob(await response.json());
    } catch (error) {
      trimError.textContent = error.message || String(error);
      startTrimButton.disabled = false;
      startTrimButton.textContent = "开始剪辑";
    }
  });

  refreshStorage?.addEventListener("click", async () => {
    refreshStorage.disabled = true;
    const original = refreshStorage.textContent;
    refreshStorage.textContent = "后台刷新中…";
    try {
      const response = await fetch("/api/manage/storage/refresh", {
        method: "POST",
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      window.setTimeout(() => window.location.reload(), 3000);
    } catch (_) {
      refreshStorage.disabled = false;
      refreshStorage.textContent = original;
    }
  });

  document.querySelector("[data-close-video-detail]")?.addEventListener("click", () => {
    detailDialog.close();
  });
  document.querySelectorAll("[data-close-edit-video]").forEach((button) => {
    button.addEventListener("click", () => editDialog.close());
  });
  document.querySelectorAll("[data-close-trim-video]").forEach((button) => {
    button.addEventListener("click", () => trimDialog.close());
  });
  document.querySelectorAll("[data-close-format-mp4]").forEach((button) => {
    button.addEventListener("click", () => formatDialog.close());
  });
  document.querySelector("[data-continue-managed-delete]")?.addEventListener("click", () => {
    deleteStep.hidden = true;
    deleteForm.hidden = false;
    deletePassword.focus();
  });
  document.querySelectorAll("[data-cancel-managed-delete]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!deleteBusy) deleteDialog.close();
    });
  });
  deletePassword.addEventListener("input", () => {
    deleteSubmit.disabled = !deletePassword.value || deleteBusy;
  });
  deleteForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!deletingId || !deletePassword.value || deleteBusy) return;
    deleteBusy = true;
    deleteSubmit.disabled = true;
    deleteSubmit.textContent = "正在创建任务…";
    deleteError.textContent = "";
    try {
      const response = await fetch(`/api/videos/${deletingId}`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          password: deletePassword.value,
          return_to: "/manage/videos",
        }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        const detail = payload.detail;
        throw new Error(
          detail?.message || detail || `HTTP ${response.status}`,
        );
      }
      const result = await response.json();
      window.location.href = result.redirect_url || "/manage/delete-tasks";
    } catch (error) {
      deleteBusy = false;
      deleteSubmit.disabled = !deletePassword.value;
      deleteSubmit.textContent = "确认删除";
      deleteError.textContent = error.message;
    }
  });
  deleteDialog.addEventListener("close", resetDelete);
  detailDialog.addEventListener("close", () => {
    detailContent.replaceChildren();
    detailError.textContent = "";
  });
  editDialog?.addEventListener("close", () => {
    editingVideo = null;
    displayTitleError.textContent = "";
    fileNameError.textContent = "";
    accessLevelError.textContent = "";
  });
  editDialog?.addEventListener("cancel", (event) => {
    event.preventDefault();
    editDialog.close();
  });
  editDialog?.addEventListener("click", (event) => {
    if (event.target === editDialog) editDialog.close();
  });
  trimDialog?.addEventListener("close", () => {
    if (trimPollTimer) window.clearTimeout(trimPollTimer);
    trimPollTimer = null;
    trimmingVideo = null;
    trimOptions = null;
  });
  trimDialog?.addEventListener("cancel", (event) => {
    event.preventDefault();
    trimDialog.close();
  });
  trimDialog?.addEventListener("click", (event) => {
    if (event.target === trimDialog) trimDialog.close();
  });
  formatDialog?.addEventListener("close", () => {
    formattingVideoId = null;
    if (formatError) formatError.textContent = "";
    if (formatConfirm) formatConfirm.disabled = false;
  });
  formatDialog?.addEventListener("cancel", (event) => {
    event.preventDefault();
    formatDialog.close();
  });
  formatDialog?.addEventListener("click", (event) => {
    if (event.target === formatDialog) formatDialog.close();
  });
})();
