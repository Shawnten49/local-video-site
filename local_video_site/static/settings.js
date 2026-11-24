"use strict";

(() => {
  const state = {
    category: "",
    query: "",
    includeAdvanced: true,
    items: [],
    editing: null,
  };

  const tableBody = document.getElementById("settings-table-body");
  const searchInput = document.getElementById("settings-search");
  const advancedInput = document.getElementById("settings-show-advanced");
  const countLabel = document.getElementById("settings-count");
  const titleLabel = document.getElementById("settings-current-title");
  const errorLabel = document.getElementById("settings-error");
  const noticeLabel = document.getElementById("settings-notice");
  const categoryList = document.getElementById("settings-category-list");
  const editDialog = document.getElementById("setting-edit-dialog");
  const secretDialog = document.getElementById("setting-secret-dialog");
  const historyDialog = document.getElementById("setting-history-dialog");

  function showMessage(label, message) {
    label.textContent = message || "";
    label.hidden = !message;
  }

  function applyModeLabel(value) {
    return {
      immediate: "立即生效",
      next_job: "下个任务",
      restart_required: "重启后生效",
      secret: "立即生效",
    }[value] || value;
  }

  function sourceLabel(item) {
    if (item.source === "environment") return "环境变量";
    if (item.source === "database") return "数据库覆盖";
    if (item.source === "secret_file") return "安全文件";
    if (item.source === "startup") return "启动级参数";
    if (item.source === "default_after_invalid_override") return "默认值（旧值无效）";
    if (item.source === "unconfigured") return "未配置";
    return "默认值";
  }

  function createCell(text, className = "") {
    const cell = document.createElement("td");
    cell.textContent = text;
    if (className) cell.className = className;
    return cell;
  }

  function createActionButton(label, action, item, extraClass = "") {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `settings-row-button ${extraClass}`.trim();
    button.textContent = label;
    button.addEventListener("click", () => action(item));
    return button;
  }

  function renderRows() {
    tableBody.replaceChildren();
    countLabel.textContent = `${state.items.length} 项`;
    if (!state.items.length) {
      const row = document.createElement("tr");
      const cell = createCell("没有匹配的参数", "settings-empty");
      cell.colSpan = 6;
      row.appendChild(cell);
      tableBody.appendChild(row);
      return;
    }

    state.items.forEach((item) => {
      const row = document.createElement("tr");
      if (item.dangerous) row.classList.add("is-dangerous");

      const nameCell = document.createElement("td");
      const name = document.createElement("strong");
      name.textContent = item.label;
      const key = document.createElement("code");
      key.textContent = item.key;
      const description = document.createElement("small");
      description.textContent = item.description;
      nameCell.append(name, key, description);

      const valueCell = document.createElement("td");
      const value = document.createElement("strong");
      value.textContent = item.display_value;
      valueCell.appendChild(value);
      if (item.env_locked) {
        const lock = document.createElement("span");
        lock.className = "settings-source-badge is-locked";
        lock.textContent = "环境变量锁定";
        valueCell.appendChild(lock);
      }

      const sourceCell = document.createElement("td");
      const source = document.createElement("span");
      source.className = `settings-source-badge source-${item.source}`;
      source.textContent = sourceLabel(item);
      sourceCell.appendChild(source);

      const modeCell = createCell(applyModeLabel(item.apply_mode));
      const updatedCell = document.createElement("td");
      const updatedMain = document.createElement("span");
      updatedMain.textContent = item.updated_at
        ? item.updated_at.replace("T", " ").slice(0, 19)
        : "—";
      const updatedBy = document.createElement("small");
      updatedBy.textContent = item.updated_by_username || "";
      updatedCell.append(updatedMain, updatedBy);

      const actionsCell = document.createElement("td");
      actionsCell.className = "settings-actions";
      if (item.secret) {
        actionsCell.appendChild(
          createActionButton(
            item.configured ? "更换校验码" : "设置校验码",
            openSecretDialog,
            item,
            "is-danger"
          )
        );
      } else {
        const edit = createActionButton(
          "修改",
          openEditDialog,
          item
        );
        edit.disabled = item.env_locked || !item.mutable;
        actionsCell.appendChild(edit);
        if (item.source === "database") {
          actionsCell.appendChild(
            createActionButton(
              "恢复默认",
              resetSetting,
              item,
              "is-danger"
            )
          );
        }
      }
      actionsCell.appendChild(
        createActionButton("历史", openHistoryDialog, item)
      );

      row.append(
        nameCell,
        valueCell,
        sourceCell,
        modeCell,
        updatedCell,
        actionsCell
      );
      tableBody.appendChild(row);
    });
  }

  async function loadSettings() {
    showMessage(errorLabel, "");
    const params = new URLSearchParams({
      category: state.category,
      q: state.query,
      include_advanced: state.includeAdvanced ? "true" : "false",
    });
    try {
      const response = await fetch(`/api/manage/settings?${params}`);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail?.message || payload.detail || "读取参数失败");
      }
      state.items = payload.items || [];
      renderRows();
    } catch (error) {
      showMessage(errorLabel, error.message || String(error));
      tableBody.replaceChildren();
    }
  }

  function renderValueField(item, value) {
    const field = document.getElementById("setting-value-field");
    field.replaceChildren();
    const label = document.createElement("label");
    const title = document.createElement("span");
    title.textContent = "新值";
    label.appendChild(title);
    let input;
    if (item.value_type === "bool") {
      input = document.createElement("input");
      input.type = "checkbox";
      input.id = "setting-value-input";
      input.checked = Boolean(value);
      const row = document.createElement("span");
      row.className = "settings-checkbox-row";
      row.appendChild(input);
      row.appendChild(document.createTextNode("启用"));
      label.appendChild(row);
    } else if (item.value_type === "enum") {
      input = document.createElement("select");
      input.id = "setting-value-input";
      (item.choices || []).forEach((choice) => {
        const option = document.createElement("option");
        option.value = choice;
        option.textContent = choice;
        option.selected = choice === value;
        input.appendChild(option);
      });
      label.appendChild(input);
    } else {
      input = document.createElement("input");
      input.id = "setting-value-input";
      input.type = ["int", "float", "bytes"].includes(item.value_type)
        ? "number"
        : "text";
      if (item.value_type === "float") input.step = "any";
      if (item.min_value !== null) input.min = String(item.min_value);
      if (item.max_value !== null) input.max = String(item.max_value);
      input.value = value ?? "";
      label.appendChild(input);
      if (item.unit) {
        const hint = document.createElement("small");
        hint.textContent = `单位：${item.unit}`;
        label.appendChild(hint);
      }
    }
    field.appendChild(label);
  }

  function openEditDialog(item) {
    state.editing = item;
    document.getElementById("setting-dialog-title").textContent = item.label;
    document.getElementById("setting-dialog-description").textContent = item.description;
    document.getElementById("setting-dialog-key").textContent = item.key;
    document.getElementById("setting-dialog-apply").textContent = applyModeLabel(item.apply_mode);
    document.getElementById("setting-edit-note").value = "";
    showMessage(document.getElementById("setting-dialog-error"), "");
    renderValueField(item, item.value);
    editDialog.showModal();
  }

  function readInputValue(item, input) {
    if (item.value_type === "bool") return input.checked;
    if (["int", "bytes"].includes(item.value_type)) return Number.parseInt(input.value, 10);
    if (item.value_type === "float") return Number.parseFloat(input.value);
    return input.value;
  }

  async function saveSetting(event) {
    event.preventDefault();
    const item = state.editing;
    if (!item) return;
    const input = document.getElementById("setting-value-input");
    const value = readInputValue(item, input);
    if (item.dangerous && !window.confirm(`确认修改“${item.label}”？此参数可能影响正在运行的任务。`)) {
      return;
    }
    const button = document.getElementById("setting-save-button");
    button.disabled = true;
    try {
      const response = await fetch(`/api/manage/settings/${encodeURIComponent(item.key)}`, {
        method: "PATCH",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          value,
          expected_version: item.version,
          note: document.getElementById("setting-edit-note").value,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail?.message || payload.detail || "保存参数失败");
      }
      editDialog.close();
      showMessage(noticeLabel, `“${payload.label}”已更新`);
      await loadSettings();
    } catch (error) {
      showMessage(document.getElementById("setting-dialog-error"), error.message || String(error));
    } finally {
      button.disabled = false;
    }
  }

  async function resetSetting(item) {
    if (!window.confirm(`确认恢复“${item.label}”的代码默认值？`)) return;
    try {
      const response = await fetch(`/api/manage/settings/${encodeURIComponent(item.key)}/reset`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          expected_version: item.version,
          note: "从参数配置页面恢复默认值",
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail?.message || payload.detail || "恢复默认失败");
      }
      showMessage(noticeLabel, `“${payload.label}”已恢复默认值`);
      await loadSettings();
    } catch (error) {
      showMessage(errorLabel, error.message || String(error));
    }
  }

  function openSecretDialog(item) {
    state.editing = item;
    document.getElementById("current-delete-password-field").hidden = !item.configured;
    document.getElementById("current-delete-password").value = "";
    document.getElementById("new-delete-password").value = "";
    document.getElementById("confirm-delete-password").value = "";
    document.getElementById("delete-password-note").value = "";
    showMessage(document.getElementById("delete-password-error"), "");
    secretDialog.showModal();
  }

  async function saveDeleteVerification(event) {
    event.preventDefault();
    const item = state.editing;
    try {
      const response = await fetch("/api/manage/settings/secrets/delete-verification", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          current_password: document.getElementById("current-delete-password").value,
          new_password: document.getElementById("new-delete-password").value,
          confirmation: document.getElementById("confirm-delete-password").value,
          note: document.getElementById("delete-password-note").value,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail?.message || payload.detail || "保存删除校验码失败");
      }
      secretDialog.close();
      showMessage(noticeLabel, "删除校验码已更新");
      await loadSettings();
    } catch (error) {
      showMessage(document.getElementById("delete-password-error"), error.message || String(error));
    }
  }

  async function openHistoryDialog(item) {
    const content = document.getElementById("setting-history-content");
    document.getElementById("history-dialog-title").textContent = `${item.label} · 变更历史`;
    content.textContent = "正在加载…";
    historyDialog.showModal();
    try {
      const response = await fetch(`/api/manage/settings/audit?setting_key=${encodeURIComponent(item.key)}&page_size=50`);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail?.message || payload.detail || "读取历史失败");
      }
      content.replaceChildren();
      if (!(payload.items || []).length) {
        content.textContent = "暂无变更记录";
        return;
      }
      payload.items.forEach((entry) => {
        const article = document.createElement("article");
        const heading = document.createElement("header");
        const actor = document.createElement("strong");
        actor.textContent = entry.actor_username || "系统";
        const time = document.createElement("time");
        time.textContent = (entry.changed_at || "").replace("T", " ").slice(0, 19);
        heading.append(actor, time);
        const change = document.createElement("p");
        change.textContent = `${entry.old_display || "—"} → ${entry.new_display || "—"}`;
        article.append(heading, change);
        if (entry.note) {
          const note = document.createElement("small");
          note.textContent = entry.note;
          article.appendChild(note);
        }
        content.appendChild(article);
      });
    } catch (error) {
      content.textContent = error.message || String(error);
    }
  }

  categoryList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-category]");
    if (!button) return;
    state.category = button.dataset.category || "";
    categoryList.querySelectorAll("[data-category]").forEach((item) => {
      item.classList.toggle("is-active", item === button);
    });
    titleLabel.textContent = button.textContent.trim().replace(/\s+\d+$/, "");
    loadSettings();
  });

  let searchTimer = null;
  searchInput.addEventListener("input", () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      state.query = searchInput.value.trim();
      loadSettings();
    }, 180);
  });
  advancedInput.addEventListener("change", () => {
    state.includeAdvanced = advancedInput.checked;
    loadSettings();
  });

  document.getElementById("setting-edit-form").addEventListener("submit", saveSetting);
  document.getElementById("setting-secret-form").addEventListener("submit", saveDeleteVerification);
  document.querySelectorAll("[data-close-setting-dialog]").forEach((button) => {
    button.addEventListener("click", () => editDialog.close());
  });
  document.querySelectorAll("[data-close-secret-dialog]").forEach((button) => {
    button.addEventListener("click", () => secretDialog.close());
  });
  document.querySelectorAll("[data-close-history-dialog]").forEach((button) => {
    button.addEventListener("click", () => historyDialog.close());
  });

  loadSettings();
})();
