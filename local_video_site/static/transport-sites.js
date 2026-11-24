(() => {
  const openButton = document.getElementById("new-transport-site");
  const dialog = document.getElementById("transport-site-dialog");
  const form = document.getElementById("transport-site-form");
  const errorNode = document.getElementById("transport-site-error");
  const notice = document.getElementById("transport-notice");
  if (!openButton || !dialog || !form) return;

  function message(text, error = false) {
    if (!notice) return;
    notice.textContent = text;
    notice.classList.toggle("is-error", error);
    notice.hidden = !text;
  }

  const guideButton = document.getElementById("show-transport-guide");
  const guideDialog = document.getElementById("transport-config-guide");
  if (guideButton && guideDialog) {
    guideButton.addEventListener("click", () => guideDialog.showModal());
    guideDialog.querySelectorAll("[data-close-transport-guide]").forEach((node) => {
      node.addEventListener("click", () => guideDialog.close());
    });
    guideDialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      guideDialog.close();
    });
    guideDialog.addEventListener("click", (event) => {
      if (event.target === guideDialog) guideDialog.close();
    });
  }

  function closeDialog() {
    if (dialog.open) dialog.close();
  }

  openButton.addEventListener("click", () => {
    form.reset();
    if (errorNode) errorNode.textContent = "";
    dialog.showModal();
    form.elements.display_name.focus();
  });

  dialog.querySelectorAll("[data-close-transport-dialog]").forEach((node) => {
    node.addEventListener("click", closeDialog);
  });
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeDialog();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorNode.textContent = "";
    const data = new FormData(form);
    const payload = {
      display_name: String(data.get("display_name") || "").trim(),
      site_key: String(data.get("site_key") || "").trim(),
      domains: String(data.get("domains") || "")
        .split(/\r?\n/)
        .map(value => value.trim())
        .filter(Boolean),
      transport_mode: data.get("transport_mode"),
      lifecycle_state: data.get("lifecycle_state"),
      initial_connections: Number(data.get("initial_connections")),
      max_connections: Number(data.get("max_connections")),
      tls_backend: data.get("tls_backend"),
      note: String(data.get("note") || "").trim(),
    };
    try {
      const response = await fetch("/api/transport-sites", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(result.detail || "保存站点策略失败");
      }
      closeDialog();
      window.location.href = `/downloads/site-policies/${result.site_key}`;
    } catch (error) {
      errorNode.textContent = error.message || String(error);
    }
  });

  const editDialog = document.getElementById("transport-site-edit-dialog");
  const editForm = document.getElementById("transport-site-edit-form");
  const editError = document.getElementById("transport-site-edit-error");
  const discardDialog = document.getElementById("transport-edit-discard-dialog");
  const breakerInput = document.getElementById("edit-transport-breaker");
  const cooldownField = document.getElementById("edit-transport-cooldown-field");
  let editingPolicy = null;
  let originalEditValue = "";

  if (
    !editDialog
    || !editForm
    || !discardDialog
    || !breakerInput
    || !cooldownField
  ) return;

  function setFormValue(name, value) {
    const field = editForm.elements[name];
    if (field) field.value = value ?? "";
  }

  function editSnapshot() {
    const values = {};
    for (const element of editForm.elements) {
      if (!element.name || element.disabled) continue;
      values[element.name] = element.value;
    }
    return JSON.stringify(values);
  }

  function toggleCooldown() {
    cooldownField.hidden = breakerInput.value !== "OPEN";
  }

  function populateEdit(policy) {
    editingPolicy = policy;
    setFormValue("display_name", policy.display_name);
    setFormValue("domains", (policy.domains || []).join("\n"));
    setFormValue("transport_mode", policy.transport_mode);
    setFormValue("lifecycle_state", policy.lifecycle_state);
    setFormValue("initial_connections", policy.initial_connections);
    setFormValue("max_connections", policy.max_connections);
    setFormValue("tls_backend", policy.tls_backend);
    setFormValue("breaker_state", policy.breaker_state);
    setFormValue("note", "");
    document.getElementById("edit-transport-site-key").textContent = policy.site_key;
    document.getElementById("edit-transport-policy-version").textContent = policy.policy_version;
    document.getElementById("edit-transport-site-key-input").value = policy.site_key;
    toggleCooldown();
    editError.textContent = "";
    originalEditValue = editSnapshot();
  }

  async function openEditor(siteKey) {
    try {
      const response = await fetch(`/api/transport-sites/${encodeURIComponent(siteKey)}`);
      const policy = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(policy.detail || "读取站点策略失败");
      populateEdit(policy);
      editDialog.showModal();
      editForm.elements.display_name.focus();
    } catch (error) {
      message(error.message || String(error), true);
    }
  }

  function requestCloseEditor() {
    if (editSnapshot() !== originalEditValue) {
      discardDialog.showModal();
      return;
    }
    editDialog.close();
  }

  document.querySelectorAll("[data-edit-transport-site]").forEach((node) => {
    node.addEventListener("click", () => {
      openEditor(node.dataset.editTransportSite);
    });
  });

  breakerInput.addEventListener("change", toggleCooldown);
  editDialog.querySelectorAll("[data-close-transport-edit]").forEach((node) => {
    node.addEventListener("click", requestCloseEditor);
  });
  editDialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    requestCloseEditor();
  });
  document.getElementById("continue-transport-edit").addEventListener("click", () => {
    discardDialog.close();
  });
  document.getElementById("discard-transport-edit").addEventListener("click", () => {
    discardDialog.close();
    editDialog.close();
  });

  editForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!editingPolicy) return;
    const note = String(editForm.elements.note.value || "").trim();
    const state = editForm.elements.lifecycle_state.value;
    const breaker = editForm.elements.breaker_state.value;
    if (
      ["VERIFIED", "QUARANTINED", "DISABLED"].includes(state)
      && state !== editingPolicy.lifecycle_state
      && !note
    ) {
      editError.textContent = "修改为 VERIFIED、QUARANTINED 或 DISABLED 时必须填写备注";
      return;
    }
    if (breaker === "OPEN" && !note) {
      editError.textContent = "手动打开熔断时必须填写备注";
      return;
    }
    const initial = Number(editForm.elements.initial_connections.value);
    const maximum = Number(editForm.elements.max_connections.value);
    if (initial < 1 || initial > 2 || maximum < 2 || maximum > 4 || maximum < initial) {
      editError.textContent = "连接数必须满足初始 1–2、最高 2–4，且最高不小于初始";
      return;
    }
    editError.textContent = "";
    const submitButton = document.getElementById("save-transport-site-edit");
    submitButton.disabled = true;
    submitButton.textContent = "保存中…";
    try {
      const response = await fetch(
        `/api/transport-sites/${encodeURIComponent(editingPolicy.site_key)}`,
        {
          method: "PATCH",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            display_name: editForm.elements.display_name.value.trim(),
            domains: editForm.elements.domains.value
              .split(/\r?\n/)
              .map(value => value.trim())
              .filter(Boolean),
            transport_mode: editForm.elements.transport_mode.value,
            lifecycle_state: state,
            breaker_state: breaker,
            cooldown_minutes: Number(editForm.elements.cooldown_minutes.value),
            initial_connections: initial,
            max_connections: maximum,
            tls_backend: editForm.elements.tls_backend.value,
            note,
            expected_policy_version: editingPolicy.policy_version,
            expected_updated_at: editingPolicy.updated_at,
          }),
        },
      );
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || "保存站点策略失败");
      originalEditValue = editSnapshot();
      editDialog.close();
      window.location.reload();
    } catch (error) {
      editError.textContent = error.message || String(error);
      submitButton.disabled = false;
      submitButton.textContent = "保存修改";
    }
  });
})();
