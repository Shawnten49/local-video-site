(() => {
  const form = document.getElementById("external-scan-form");
  const source = document.getElementById("external-scan-source");
  const mode = document.getElementById("external-scan-mode");
  const accessLevel = document.getElementById("external-scan-access-level");
  const filePanel = document.getElementById("external-scan-file-panel");
  const batchPanel = document.getElementById("external-scan-batch-panel");
  const collectionFields = document.getElementById(
    "external-scan-collection-fields",
  );
  const seriesName = document.getElementById("external-scan-series-name");
  const seriesLabel = document.getElementById("external-scan-series-label");
  const seriesHint = document.getElementById("external-scan-series-hint");
  const attachSeries = document.getElementById(
    "external-scan-attach-series",
  );
  const targetControls = document.getElementById(
    "external-scan-target-controls",
  );
  const targetSeriesId = document.getElementById(
    "external-scan-target-series-id",
  );
  const loadTarget = document.getElementById("external-scan-load-target");
  const targetPreview = document.getElementById(
    "external-scan-target-preview",
  );
  const targetError = document.getElementById("external-scan-target-error");
  const seasonAssignment = document.getElementById(
    "external-scan-season-assignment",
  );
  const attachSeason = document.getElementById(
    "external-scan-attach-season",
  );
  const targetSeasonControls = document.getElementById(
    "external-scan-target-season-controls",
  );
  const targetSeasonId = document.getElementById(
    "external-scan-target-season-id",
  );
  const loadTargetSeason = document.getElementById(
    "external-scan-load-target-season",
  );
  const targetSeasonPreview = document.getElementById(
    "external-scan-target-season-preview",
  );
  const targetSeasonError = document.getElementById(
    "external-scan-target-season-error",
  );
  const groupsRoot = document.getElementById("external-scan-groups");
  const groupSummary = document.getElementById(
    "external-scan-group-summary",
  );
  const groupError = document.getElementById("external-scan-group-error");
  const error = document.getElementById("external-scan-error");
  const submit = document.getElementById("start-external-scan");
  const previewSection = document.getElementById(
    "external-scan-source-preview",
  );
  const previewSummary = document.getElementById(
    "external-scan-source-summary",
  );
  const previewSamples = document.getElementById(
    "external-scan-source-samples",
  );
  const dialog = document.getElementById("external-scan-browser-dialog");
  const dialogPath = document.getElementById("external-scan-browser-path");
  const dialogList = document.getElementById("external-scan-browser-list");
  const dialogError = document.getElementById("external-scan-browser-error");
  const selectDirectory = document.getElementById(
    "select-external-scan-directory",
  );
  if (!form || !source || !mode || !accessLevel || !submit) return;

  let currentPath = "";
  let currentParent = "";
  let previewTimer = null;
  let previewReady = false;
  let lastDiscovery = null;
  let groupSequence = 0;
  let targetAssignment = null;
  let targetLookupSequence = 0;
  let targetSeasonAssignment = null;
  let targetSeasonLookupSequence = 0;

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

  function detailMessage(payload, fallback) {
    const detail = payload?.detail || payload;
    return detail?.message || detail?.detail || detail || fallback;
  }

  function setError(message = "") {
    error.textContent = message;
  }

  function setGroupError(message = "") {
    if (groupError) groupError.textContent = message;
  }

  function setTargetError(message = "") {
    if (targetError) targetError.textContent = message;
  }

  function setTargetSeasonError(message = "") {
    if (targetSeasonError) targetSeasonError.textContent = message;
  }

  function syncModePanels() {
    const selectedMode = mode.value;
    if (filePanel) filePanel.hidden = selectedMode !== "file";
    if (batchPanel) batchPanel.hidden = selectedMode !== "batch";
    if (collectionFields) {
      collectionFields.hidden = selectedMode !== "collection";
    }
    if (previewSamples) {
      previewSamples.hidden = selectedMode === "collection";
    }
    const joining = Boolean(attachSeries?.checked);
    if (targetControls) {
      targetControls.hidden = (
        selectedMode !== "collection" || !joining
      );
    }
    if (targetPreview && !joining) targetPreview.hidden = true;
    const canJoinSeason = ["batch", "file"].includes(selectedMode);
    if (seasonAssignment) seasonAssignment.hidden = !canJoinSeason;
    if (targetSeasonControls) {
      targetSeasonControls.hidden = (
        !canJoinSeason || !attachSeason?.checked
      );
    }
    if (targetSeasonPreview && !attachSeason?.checked) {
      targetSeasonPreview.hidden = true;
    }
    if (seriesLabel) {
      seriesLabel.textContent = joining
        ? "扫描来源视频集名称"
        : "视频集名称";
    }
    if (seriesHint) {
      seriesHint.textContent = joining
        ? "来源名称只用于本次扫描识别；入库时将使用目标视频集，已有视频集数据不会被改写。"
        : "修改视频集名称会同步到下方所有季。季名称、季编号和集数均可人工修正。";
    }
  }

  function suggestSeriesName() {
    if (!seriesName || seriesName.value.trim()) return;
    const value = source.value.trim().replace(/\/+$/, "");
    if (!value) return;
    const last = value.split("/").pop();
    if (last) seriesName.value = last;
  }

  function updateSeriesLabels() {
    const value = seriesName?.value.trim() || "未填写";
    document.querySelectorAll(".external-scan-group-series-value").forEach(
      (node) => {
        node.textContent = value;
      },
    );
  }

  function groupCards() {
    return Array.from(
      groupsRoot?.querySelectorAll("[data-season-group]") || [],
    );
  }

  function rowElements(group) {
    return Array.from(group.querySelectorAll("[data-scan-file-row]"));
  }

  function currentSeasonNumbers() {
    return groupCards()
      .map((card) => Number(
        card.querySelector(".external-scan-group-number")?.value,
      ))
      .filter((value) => Number.isInteger(value) && value > 0);
  }

  function targetConflictNumbers() {
    if (!targetAssignment) return new Set();
    const existing = new Set(
      (targetAssignment.seasons || []).map(
        (season) => Number(season.season_number),
      ),
    );
    return new Set(
      currentSeasonNumbers().filter((number) => existing.has(number)),
    );
  }

  function renderTargetPreview() {
    if (!targetPreview) return;
    if (!attachSeries?.checked || !targetAssignment) {
      targetPreview.hidden = true;
      targetPreview.replaceChildren();
      targetPreview.classList.remove("is-conflict");
      return;
    }
    const conflicts = targetConflictNumbers();
    targetPreview.hidden = false;
    targetPreview.classList.toggle("is-conflict", conflicts.size > 0);
    targetPreview.replaceChildren();

    const heading = document.createElement("strong");
    heading.textContent =
      `#${targetAssignment.id} ${targetAssignment.display_name}`;
    const summary = document.createElement("span");
    summary.textContent = targetAssignment.seasons?.length
      ? `目标视频集现有 ${targetAssignment.seasons.length} 季。`
      : "目标视频集目前没有季，可直接加入。";
    targetPreview.append(heading, summary);

    if (targetAssignment.seasons?.length) {
      const list = document.createElement("div");
      list.className = "external-scan-target-season-list";
      for (const season of targetAssignment.seasons) {
        const number = Number(season.season_number);
        const badge = document.createElement("span");
        badge.className = "external-scan-target-season";
        if (conflicts.has(number)) badge.classList.add("is-conflict");
        badge.textContent =
          `${season.display_name || season.name}`
          + ` · 第${number}季`
          + ` · ${Number(season.episode_count || 0)} 集`;
        list.appendChild(badge);
      }
      targetPreview.appendChild(list);
    }

    if (conflicts.size) {
      const conflict = document.createElement("span");
      conflict.textContent =
        `季编号冲突：${Array.from(conflicts)
          .sort((left, right) => left - right)
          .map((number) => `第${number}季`)
          .join("、")}。请修改本次扫描的季编号。`;
      targetPreview.appendChild(conflict);
    }
  }

  function validateTargetAssignment(showError = true) {
    if (
      mode.value !== "collection"
      || !attachSeries?.checked
    ) {
      if (showError) setTargetError("");
      return true;
    }
    const id = Number(targetSeriesId?.value);
    if (!Number.isInteger(id) || id <= 0) {
      if (showError) {
        setTargetError("请填写有效的目标视频集 ID。");
      }
      return false;
    }
    if (!targetAssignment || Number(targetAssignment.id) !== id) {
      if (showError) {
        setTargetError("请先读取目标视频集信息。");
      }
      return false;
    }
    const conflicts = targetConflictNumbers();
    if (conflicts.size) {
      if (showError) {
        setTargetError(
          `目标视频集已有第 ${Array.from(conflicts)
            .sort((left, right) => left - right)
            .join("、")} 季，请先修改本次季编号。`,
        );
      }
      return false;
    }
    if (showError) setTargetError("");
    return true;
  }

  async function lookupTargetSeries() {
    if (!attachSeries?.checked) {
      targetAssignment = null;
      setTargetError("");
      renderTargetPreview();
      updateGroupSummary();
      return;
    }
    const id = Number(targetSeriesId?.value);
    if (!Number.isInteger(id) || id <= 0) {
      targetAssignment = null;
      renderTargetPreview();
      setTargetError("请填写有效的目标视频集 ID。");
      updateGroupSummary();
      return;
    }
    const sequence = ++targetLookupSequence;
    targetAssignment = null;
    setTargetError("正在读取目标视频集…");
    renderTargetPreview();
    try {
      const response = await fetch(
        `/api/manage/series/${encodeURIComponent(id)}/assignment-preview`,
        { cache: "no-store" },
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(detailMessage(payload, `HTTP ${response.status}`));
      }
      if (sequence !== targetLookupSequence) return;
      targetAssignment = payload;
      setTargetError("");
      renderTargetPreview();
      validateTargetAssignment(true);
    } catch (lookupError) {
      if (sequence !== targetLookupSequence) return;
      targetAssignment = null;
      setTargetError(lookupError.message);
      renderTargetPreview();
    } finally {
      if (sequence === targetLookupSequence) updateGroupSummary();
    }
  }

  function renderTargetSeasonPreview() {
    if (!targetSeasonPreview) return;
    if (!attachSeason?.checked || !targetSeasonAssignment) {
      targetSeasonPreview.hidden = true;
      targetSeasonPreview.replaceChildren();
      return;
    }
    const season = targetSeasonAssignment.season || {};
    const series = targetSeasonAssignment.series || {};
    targetSeasonPreview.hidden = false;
    targetSeasonPreview.replaceChildren();

    const heading = document.createElement("strong");
    heading.textContent =
      `${series.display_name || series.name || "未知视频集"} · `
      + `${season.display_name || season.name || "未知季"}`;
    const summary = document.createElement("span");
    summary.textContent =
      `视频集 #${series.id} · 第${season.season_number}季 · `
      + `现有 ${Number(targetSeasonAssignment.episode_count || 0)} 集`;
    targetSeasonPreview.append(heading, summary);

    const used = targetSeasonAssignment.used_episode_numbers || [];
    if (used.length) {
      const list = document.createElement("div");
      list.className = "external-scan-target-season-list";
      for (const number of used.slice(0, 40)) {
        const badge = document.createElement("span");
        badge.className = "external-scan-target-season";
        badge.textContent = `第${number}集`;
        list.appendChild(badge);
      }
      if (used.length > 40) {
        const more = document.createElement("span");
        more.className = "external-scan-target-season";
        more.textContent = `另有 ${used.length - 40} 集`;
        list.appendChild(more);
      }
      targetSeasonPreview.appendChild(list);
    }
  }

  function validateTargetSeason(showError = true) {
    if (
      !["batch", "file"].includes(mode.value)
      || !attachSeason?.checked
    ) {
      if (showError) setTargetSeasonError("");
      return true;
    }
    const id = Number(targetSeasonId?.value);
    if (!Number.isInteger(id) || id <= 0) {
      if (showError) {
        setTargetSeasonError("请填写有效的目标视频季 ID。");
      }
      return false;
    }
    if (
      !targetSeasonAssignment
      || Number(targetSeasonAssignment.season?.id) !== id
    ) {
      if (showError) {
        setTargetSeasonError("请先读取目标视频季信息。");
      }
      return false;
    }
    if (showError) setTargetSeasonError("");
    return true;
  }

  async function lookupTargetSeason() {
    if (!attachSeason?.checked) {
      targetSeasonAssignment = null;
      setTargetSeasonError("");
      renderTargetSeasonPreview();
      updateSubmitState();
      return;
    }
    const id = Number(targetSeasonId?.value);
    if (!Number.isInteger(id) || id <= 0) {
      targetSeasonAssignment = null;
      renderTargetSeasonPreview();
      setTargetSeasonError("请填写有效的目标视频季 ID。");
      updateSubmitState();
      return;
    }
    const sequence = ++targetSeasonLookupSequence;
    targetSeasonAssignment = null;
    setTargetSeasonError("正在读取目标视频季…");
    renderTargetSeasonPreview();
    updateSubmitState();
    try {
      const response = await fetch(
        `/api/manage/seasons/${encodeURIComponent(id)}/assignment-preview`,
        { cache: "no-store" },
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(detailMessage(payload, `HTTP ${response.status}`));
      }
      if (sequence !== targetSeasonLookupSequence) return;
      targetSeasonAssignment = payload;
      setTargetSeasonError("");
      renderTargetSeasonPreview();
      validateTargetSeason(true);
    } catch (lookupError) {
      if (sequence !== targetSeasonLookupSequence) return;
      targetSeasonAssignment = null;
      setTargetSeasonError(lookupError.message);
      renderTargetSeasonPreview();
    } finally {
      if (sequence === targetSeasonLookupSequence) updateSubmitState();
    }
  }

  function updateSubmitState() {
    let valid = previewReady;
    if (mode.value === "collection") {
      valid = valid && validateGroups(false);
    } else {
      valid = valid && validateTargetSeason(false);
    }
    submit.disabled = !valid;
  }

  function refreshMoveOptions() {
    const cards = groupCards();
    const choices = cards.map((card, index) => ({
      id: card.dataset.groupId,
      label: `第 ${index + 1} 组 · ${
        card.querySelector(".external-scan-group-name")?.value.trim()
        || "未命名季"
      }`,
    }));
    document.querySelectorAll(".external-scan-move-group").forEach(
      (select) => {
        const row = select.closest("[data-scan-file-row]");
        const currentGroup = row?.closest("[data-season-group]");
        select.replaceChildren();
        for (const choice of choices) {
          const option = document.createElement("option");
          option.value = choice.id;
          option.textContent = choice.label;
          option.selected = currentGroup?.dataset.groupId === choice.id;
          select.appendChild(option);
        }
      },
    );
    updateSeriesLabels();
    renderTargetPreview();
    updateGroupSummary();
  }

  function validateGroups(showError = true) {
    if (mode.value !== "collection") return true;
    const cards = groupCards();
    if (!cards.length) {
      if (showError) setGroupError("至少需要一个季分组。");
      return false;
    }
    if (!seriesName?.value.trim()) {
      if (showError) setGroupError("视频集名称不能为空。");
      return false;
    }
    const seasonNumbers = new Set();
    let totalItems = 0;
    for (const [index, card] of cards.entries()) {
      const name = card.querySelector(
        ".external-scan-group-name",
      )?.value.trim();
      const number = Number(
        card.querySelector(".external-scan-group-number")?.value,
      );
      if (!name) {
        if (showError) setGroupError(`第 ${index + 1} 组季名称不能为空。`);
        return false;
      }
      if (!Number.isInteger(number) || number <= 0) {
        if (showError) setGroupError(`第 ${index + 1} 组季编号无效。`);
        return false;
      }
      if (seasonNumbers.has(number)) {
        if (showError) setGroupError(`季编号 ${number} 重复。`);
        return false;
      }
      seasonNumbers.add(number);
      const rows = rowElements(card);
      if (!rows.length) {
        if (showError) setGroupError(`季“${name}”没有视频。`);
        return false;
      }
      const episodes = new Set();
      for (const row of rows) {
        const episode = Number(
          row.querySelector(".external-scan-episode-input")?.value,
        );
        if (!Number.isInteger(episode) || episode <= 0) {
          if (showError) {
            setGroupError(`“${name}”中存在无效集数。`);
          }
          return false;
        }
        if (episodes.has(episode)) {
          if (showError) {
            setGroupError(`季“${name}”存在重复集数：第 ${episode} 集。`);
          }
          return false;
        }
        episodes.add(episode);
      }
      totalItems += rows.length;
    }
    if (
      lastDiscovery
      && totalItems !== Number(lastDiscovery.file_count || 0)
    ) {
      if (showError) {
        setGroupError(
          `已分配 ${totalItems} 个视频，来源共有 ${lastDiscovery.file_count} 个。`,
        );
      }
      return false;
    }
    if (!validateTargetAssignment(showError)) return false;
    if (showError) setGroupError("");
    return true;
  }

  function updateGroupSummary() {
    if (!groupSummary) return;
    if (mode.value !== "collection") {
      groupSummary.textContent = "";
      updateSubmitState();
      return;
    }
    const cards = groupCards();
    const total = cards.reduce(
      (sum, card) => sum + rowElements(card).length,
      0,
    );
    groupSummary.textContent =
      `共 ${cards.length} 个季分组 · ${total} 个视频`;
    updateSubmitState();
  }

  function createRow(item, group) {
    const row = document.createElement("div");
    row.className = "external-scan-group-row";
    row.dataset.scanFileRow = "1";
    row.dataset.sourcePath = item.source_path;

    const fileName = document.createElement("span");
    fileName.className = "external-scan-group-file";
    fileName.textContent = item.file_name;
    fileName.title = item.source_path;

    const size = document.createElement("small");
    size.textContent = formatBytes(item.size);

    const episodeLabel = document.createElement("label");
    episodeLabel.textContent = "集数";
    const episode = document.createElement("input");
    episode.type = "number";
    episode.min = "1";
    episode.className = "external-scan-episode-input";
    episode.value = item.episode_number || "";
    episodeLabel.appendChild(episode);

    const moveLabel = document.createElement("label");
    moveLabel.textContent = "归属季";
    const move = document.createElement("select");
    move.className = "external-scan-move-group";
    moveLabel.appendChild(move);
    move.addEventListener("change", () => {
      const target = groupsRoot.querySelector(
        `[data-group-id="${CSS.escape(move.value)}"]`,
      );
      if (!target) return;
      target.querySelector(".external-scan-group-items").appendChild(row);
      refreshMoveOptions();
    });
    episode.addEventListener("input", updateGroupSummary);

    const warning = document.createElement("small");
    warning.className = "external-scan-row-warning";
    const notes = [];
    if (item.detection_mode) {
      const season = item.season_number
        ? `第${item.season_number}季`
        : "季未识别";
      const episode = item.episode_number
        ? `第${item.episode_number}集`
        : "集未识别";
      notes.push(
        `识别：${item.matched_text || item.detection_mode} → ${season} ${episode}`,
      );
    } else {
      notes.push("未自动识别季集");
    }
    notes.push(...(item.warnings || []));
    warning.textContent = notes.join("；");

    row.append(fileName, size, episodeLabel, moveLabel, warning);
    return row;
  }

  function createGroupCard(group) {
    const card = document.createElement("article");
    card.className = "external-scan-season-card";
    card.dataset.seasonGroup = "1";
    card.dataset.groupId = group.group_key || `manual-${++groupSequence}`;
    card.dataset.numberEditable = group.number_editable === false ? "0" : "1";

    const header = document.createElement("header");
    const titleBox = document.createElement("div");
    const eyebrow = document.createElement("p");
    eyebrow.className = "eyebrow";
    eyebrow.textContent = "SEASON GROUP";
    const title = document.createElement("h3");
    title.className = "external-scan-group-title";
    title.textContent = group.season_name || "未命名季";
    titleBox.append(eyebrow, title);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "download-link-button external-scan-remove-group";
    remove.textContent = "删除分组";
    remove.addEventListener("click", () => {
      if (rowElements(card).length) {
        setGroupError("请先把该季的视频移动到其他季，再删除分组。");
        return;
      }
      if (groupCards().length <= 1) {
        setGroupError("至少保留一个季分组。");
        return;
      }
      card.remove();
      refreshMoveOptions();
      validateGroups(false);
    });
    header.append(titleBox, remove);

    const seriesLine = document.createElement("p");
    seriesLine.className = "external-scan-group-series";
    seriesLine.append("视频集：");
    const seriesValue = document.createElement("strong");
    seriesValue.className = "external-scan-group-series-value";
    seriesValue.textContent = seriesName?.value.trim() || "未填写";
    seriesLine.appendChild(seriesValue);

    const fields = document.createElement("div");
    fields.className = "external-scan-group-fields";
    const nameLabel = document.createElement("label");
    nameLabel.textContent = "季名称";
    const nameInput = document.createElement("input");
    nameInput.className = "external-scan-group-name";
    nameInput.value = group.season_name || "";
    nameInput.addEventListener("input", () => {
      title.textContent = nameInput.value.trim() || "未命名季";
      refreshMoveOptions();
      validateGroups(false);
    });
    nameLabel.appendChild(nameInput);

    const numberLabel = document.createElement("label");
    numberLabel.textContent = "季编号";
    const numberInput = document.createElement("input");
    numberInput.type = "number";
    numberInput.min = "1";
    numberInput.className = "external-scan-group-number";
    numberInput.value = group.season_number || "";
    numberInput.disabled = group.number_editable === false;
    numberInput.addEventListener("input", () => {
      refreshMoveOptions();
      renderTargetPreview();
      validateGroups(false);
    });
    numberLabel.appendChild(numberInput);
    fields.append(nameLabel, numberLabel);

    const itemHeading = document.createElement("div");
    itemHeading.className = "external-scan-group-list-heading";
    itemHeading.innerHTML =
      "<span>文件名</span><span>大小</span><span>集数</span><span>归属季</span><span>提示</span>";
    const items = document.createElement("div");
    items.className = "external-scan-group-items";
    for (const item of group.items || []) {
      items.appendChild(createRow(item, card));
    }

    const warnings = document.createElement("p");
    warnings.className = "external-scan-group-warning";
    warnings.textContent = (group.warnings || []).join("；");
    card.append(header, seriesLine, fields, itemHeading, items, warnings);
    return card;
  }

  function renderGroups(groups) {
    if (!groupsRoot) return;
    groupSequence = 0;
    groupsRoot.replaceChildren();
    for (const group of groups || []) {
      groupsRoot.appendChild(createGroupCard(group));
    }
    refreshMoveOptions();
    validateGroups(false);
  }

  function addGroup() {
    if (mode.value !== "collection") return;
    const numbers = groupCards()
      .map((card) => Number(
        card.querySelector(".external-scan-group-number")?.value,
      ))
      .filter((value) => Number.isInteger(value) && value > 0);
    const next = numbers.length ? Math.max(...numbers) + 1 : 1;
    groupsRoot.appendChild(createGroupCard({
      group_key: `manual-${++groupSequence}`,
      season_name: `第${next}季`,
      season_number: next,
      number_editable: true,
      warnings: [],
      items: [],
    }));
    refreshMoveOptions();
  }

  function renderSummary(payload) {
    previewSummary.replaceChildren();
    for (const [label, value] of [
      ["来源类型", payload.source_type === "file" ? "单个视频" : "文件夹"],
      ["视频数量", `${payload.file_count} 个`],
      ["总大小", formatBytes(payload.total_bytes)],
      ["外盘", payload.volume?.label || "已识别"],
    ]) {
      const row = document.createElement("div");
      const term = document.createElement("span");
      const description = document.createElement("strong");
      term.textContent = label;
      description.textContent = value;
      row.append(term, description);
      previewSummary.appendChild(row);
    }
    previewSamples.replaceChildren();
    for (const item of payload.samples || []) {
      const row = document.createElement("div");
      row.className = "import-preview-sample";
      const name = document.createElement("span");
      name.textContent = item.name;
      name.title = item.path;
      const meta = document.createElement("small");
      const detected = item.episode_number
        ? `${item.season_number ? `第 ${item.season_number} 季 · ` : ""}`
          + `第 ${item.episode_number} 集`
        : "未自动识别季集";
      meta.textContent = `${detected} · ${formatBytes(item.size)}`;
      row.append(name, meta);
      previewSamples.appendChild(row);
    }
  }

  function renderPreview(payload) {
    previewSection.hidden = false;
    lastDiscovery = payload;
    previewReady = payload.file_count > 0;
    if (
      payload.source_type === "file"
      && ["batch", "collection"].includes(mode.value)
    ) {
      mode.value = "file";
    } else if (
      payload.source_type === "directory"
      && mode.value === "file"
    ) {
      mode.value = "batch";
    }
    syncModePanels();
    renderSummary(payload);
    if (mode.value === "collection") {
      if (seriesName && !seriesName.value.trim()) {
        seriesName.value = payload.suggested_series_name || "";
      }
      renderGroups(payload.groups || []);
    } else {
      if (groupsRoot) groupsRoot.replaceChildren();
    }
    updateSubmitState();
  }

  async function updatePreview() {
    clearTimeout(previewTimer);
    const sourcePath = source.value.trim();
    setError("");
    if (!sourcePath) {
      previewSection.hidden = true;
      previewReady = false;
      submit.disabled = true;
      return;
    }
    try {
      const response = await fetch(
        "/api/manage/external-scans/preview?"
        + `source_path=${encodeURIComponent(sourcePath)}`
        + `&scan_mode=${encodeURIComponent(mode.value)}`,
        { cache: "no-store" },
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(detailMessage(payload, `HTTP ${response.status}`));
      }
      renderPreview(payload);
    } catch (previewError) {
      previewSection.hidden = true;
      previewReady = false;
      submit.disabled = true;
      setError(previewError.message);
    }
  }

  function schedulePreview() {
    clearTimeout(previewTimer);
    submit.disabled = true;
    previewTimer = setTimeout(updatePreview, 250);
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
    for (const item of payload.directories || []) {
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
        mode.value = "file";
        seriesName.value = "";
        dialog.close();
        syncModePanels();
        schedulePreview();
      });
      dialogList.appendChild(button);
    }
  }

  async function browse(path) {
    dialogError.textContent = "";
    try {
      const response = await fetch(
        `/api/manage/external-scans/browse?path=${encodeURIComponent(path)}`,
        { cache: "no-store" },
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(detailMessage(payload, `HTTP ${response.status}`));
      }
      renderBrowser(payload);
    } catch (browseError) {
      dialogError.textContent = browseError.message;
    }
  }

  document.getElementById("browse-external-scan-source")?.addEventListener(
    "click",
    () => {
      dialog.showModal();
      browse(source.value.trim() || "/Volumes");
    },
  );
  document.querySelectorAll("[data-close-external-browser]").forEach(
    (button) => button.addEventListener("click", () => dialog.close()),
  );
  selectDirectory?.addEventListener("click", () => {
    if (!currentPath) return;
    source.value = currentPath;
    seriesName.value = "";
    if (mode.value === "file") mode.value = "batch";
    dialog.close();
    syncModePanels();
    schedulePreview();
  });
  document.getElementById("add-external-scan-group")?.addEventListener(
    "click",
    addGroup,
  );
  document.getElementById("reset-external-scan-groups")?.addEventListener(
    "click",
    () => {
      if (!lastDiscovery?.groups) return;
      renderGroups(lastDiscovery.groups);
      setGroupError("");
    },
  );
  attachSeries?.addEventListener("change", () => {
    targetLookupSequence += 1;
    targetAssignment = null;
    setTargetError("");
    syncModePanels();
    renderTargetPreview();
    updateGroupSummary();
    if (attachSeries.checked && targetSeriesId) targetSeriesId.focus();
  });
  targetSeriesId?.addEventListener("input", () => {
    targetLookupSequence += 1;
    targetAssignment = null;
    setTargetError("");
    renderTargetPreview();
    updateGroupSummary();
  });
  targetSeriesId?.addEventListener("change", () => {
    if (attachSeries?.checked) lookupTargetSeries();
  });
  targetSeriesId?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    lookupTargetSeries();
  });
  loadTarget?.addEventListener("click", lookupTargetSeries);
  attachSeason?.addEventListener("change", () => {
    targetSeasonLookupSequence += 1;
    targetSeasonAssignment = null;
    setTargetSeasonError("");
    syncModePanels();
    renderTargetSeasonPreview();
    updateSubmitState();
    if (attachSeason.checked && targetSeasonId) targetSeasonId.focus();
  });
  targetSeasonId?.addEventListener("input", () => {
    targetSeasonLookupSequence += 1;
    targetSeasonAssignment = null;
    setTargetSeasonError("");
    renderTargetSeasonPreview();
    updateSubmitState();
  });
  targetSeasonId?.addEventListener("change", () => {
    if (attachSeason?.checked) lookupTargetSeason();
  });
  targetSeasonId?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    lookupTargetSeason();
  });
  loadTargetSeason?.addEventListener("click", lookupTargetSeason);
  seriesName?.addEventListener("input", () => {
    updateSeriesLabels();
    validateGroups(false);
  });
  source.addEventListener("change", () => {
    seriesName.value = "";
    schedulePreview();
  });
  mode.addEventListener("change", () => {
    syncModePanels();
    suggestSeriesName();
    updateSubmitState();
    if (source.value.trim()) schedulePreview();
  });
  syncModePanels();

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    setError("");
    if (!previewReady) {
      setError("请先选择可扫描的外盘视频或目录。");
      return;
    }
    if (mode.value === "collection" && !validateGroups(true)) {
      return;
    }
    if (
      ["batch", "file"].includes(mode.value)
      && attachSeason?.checked
      && !validateTargetSeason(true)
    ) {
      return;
    }
    submit.disabled = true;
    try {
      const payload = {
        source_path: source.value.trim(),
        scan_mode: mode.value,
        access_level: Number(accessLevel.value),
        series_name: seriesName?.value.trim() || "",
        season_name: "",
        season_number: 1,
        attach_to_series: Boolean(
          mode.value === "collection" && attachSeries?.checked,
        ),
        target_series_id: (
          mode.value === "collection" && attachSeries?.checked
            ? Number(targetSeriesId?.value)
            : null
        ),
        attach_to_season: Boolean(
          ["batch", "file"].includes(mode.value)
          && attachSeason?.checked,
        ),
        target_season_id: (
          ["batch", "file"].includes(mode.value)
          && attachSeason?.checked
            ? Number(targetSeasonId?.value)
            : null
        ),
      };
      if (mode.value === "collection") {
        payload.groups = groupCards().map((card) => ({
          season_name: card.querySelector(
            ".external-scan-group-name",
          ).value.trim(),
          season_number: Number(
            card.querySelector(".external-scan-group-number").value,
          ),
          items: rowElements(card).map((row) => ({
            source_path: row.dataset.sourcePath,
            episode_number: Number(
              row.querySelector(".external-scan-episode-input").value,
            ),
          })),
        }));
      }
      const response = await fetch("/api/manage/external-scans", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(detailMessage(result, `HTTP ${response.status}`));
      }
      window.location.href =
        `/manage/videos/external-scan/${encodeURIComponent(result.scan_id)}`;
    } catch (submitError) {
      setError(submitError.message);
      submit.disabled = false;
    }
  });
})();
