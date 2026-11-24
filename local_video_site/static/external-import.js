(() => {
  const root = document.getElementById("external-import-form");
  const form = document.getElementById("external-import-submit-form");
  const source = document.getElementById("import-source");
  const destination = document.getElementById("import-destination");
  const accessLevel = document.getElementById(
    "external-import-access-level",
  );
  const deleteSource = document.getElementById("import-delete-source");
  const password = document.getElementById("external-import-password");
  const passwordFields = document.getElementById(
    "external-import-password-fields",
  );
  const error = document.getElementById("external-import-error");
  const confirmButton = document.getElementById("confirm-external-import");
  const previewSection = document.getElementById("import-preview");
  const previewSummary = document.getElementById("import-preview-summary");
  const previewSamples = document.getElementById("import-preview-samples");
  const dialog = document.getElementById("import-browser-dialog");
  const dialogTitle = document.getElementById("import-browser-title");
  const dialogPath = document.getElementById("import-browser-path");
  const dialogList = document.getElementById("import-browser-list");
  const dialogError = document.getElementById("import-browser-error");
  const selectDirectory = document.getElementById("select-import-directory");
  if (
    !root || !form || !source || !destination
    || !accessLevel || !password
  ) return;

  let browserMode = "source";
  let currentPath = "";
  let currentParent = "";
  let previewTimer = null;
  let previewCanCreate = false;
  let submitting = false;

  function formatBytes(value) {
    const bytes = Number(value) || 0;
    if (bytes < 1024) return `${bytes} B`;
    const units = ["KiB", "MiB", "GiB", "TiB"];
    let amount = bytes;
    let unit = "B";
    for (const candidate of units) {
      amount /= 1024;
      unit = candidate;
      if (amount < 1024) break;
    }
    return `${amount.toFixed(amount >= 10 ? 1 : 2)} ${unit}`;
  }

  function setError(message = "") {
    if (error) error.textContent = message;
  }

  function syncPasswordRequirement() {
    const required = Boolean(deleteSource?.checked);
    if (passwordFields) passwordFields.hidden = !required;
    if (password) {
      password.required = required;
      if (!required) password.value = "";
    }
  }

  function renderBrowser(payload) {
    currentPath = payload.path;
    currentParent = payload.parent;
    dialogPath.textContent = currentPath;
    dialogError.textContent = "";
    dialogList.replaceChildren();
    if (payload.parent) {
      const parent = document.createElement("button");
      parent.type = "button";
      parent.className = "directory-row";
      parent.textContent = ".. 返回上级";
      parent.addEventListener("click", () => browse(currentParent));
      dialogList.appendChild(parent);
    }
    const directories = payload.directories || payload.items || [];
    for (const item of directories) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "directory-row";
      button.textContent = `▸ ${item.name}`;
      button.title = item.path;
      button.addEventListener("click", () => browse(item.path));
      dialogList.appendChild(button);
    }
    for (const item of payload.files || []) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "directory-row import-file-row";
      button.textContent = `▶ ${item.name} · ${formatBytes(item.size)}`;
      button.title = item.path;
      button.addEventListener("click", () => {
        source.value = item.path;
        dialog.close();
        schedulePreview();
      });
      dialogList.appendChild(button);
    }
    selectDirectory.textContent = browserMode === "source"
      ? "选择当前目录"
      : "选择当前目录作为目标";
  }

  async function browse(path) {
    dialogError.textContent = "";
    const url = browserMode === "source"
      ? `/api/manage/import/browse?path=${encodeURIComponent(path)}`
      : `/api/manage/storage/directories?path=${encodeURIComponent(path)}`;
    try {
      const response = await fetch(url, { cache: "no-store" });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = payload.detail || {};
        throw new Error(
          detail.message || detail || `HTTP ${response.status}`,
        );
      }
      renderBrowser(payload);
    } catch (browseError) {
      dialogError.textContent = browseError.message;
    }
  }

  function openBrowser(mode) {
    browserMode = mode;
    dialogTitle.textContent = mode === "source"
      ? "选择外盘文件或目录"
      : "选择本地目标目录";
    const input = mode === "source" ? source : destination;
    let start = input.value.trim();
    if (mode === "source" && /\.[a-z0-9]+$/i.test(start)) {
      const slash = Math.max(start.lastIndexOf("/"), start.lastIndexOf("\\"));
      if (slash > 0) start = start.slice(0, slash);
    }
    dialog.showModal();
    browse(start || (mode === "source" ? "/Volumes" : "/Users"));
  }

  document.getElementById("browse-import-source")?.addEventListener(
    "click",
    () => openBrowser("source"),
  );
  document.getElementById("browse-import-destination")?.addEventListener(
    "click",
    () => openBrowser("destination"),
  );
  document.querySelectorAll("[data-close-import-browser]").forEach((button) => {
    button.addEventListener("click", () => dialog.close());
  });
  selectDirectory?.addEventListener("click", () => {
    if (!currentPath) return;
    if (browserMode === "source") source.value = currentPath;
    else destination.value = currentPath;
    dialog.close();
    schedulePreview();
  });

  function renderPreview(payload) {
    previewSection.hidden = false;
    previewCanCreate = payload.can_create && !payload.insufficient_space;
    const rows = [
      ["视频数量", `${payload.file_count} 个`],
      ["总大小", formatBytes(payload.total_bytes)],
      ["目标剩余空间", formatBytes(payload.free_bytes)],
      ["跳过文件", `${payload.skipped_count} 个`],
      ["重名冲突", `${payload.conflict_count} 个`],
    ];
    previewSummary.replaceChildren();
    for (const [label, value] of rows) {
      const row = document.createElement("div");
      const term = document.createElement("span");
      const description = document.createElement("strong");
      term.textContent = label;
      description.textContent = value;
      row.append(term, description);
      previewSummary.appendChild(row);
    }
    if (payload.insufficient_space) {
      const warning = document.createElement("p");
      warning.className = "import-preview-warning";
      warning.textContent = "目标目录剩余空间不足，无法创建导入任务。";
      previewSummary.appendChild(warning);
    } else if (payload.conflict_count) {
      const warning = document.createElement("p");
      warning.className = "import-preview-warning";
      warning.textContent = "存在重名文件，对应任务会失败，不会覆盖现有文件。";
      previewSummary.appendChild(warning);
    }
    previewSamples.replaceChildren();
    for (const item of payload.samples || []) {
      const row = document.createElement("div");
      row.className = item.conflict
        ? "import-preview-sample is-conflict"
        : "import-preview-sample";
      const sourceText = document.createElement("span");
      sourceText.textContent = item.source_path;
      sourceText.title = item.source_path;
      const state = document.createElement("small");
      state.textContent = item.conflict
        ? item.conflict
        : formatBytes(item.size);
      row.append(sourceText, state);
      previewSamples.appendChild(row);
    }
    confirmButton.disabled = !previewCanCreate || submitting;
  }

  async function updatePreview() {
    const sourcePath = source.value.trim();
    const destinationRoot = destination.value.trim();
    if (!sourcePath || !destinationRoot) {
      previewSection.hidden = true;
      previewCanCreate = false;
      confirmButton.disabled = true;
      return;
    }
    setError("");
    try {
      const response = await fetch(
        "/api/manage/import/preview?"
        + `source_path=${encodeURIComponent(sourcePath)}`
        + `&destination_root=${encodeURIComponent(destinationRoot)}`,
        { cache: "no-store" },
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = payload.detail || {};
        throw new Error(
          detail.message || detail || `HTTP ${response.status}`,
        );
      }
      renderPreview(payload);
    } catch (previewError) {
      previewSection.hidden = true;
      previewCanCreate = false;
      confirmButton.disabled = true;
      setError(previewError.message);
    }
  }

  function schedulePreview() {
    window.clearTimeout(previewTimer);
    previewTimer = window.setTimeout(updatePreview, 250);
  }

  source.addEventListener("change", schedulePreview);
  destination.addEventListener("change", schedulePreview);
  deleteSource?.addEventListener("change", syncPasswordRequirement);
  syncPasswordRequirement();

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submitting) return;
    if (!previewCanCreate) {
      setError("请先选择可导入的视频和本地目标目录。");
      return;
    }
    const deletingSource = Boolean(deleteSource?.checked);
    if (deletingSource && !password.value) {
      setError("请输入删除校验码。");
      return;
    }
    submitting = true;
    confirmButton.disabled = true;
    confirmButton.textContent = "正在创建任务…";
    setError("");
    try {
      const response = await fetch("/api/manage/import/batches", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source_path: source.value.trim(),
          destination_root: destination.value.trim(),
          access_level: Number(accessLevel.value),
          delete_source: deletingSource,
          verification_mode: "sha256",
          password: deletingSource ? password.value : "",
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = payload.detail || {};
        throw new Error(
          detail.message || detail || `HTTP ${response.status}`,
        );
      }
      window.location.href = "/manage/migrations";
    } catch (submitError) {
      submitting = false;
      confirmButton.disabled = false;
      confirmButton.textContent = "确认创建导入任务";
      setError(submitError.message);
    }
  });

  schedulePreview();
})();
