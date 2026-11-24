(() => {
  const form = document.getElementById("episode-batch-form");
  const discoverButton = document.getElementById("discover-episode-batch");
  const confirmButton = document.getElementById("confirm-episode-batch");
  const errorNode = document.getElementById("episode-batch-error");
  const previewSection = document.getElementById("episode-preview-section");
  const previewTitle = document.getElementById("episode-preview-title");
  const previewSummary = document.getElementById("episode-preview-summary");
  const previewWarning = document.getElementById("episode-preview-warning");
  const previewRows = document.getElementById("episode-preview-rows");
  const selectAll = document.getElementById("episode-preview-select-all");
  const continuousInput = document.getElementById("episode-continuous-parse");
  const aggregateInput = document.getElementById(
    "episode-aggregate-single-season",
  );
  const aggregateOption = document.getElementById("episode-aggregate-option");
  const preserveInput = document.getElementById(
    "episode-preserve-original-name",
  );
  const preserveOption = document.getElementById(
    "episode-preserve-original-option",
  );
  const modeHint = document.getElementById("episode-batch-mode-hint");
  const seasonNameInput = document.getElementById("episode-season-name");
  const seasonNumberInput = document.getElementById("episode-season-number");
  const allowSourceMismatch = document.getElementById(
    "episode-allow-source-mismatch",
  );
  if (
    !form
    || !discoverButton
    || !confirmButton
    || !errorNode
    || !previewSection
    || !previewRows
    || !continuousInput
    || !aggregateInput
    || !preserveInput
  ) return;

  let currentBatch = null;

  function setError(message) {
    errorNode.textContent = message || "";
  }

  async function readError(response, fallback) {
    const payload = await response.json().catch(() => ({}));
    const detail = payload.detail;
    if (detail && typeof detail === "object") {
      return detail.message || fallback;
    }
    return detail || fallback;
  }

  function escapeHtml(value) {
    return String(value || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function syncDiscoveryOptions() {
    const continuous = continuousInput.checked;
    if (!continuous) aggregateInput.checked = false;
    aggregateInput.disabled = !continuous;
    aggregateOption?.classList.toggle("is-disabled", !continuous);
    if (!aggregateInput.checked) preserveInput.checked = false;
    preserveInput.disabled = !aggregateInput.checked;
    preserveOption?.classList.toggle(
      "is-disabled",
      !aggregateInput.checked,
    );
    const grouped = continuous && !aggregateInput.checked;
    seasonNameInput.disabled = grouped;
    seasonNumberInput.disabled = grouped;
    if (!continuous) {
      modeHint.textContent = (
        "当前仅解析输入地址所在的选集。季名和季序号使用上方填写值。"
      );
    } else if (aggregateInput.checked) {
      modeHint.textContent = (
        preserveInput.checked
          ? "全部来源 Tab 会汇总到上方目标季，文件名使用影片或分集标题；"
            + "内部集数仍按 Tab 顺序连续编号。"
          : "全部来源 Tab 会汇总到上方目标季，视频按 Tab 顺序统一连续编号；"
            + "原季名仍保留在来源信息中。"
      );
    } else {
      modeHint.textContent = (
        "各来源 Tab 会保留为独立季，季名和季序号由解析结果自动识别。"
      );
    }
  }

  continuousInput.addEventListener("change", syncDiscoveryOptions);
  aggregateInput.addEventListener("change", syncDiscoveryOptions);
  preserveInput.addEventListener("change", syncDiscoveryOptions);
  syncDiscoveryOptions();

  function tabNameLabel(item) {
    return (
      item.source_tab_item_title
      || (
        item.source_episode_number
          ? `第 ${item.source_episode_number} 集`
          : ""
      )
      || item.source_tab_title
      || item.source_season_name
      || currentBatch?.season_name
      || "—"
    );
  }

  function originalEpisodeLabel(item) {
    const sourceNumber = item.source_episode_number;
    return sourceNumber ? `第 ${sourceNumber} 集` : "—";
  }

  function renderItem(item) {
    const row = document.createElement("tr");
    const enabled = ["ready", "existing_task"].includes(
      item.validation_status,
    );
    row.innerHTML = `
      <td><input type="checkbox" data-item-id="${item.id}"
        ${item.selected ? "checked" : ""} ${enabled ? "" : "disabled"}></td>
      <td>${escapeHtml(String(item.episode_number).padStart(3, "0"))}</td>
      <td><span class="episode-tab-name"
        title="${escapeHtml(tabNameLabel(item))}">
        ${escapeHtml(tabNameLabel(item))}
      </span></td>
      <td><strong>${escapeHtml(item.display_title)}</strong>
        <small>${escapeHtml(item.file_name)}</small></td>
      <td>${escapeHtml(originalEpisodeLabel(item))}</td>
      <td><code>${escapeHtml(item.relative_path)}</code></td>
      <td><a href="${escapeHtml(item.source_url)}" target="_blank"
        rel="noopener noreferrer">${escapeHtml(item.source_url)}</a></td>
      <td><span class="episode-status episode-status-${item.validation_status}">
        ${escapeHtml(item.validation_message || item.validation_status)}
      </span></td>
    `;
    previewRows.appendChild(row);
  }

  function renderGroup(group, index) {
    if (!group.items?.length && group.status === "ready") return;
    const heading = document.createElement("tr");
    heading.className = "episode-preview-group";
    const statusLabel = group.status === "ready"
      ? `${group.source_item_count} 个来源视频`
      : `解析失败${group.warning ? ` · ${group.warning}` : ""}`;
    heading.innerHTML = `
      <td colspan="8">
        <strong>${String(index + 1).padStart(2, "0")} ·
          ${escapeHtml(group.tab_title || group.season_name)}
        </strong>
        <small>${escapeHtml(statusLabel)}</small>
      </td>
    `;
    previewRows.appendChild(heading);
    group.items.forEach(renderItem);
  }

  function renderPreview(batch) {
    currentBatch = batch;
    previewRows.textContent = "";
    const items = batch.items || [];
    const enabledItems = items.filter(
      item => ["ready", "existing_task"].includes(item.validation_status),
    );
    const selectedCount = items.filter(item => item.selected).length;
    if (batch.discovery_id && batch.mode === "continuous_aggregate") {
      previewTitle.textContent = (
        `${batch.series_name} · ${batch.target_season_name || "汇总季"}`
      );
      previewSummary.textContent = (
        `解析 ${batch.tab_count} 个来源 Tab，汇总 ${items.length} 个视频，`
        + `默认选择 ${selectedCount} 个`
        + (
          batch.file_name_mode === "original"
            ? "，文件名保留原始名称"
            : ""
        )
      );
      const heading = document.createElement("tr");
      heading.className = "episode-preview-group";
      heading.innerHTML = `
        <td colspan="8">
          <strong>汇总目标 ·
            ${escapeHtml(batch.series_name)} /
            ${escapeHtml(batch.target_season_name || "汇总季")}
          </strong>
          <small>已按来源 Tab 顺序连续编号</small>
        </td>
      `;
      previewRows.appendChild(heading);
      items.forEach(renderItem);
      (batch.groups || [])
        .filter(group => group.status !== "ready")
        .forEach(renderGroup);
    } else if (batch.discovery_id) {
      previewTitle.textContent = `${batch.series_name} · 连续解析`;
      previewSummary.textContent = (
        `解析 ${batch.tab_count} 个季 Tab，发现 ${items.length} 个视频，`
        + `可选 ${enabledItems.length} 个，默认选择 ${selectedCount} 个`
      );
      (batch.groups || []).forEach(renderGroup);
    } else {
      previewTitle.textContent = `${batch.series_name} · ${batch.season_name}`;
      previewSummary.textContent = (
        `发现 ${items.length} 集，可选 ${enabledItems.length} 集，`
        + `默认选择 ${selectedCount} 集`
      );
      const group = {
        tab_title: batch.season_name,
        source_item_count: items.length,
        status: "ready",
        items,
      };
      renderGroup(group, 0);
    }
    const warnings = [];
    if (batch.warning) warnings.push(batch.warning);
    if (batch.discovery_id && !batch.complete) {
      warnings.push("部分来源 Tab 未能完整解析，请检查失败项。");
    }
    previewWarning.hidden = !warnings.length;
    previewWarning.textContent = warnings.join(" ");
    previewSection.hidden = false;
    syncSelectAll();
    previewSection.scrollIntoView({behavior: "smooth", block: "start"});
  }

  function updateSelectAllState() {
    if (!selectAll) return;
    const enabled = Array.from(
      previewRows.querySelectorAll("input[data-item-id]:not(:disabled)"),
    );
    const checked = enabled.filter(input => input.checked);
    selectAll.checked = enabled.length > 0 && checked.length === enabled.length;
    selectAll.indeterminate = checked.length > 0
      && checked.length < enabled.length;
    selectAll.disabled = enabled.length === 0;
  }

  function syncSelectAll() {
    updateSelectAllState();
  }

  selectAll?.addEventListener("change", () => {
    previewRows
      .querySelectorAll("input[data-item-id]:not(:disabled)")
      .forEach((input) => {
        input.checked = selectAll.checked;
      });
    updateSelectAllState();
  });

  previewRows.addEventListener("change", (event) => {
    if (!event.target.matches("input[data-item-id]")) return;
    updateSelectAllState();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    setError("");
    discoverButton.disabled = true;
    discoverButton.textContent = continuousInput.checked
      ? "正在连续解析选集…"
      : "正在解析剧集…";
    previewSection.hidden = true;
    try {
      const response = await fetch("/api/episode-batches/discover", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          series_name: document.getElementById("episode-series-name").value,
          season_name: seasonNameInput.value,
          season_number: Number(seasonNumberInput.value),
          first_episode_url: document.getElementById("episode-first-url").value,
          output_root: document.getElementById("episode-output-root").value,
          quality: document.getElementById("episode-quality").value,
          access_level: Number(
            document.getElementById("episode-access-level").value,
          ),
          continuous_parse: continuousInput.checked,
          aggregate_single_season: aggregateInput.checked,
          preserve_original_name: preserveInput.checked,
          allow_source_series_mismatch: Boolean(
            allowSourceMismatch?.checked
          ),
        }),
      });
      if (!response.ok) {
        throw new Error(await readError(response, "剧集解析失败"));
      }
      renderPreview(await response.json());
    } catch (error) {
      setError(error.message || String(error));
    } finally {
      discoverButton.disabled = false;
      discoverButton.textContent = "解析并预览";
    }
  });

  confirmButton.addEventListener("click", async () => {
    if (!currentBatch) return;
    const selectedIds = Array.from(
      previewRows.querySelectorAll("input[data-item-id]:checked"),
    ).map(input => Number(input.dataset.itemId));
    if (!selectedIds.length) {
      setError("请至少选择一集");
      return;
    }
    setError("");
    confirmButton.disabled = true;
    confirmButton.textContent = "正在生成任务…";
    const discoveryId = currentBatch.discovery_id;
    const endpoint = discoveryId
      ? `/api/episode-discoveries/${encodeURIComponent(discoveryId)}/confirm`
      : `/api/episode-batches/${encodeURIComponent(
        currentBatch.batch_id,
      )}/confirm`;
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          revision: currentBatch.revision,
          selected_item_ids: selectedIds,
        }),
      });
      if (!response.ok) {
        throw new Error(await readError(response, "生成下载任务失败"));
      }
      const result = await response.json();
      const batchId = discoveryId || currentBatch.batch_id;
      const warning = result.errors?.length
        ? `，${result.errors.length} 个来源 Tab 失败`
        : "";
      window.location.assign(
        `/downloads?q=${encodeURIComponent(batchId)}`
        + `&batch_notice=${encodeURIComponent(
          `已确认 ${result.selected} 集，新建 ${result.created} 个任务${warning}`,
        )}`,
      );
    } catch (error) {
      setError(error.message || String(error));
      confirmButton.disabled = false;
      confirmButton.textContent = "确认批量生成下载任务";
    }
  });
})();
