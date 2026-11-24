(() => {
  const root = document.getElementById("migration-form");
  const submitForm = document.getElementById("migration-submit-form");
  const target = document.getElementById("migration-target");
  const password = document.getElementById("migration-password");
  const submit = document.getElementById("submit-migration");
  const error = document.getElementById("migration-form-error");
  const selectedCount = document.getElementById("selected-count");
  const clearSelected = document.getElementById("clear-selected-videos");
  const selectPage = document.getElementById("select-page-videos");
  const picker = document.getElementById("directory-picker-dialog");
  const pickerPath = document.getElementById("directory-current-path");
  const pickerList = document.getElementById("directory-list");
  const pickerError = document.getElementById("directory-picker-error");
  const recentTargets = document.getElementById("migration-recent-targets");
  const recentTargetSelect = document.getElementById("recent-target-select");
  const recentTargetChips = document.getElementById("recent-target-chips");
  const clearRecentTargets = document.getElementById("clear-recent-targets");
  if (!root || !submitForm || !target || !password || !submit) return;

  const mode = root.dataset.mode;
  const direction = root.dataset.direction;
  const storageKey = `video-migration-selection:${direction}`;
  const targetStorageKey = `video-migration-target:${direction}`;
  const targetHistoryKey = "video-migration-target-history:v1";
  const maxRecentTargets = 8;
  let selected = new Set();
  let targetHistory = { version: 1, directions: {} };
  let currentDirectory = "";
  let currentParent = "";
  let submitting = false;

  function loadSelection() {
    try {
      const values = JSON.parse(localStorage.getItem(storageKey) || "[]");
      selected = new Set(values.map(Number).filter(Number.isFinite));
    } catch (_) {
      selected = new Set();
    }
    document.querySelectorAll("[data-select-video]").forEach((checkbox) => {
      const videoId = Number(checkbox.dataset.selectVideo);
      if (checkbox.checked) selected.add(videoId);
    });
    persistSelection();
    updateSelectionCount();
  }

  function persistSelection() {
    localStorage.setItem(storageKey, JSON.stringify([...selected].sort((a, b) => a - b)));
  }

  function normalizeTargetPath(value) {
    const trimmed = String(value || "").trim();
    if (!trimmed) return "";
    return trimmed.replace(/\/+$/, "") || "/";
  }

  function targetLabel(path) {
    const parts = path.split("/").filter(Boolean);
    if (parts.length >= 2) return `${parts.at(-2)} / ${parts.at(-1)}`;
    return parts.at(-1) || path;
  }

  function readTargetHistory() {
    try {
      const parsed = JSON.parse(localStorage.getItem(targetHistoryKey) || "{}");
      if (parsed && parsed.version === 1 && parsed.directions) {
        targetHistory = parsed;
      }
    } catch (_) {
      targetHistory = { version: 1, directions: {} };
    }
    if (!Array.isArray(targetHistory.directions[direction])) {
      targetHistory.directions[direction] = [];
    }
    const legacyTarget = normalizeTargetPath(
      localStorage.getItem(targetStorageKey) || "",
    );
    if (legacyTarget && !targetHistory.directions[direction].some(
      (item) => normalizeTargetPath(item.path) === legacyTarget,
    )) {
      targetHistory.directions[direction].unshift({
        path: legacyTarget,
        label: targetLabel(legacyTarget),
        lastUsedAt: new Date().toISOString(),
      });
    }
    localStorage.removeItem(targetStorageKey);
    writeTargetHistory();
  }

  function writeTargetHistory() {
    try {
      localStorage.setItem(targetHistoryKey, JSON.stringify(targetHistory));
    } catch (_) {
      // Local history is optional; the directory picker remains usable.
    }
  }

  function recentTargetItems() {
    return targetHistory.directions[direction] || [];
  }

  function rememberTarget(value) {
    const path = normalizeTargetPath(value);
    if (!path) return;
    const items = recentTargetItems().filter(
      (item) => normalizeTargetPath(item.path) !== path,
    );
    items.unshift({
      path,
      label: targetLabel(path),
      lastUsedAt: new Date().toISOString(),
    });
    targetHistory.directions[direction] = items.slice(0, maxRecentTargets);
    writeTargetHistory();
    renderRecentTargets();
  }

  function useRecentTarget(path) {
    const normalized = normalizeTargetPath(path);
    if (!normalized) return;
    target.value = normalized;
    rememberTarget(normalized);
    if (recentTargetSelect) recentTargetSelect.value = "";
  }

  function renderRecentTargets() {
    const items = recentTargetItems();
    if (!recentTargets || !recentTargetSelect || !recentTargetChips) return;
    recentTargets.hidden = items.length === 0;
    recentTargetSelect.replaceChildren();
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = items.length
      ? "从最近目录中选择…"
      : "暂无最近目录";
    recentTargetSelect.appendChild(placeholder);
    recentTargetChips.replaceChildren();
    for (const item of items) {
      const option = document.createElement("option");
      option.value = item.path;
      option.textContent = item.label || item.path;
      option.title = item.path;
      recentTargetSelect.appendChild(option);

      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "migration-recent-chip";
      chip.textContent = item.label || item.path;
      chip.title = item.path;
      chip.addEventListener("click", () => useRecentTarget(item.path));
      recentTargetChips.appendChild(chip);
    }
  }

  function loadTarget() {
    readTargetHistory();
    const items = recentTargetItems();
    const latest = items.length ? normalizeTargetPath(items[0].path) : "";
    if (latest) target.value = latest;
    renderRecentTargets();
  }

  function persistTarget() {
    const value = normalizeTargetPath(target.value);
    if (value) rememberTarget(value);
    localStorage.removeItem(targetStorageKey);
  }

  function updateSelectionCount() {
    if (selectedCount) {
      selectedCount.textContent = `已选择 ${selected.size} 个`;
    }
    document.querySelectorAll("[data-select-video]").forEach((checkbox) => {
      checkbox.checked = selected.has(Number(checkbox.dataset.selectVideo));
    });
    updateSelectPageButton();
  }

  function pageCheckboxes() {
    return Array.from(document.querySelectorAll("[data-select-video]"));
  }

  function updateSelectPageButton() {
    if (!selectPage) return;
    const checkboxes = pageCheckboxes();
    const selectedOnPage = checkboxes.filter(
      (checkbox) => selected.has(Number(checkbox.dataset.selectVideo)),
    ).length;
    const allSelected = (
      checkboxes.length > 0
      && selectedOnPage === checkboxes.length
    );
    selectPage.disabled = checkboxes.length === 0;
    selectPage.textContent = allSelected ? "取消全选" : "全选";
    selectPage.setAttribute("aria-pressed", allSelected ? "true" : "false");
  }

  document.addEventListener("change", (event) => {
    const checkbox = event.target.closest("[data-select-video]");
    if (!checkbox) return;
    const videoId = Number(checkbox.dataset.selectVideo);
    if (checkbox.checked) selected.add(videoId);
    else selected.delete(videoId);
    persistSelection();
    updateSelectionCount();
  });

  clearSelected?.addEventListener("click", () => {
    selected.clear();
    persistSelection();
    updateSelectionCount();
  });

  selectPage?.addEventListener("click", () => {
    const checkboxes = pageCheckboxes();
    const allSelected = (
      checkboxes.length > 0
      && checkboxes.every(
        (checkbox) => selected.has(Number(checkbox.dataset.selectVideo)),
      )
    );
    for (const checkbox of checkboxes) {
      const videoId = Number(checkbox.dataset.selectVideo);
      if (allSelected) selected.delete(videoId);
      else selected.add(videoId);
    }
    persistSelection();
    updateSelectionCount();
  });

  function renderPicker(payload) {
    currentDirectory = payload.path;
    currentParent = payload.parent;
    pickerPath.textContent = payload.path;
    pickerError.textContent = "";
    pickerList.replaceChildren();
    if (payload.parent) {
      const parentButton = document.createElement("button");
      parentButton.type = "button";
      parentButton.className = "directory-row";
      parentButton.textContent = ".. 返回上级";
      parentButton.addEventListener("click", () => browse(payload.parent));
      pickerList.appendChild(parentButton);
    }
    for (const item of payload.items) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "directory-row";
      button.textContent = item.name;
      button.title = item.path;
      button.addEventListener("click", () => browse(item.path));
      pickerList.appendChild(button);
    }
    if (!payload.items.length) {
      const empty = document.createElement("p");
      empty.className = "management-empty-note";
      empty.textContent = "当前目录没有子目录，可以直接选择当前目录。";
      pickerList.appendChild(empty);
    }
  }

  async function browse(path) {
    pickerError.textContent = "";
    try {
      const response = await fetch(
        `/api/manage/storage/directories?path=${encodeURIComponent(path)}`,
      );
      const payload = await response.json();
      if (!response.ok) {
        const detail = payload.detail || {};
        throw new Error(detail.message || detail || `HTTP ${response.status}`);
      }
      renderPicker(payload);
    } catch (browseError) {
      pickerError.textContent = browseError.message;
    }
  }

  document.getElementById("browse-migration-target")?.addEventListener("click", () => {
    picker.showModal();
    browse(target.value || (direction === "to_local" ? "/Users" : "/Volumes"));
  });
  document.querySelectorAll("[data-close-directory-picker]").forEach((button) => {
    button.addEventListener("click", () => picker.close());
  });
  document.getElementById("confirm-directory")?.addEventListener("click", () => {
    if (!currentDirectory) return;
    target.value = currentDirectory;
    persistTarget();
    picker.close();
  });

  target.addEventListener("change", persistTarget);
  target.addEventListener("input", () => {
    if (!target.value.trim()) localStorage.removeItem(targetStorageKey);
  });
  recentTargetSelect?.addEventListener("change", () => {
    useRecentTarget(recentTargetSelect.value);
  });
  clearRecentTargets?.addEventListener("click", () => {
    if (!recentTargetItems().length) return;
    if (!window.confirm("清空当前迁移方向的最近目录记录？")) return;
    targetHistory.directions[direction] = [];
    writeTargetHistory();
    renderRecentTargets();
  });

  submitForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submitting) return;
    const destination = target.value.trim();
    if (!destination) {
      error.textContent = "请选择目标文件夹";
      return;
    }
    if (mode === "manual" && !selected.size) {
      error.textContent = "请至少选择一个视频";
      return;
    }
    if (!password.value) {
      error.textContent = "请输入删除校验码";
      return;
    }
    submitting = true;
    submit.disabled = true;
    submit.textContent = "正在创建任务…";
    error.textContent = "";
    try {
      const response = await fetch("/api/manage/migrations/batches", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mode,
          direction,
          destination_root: destination,
          video_ids: mode === "manual" ? [...selected] : [],
          cutoff_days: Number(root.dataset.cutoffDays || 30),
          verification_mode: "sha256",
          password: password.value,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = payload.detail;
        throw new Error(
          typeof detail === "string"
            ? detail
            : detail?.message || `HTTP ${response.status}`,
        );
      }
      localStorage.removeItem(storageKey);
      localStorage.removeItem(targetStorageKey);
      window.location.href = "/manage/migrations";
    } catch (submitError) {
      submitting = false;
      submit.disabled = false;
      submit.textContent = mode === "auto" ? "创建自动迁移任务" : "保存迁移任务";
      error.textContent = submitError.message;
    }
  });

  loadTarget();
  loadSelection();
})();
