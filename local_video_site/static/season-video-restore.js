(() => {
  const dialog = document.getElementById("video-restore-dialog");
  const form = document.getElementById("video-restore-form");
  if (!dialog || !form) return;

  const episodeNode = document.getElementById("video-restore-episode");
  const titleNode = document.getElementById("video-restore-title");
  const originalPathNode = document.getElementById(
    "video-restore-original-path",
  );
  const scope = document.getElementById("video-restore-scope");
  const pathInput = document.getElementById("video-restore-path");
  const browseButton = document.getElementById("video-restore-browse");
  const originalButton = document.getElementById("video-restore-original");
  const browser = document.getElementById("video-restore-browser");
  const browserPath = document.getElementById(
    "video-restore-browser-path",
  );
  const browserList = document.getElementById("video-restore-browser-list");
  const preview = document.getElementById("video-restore-preview");
  const error = document.getElementById("video-restore-error");
  const submit = document.getElementById("video-restore-submit");
  let selected = null;
  let previewed = null;
  let browsePath = "";

  function message(payload, fallback) {
    const detail = payload?.detail || payload;
    return detail?.message || detail || fallback;
  }

  function setError(value = "") {
    error.textContent = value;
  }

  function clearPreview() {
    previewed = null;
    preview.hidden = true;
    preview.replaceChildren();
  }

  function formatBytes(value) {
    let amount = Number(value) || 0;
    const units = ["B", "KiB", "MiB", "GiB", "TiB"];
    let unit = "B";
    for (const candidate of units) {
      unit = candidate;
      if (amount < 1024 || candidate === "TiB") break;
      amount /= 1024;
    }
    return `${amount.toFixed(amount >= 10 ? 1 : 2)} ${unit}`;
  }

  function renderPreview(payload) {
    previewed = payload;
    preview.replaceChildren();
    for (const [label, value] of [
      ["路径", payload.source_path],
      ["大小", formatBytes(payload.size)],
      ["格式", (payload.extension || "").toUpperCase()],
      ["清晰度", payload.width && payload.height
        ? `${payload.width}×${payload.height}`
        : "未知"],
      ["编码", `${payload.video_codec || "未知"} / ${payload.audio_codec || "未知"}`],
      ["存储", payload.storage_kind === "external"
        ? `外置盘 ${payload.storage_volume_label || ""}`
        : "本地"],
    ]) {
      const row = document.createElement("div");
      const term = document.createElement("span");
      const description = document.createElement("strong");
      term.textContent = label;
      description.textContent = value;
      row.append(term, description);
      preview.appendChild(row);
    }
    preview.hidden = false;
  }

  async function loadPreview() {
    if (!selected || !pathInput.value.trim()) return;
    clearPreview();
    setError("");
    submit.disabled = true;
    try {
      const response = await fetch(
        `/api/manage/videos/${selected.videoId}/restore/preview`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            episode_id: selected.episodeId,
            source_path: pathInput.value.trim(),
            storage_scope: scope.value,
          }),
        },
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(message(payload, `HTTP ${response.status}`));
      renderPreview(payload);
      submit.disabled = false;
    } catch (requestError) {
      setError(requestError.message || String(requestError));
    }
  }

  function renderBrowser(payload) {
    browsePath = payload.path || "";
    browserPath.textContent = browsePath;
    browserList.replaceChildren();
    if (payload.parent) {
      const parent = document.createElement("button");
      parent.type = "button";
      parent.className = "directory-row";
      parent.textContent = ".. 返回上级";
      parent.addEventListener("click", () => browse(payload.parent));
      browserList.appendChild(parent);
    }
    for (const item of payload.directories || []) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "directory-row";
      button.textContent = `▸ ${item.name}`;
      button.dataset.path = item.path;
      button.addEventListener("click", () => browse(item.path));
      browserList.appendChild(button);
    }
    for (const item of payload.files || []) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "directory-row import-file-row";
      button.textContent = `▶ ${item.name} · ${formatBytes(item.size)}`;
      button.addEventListener("click", () => {
        pathInput.value = item.path;
        browser.hidden = true;
        loadPreview();
      });
      browserList.appendChild(button);
    }
  }

  async function browse(path = "") {
    browser.hidden = false;
    browserList.textContent = "正在读取…";
    try {
      const response = await fetch(
        `/api/manage/videos/restore/browse?scope=${encodeURIComponent(scope.value)}`
        + `&path=${encodeURIComponent(path)}`,
        { cache: "no-store" },
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(message(payload, `HTTP ${response.status}`));
      renderBrowser(payload);
    } catch (requestError) {
      browserList.textContent = "";
      setError(requestError.message || String(requestError));
    }
  }

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-restore-video]");
    if (trigger) {
      selected = {
        videoId: Number(trigger.dataset.restoreVideo),
        episodeId: Number(trigger.dataset.restoreEpisode),
        title: trigger.dataset.restoreTitle || "",
        episodeLabel: trigger.dataset.restoreEpisodeLabel || "",
        originalPath: trigger.dataset.restoreOriginalPath || "",
        updatedAt: trigger.dataset.restoreUpdatedAt || "",
      };
      episodeNode.textContent = selected.episodeLabel;
      titleNode.textContent = `#${selected.videoId} ${selected.title}`;
      originalPathNode.textContent = selected.originalPath || "—";
      pathInput.value = "";
      originalButton.disabled = !selected.originalPath;
      browser.hidden = true;
      clearPreview();
      setError("");
      submit.disabled = true;
      dialog.showModal();
      scope.focus();
      return;
    }
    if (event.target.closest("[data-cancel-video-restore]")) {
      dialog.close();
    }
  });

  scope.addEventListener("change", () => {
    browsePath = "";
    browser.hidden = true;
    clearPreview();
  });
  pathInput.addEventListener("input", () => {
    clearPreview();
    submit.disabled = true;
    setError("");
  });
  browseButton.addEventListener("click", () => browse(browsePath));
  originalButton.addEventListener("click", () => {
    if (!selected?.originalPath) return;
    pathInput.value = selected.originalPath;
    loadPreview();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!selected || !previewed) {
      setError("请先选择并校验视频文件。");
      return;
    }
    if (!previewed.source_fingerprint) {
      setError("预览校验信息不完整，请重新校验视频文件。");
      return;
    }
    submit.disabled = true;
    submit.textContent = "正在上线…";
    setError("");
    try {
      const response = await fetch(
        `/api/manage/videos/${selected.videoId}/restore-online`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            episode_id: selected.episodeId,
            source_path: pathInput.value.trim(),
            storage_scope: scope.value,
            source_fingerprint: previewed.source_fingerprint,
            expected_updated_at: selected.updatedAt,
          }),
        },
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(message(payload, `HTTP ${response.status}`));
      window.location.reload();
    } catch (requestError) {
      submit.disabled = false;
      submit.textContent = "确认重新上线";
      setError(requestError.message || String(requestError));
    }
  });
})();
