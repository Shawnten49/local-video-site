(() => {
  const noteInput = document.getElementById("transport-action-note");
  const notice = document.getElementById("transport-notice");
  const confirmDialog = document.getElementById("transport-action-confirm");
  const confirmTitle = document.getElementById("transport-action-confirm-title");
  const confirmMessage = document.getElementById("transport-action-confirm-message");
  const confirmButton = document.getElementById("confirm-transport-action");
  const cancelButton = document.getElementById("cancel-transport-action");
  const mediaForm = document.getElementById("media-policy-form");
  const mediaError = document.getElementById("media-policy-error");
  const siteKey = window.location.pathname.split("/").filter(Boolean).pop();
  let pendingAction = "";

  function message(text, error = false) {
    if (!notice) return;
    notice.textContent = text;
    notice.classList.toggle("is-error", error);
    notice.hidden = !text;
  }

  function policyLines(value) {
    return String(value || "")
      .split(/\r?\n|,/)
      .map(item => item.trim())
      .filter(Boolean);
  }

  mediaForm?.elements.preset_key.addEventListener("change", (event) => {
    const option = event.target.selectedOptions[0];
    if (!option?.dataset.mediaPreset) return;
    try {
      const preset = JSON.parse(option.dataset.mediaPreset);
      mediaForm.elements.source_order.value = (
        preset.source_order || []
      ).join("\n");
      mediaForm.elements.identity_mode.value = preset.identity_mode;
      mediaForm.elements.kind_order.value = (
        preset.kind_order || []
      ).join("\n");
      mediaForm.elements.quality_target.value = preset.quality_target;
      mediaForm.elements.quality_fallback.value = preset.quality_fallback;
      mediaForm.elements.min_height.value = preset.min_height;
      mediaForm.elements.max_height.value = preset.max_height;
      mediaForm.elements.allow_below_minimum.checked = Boolean(
        preset.allow_below_minimum,
      );
      mediaForm.elements.allow_page_fallback.checked = Boolean(
        preset.allow_page_fallback,
      );
      mediaForm.elements.play_selector.value = preset.play_selector || "";
      mediaForm.elements.format_strategy.value = (
        preset.format_strategy || "auto"
      );
      mediaForm.elements.merge_output_format.value = (
        preset.merge_output_format || "mp4"
      );
      mediaForm.elements.prefer_h264.checked = Boolean(
        preset.prefer_h264,
      );
    } catch (_) {
      if (mediaError) mediaError.textContent = "无法应用预设默认值";
    }
  });

  mediaForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submitButton = mediaForm.querySelector('[type="submit"]');
    if (mediaError) mediaError.textContent = "";
    submitButton.disabled = true;
    try {
      const payload = {
        preset_key: mediaForm.elements.preset_key.value,
        source_order: policyLines(mediaForm.elements.source_order.value),
        identity_mode: mediaForm.elements.identity_mode.value,
        kind_order: policyLines(mediaForm.elements.kind_order.value),
        quality_target: mediaForm.elements.quality_target.value,
        quality_fallback: mediaForm.elements.quality_fallback.value,
        min_height: Number(mediaForm.elements.min_height.value),
        max_height: Number(mediaForm.elements.max_height.value),
        allow_below_minimum: mediaForm.elements.allow_below_minimum.checked,
        allow_page_fallback: mediaForm.elements.allow_page_fallback.checked,
        play_selector: mediaForm.elements.play_selector.value.trim(),
        format_strategy: mediaForm.elements.format_strategy.value,
        prefer_h264: mediaForm.elements.prefer_h264.checked,
        merge_output_format: (
          mediaForm.elements.merge_output_format.value
        ),
        note: mediaForm.elements.note.value.trim(),
        expected_version: Number(mediaForm.elements.expected_version.value),
      };
      const response = await fetch(
        `/api/transport-sites/${encodeURIComponent(siteKey)}/media-policy`,
        {
          method: "PATCH",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload),
        },
      );
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(result.detail || "保存视频选择策略失败");
      }
      window.location.reload();
    } catch (error) {
      if (mediaError) {
        mediaError.textContent = error.message || String(error);
      }
      submitButton.disabled = false;
    }
  });

  document.getElementById("reset-media-policy")?.addEventListener(
    "click",
    async () => {
      if (!window.confirm("恢复该站点的内置视频选择策略？")) return;
      const button = document.getElementById("reset-media-policy");
      button.disabled = true;
      try {
        const response = await fetch(
          `/api/transport-sites/${encodeURIComponent(siteKey)}/media-policy/reset`,
          {method: "POST"},
        );
        const result = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(result.detail || "恢复默认策略失败");
        }
        window.location.reload();
      } catch (error) {
        if (mediaError) {
          mediaError.textContent = error.message || String(error);
        }
        button.disabled = false;
      }
    },
  );

  document.getElementById("probe-media-policy")?.addEventListener(
    "click",
    async () => {
      const urlInput = document.getElementById("media-policy-probe-url");
      const button = document.getElementById("probe-media-policy");
      const resultNode = document.getElementById(
        "media-policy-probe-result",
      );
      if (!urlInput?.value.trim()) {
        if (mediaError) mediaError.textContent = "请先填写试运行网页地址";
        return;
      }
      if (mediaError) mediaError.textContent = "";
      button.disabled = true;
      button.textContent = "正在探测…";
      if (resultNode) {
        resultNode.hidden = false;
        resultNode.textContent = "正在打开页面并发现媒体，请稍候…";
      }
      try {
        const response = await fetch(
          `/api/transport-sites/${encodeURIComponent(siteKey)}/media-policy/probe`,
          {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({url: urlInput.value.trim()}),
          },
        );
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(payload.detail || "媒体策略探测失败");
        }
        const result = payload.result || {};
        if (resultNode) {
          resultNode.replaceChildren();
          const summary = document.createElement("strong");
          summary.textContent = (
            `候选 ${result.candidate_count || 0} 个 · `
            + `选择 ${result.selected_candidate_url ? "已命中" : "未命中"}`
          );
          resultNode.append(summary);
          if (result.selection_reason) {
            const reason = document.createElement("p");
            reason.textContent = result.selection_reason;
            resultNode.append(reason);
          }
          const list = document.createElement("ol");
          for (const candidate of result.candidates || []) {
            const item = document.createElement("li");
            item.textContent = (
              `${candidate.selected ? "✓ " : ""}`
              + `${candidate.source} · ${candidate.kind} · `
              + `${candidate.quality} · ${candidate.url}`
            );
            list.append(item);
          }
          resultNode.append(list);
        }
      } catch (error) {
        if (mediaError) {
          mediaError.textContent = error.message || String(error);
        }
        if (resultNode) resultNode.hidden = true;
      } finally {
        button.disabled = false;
        button.textContent = "试运行当前策略";
      }
    },
  );

  if (!confirmDialog || !confirmButton) return;

  function openConfirm(action, label) {
    const note = String(noteInput?.value || "").trim();
    if (action === "promote_verified" && !note) {
      message("提升为 Verified 时必须填写操作备注", true);
      noteInput?.focus();
      return;
    }
    pendingAction = action;
    confirmTitle.textContent = `确认执行：${label}？`;
    confirmMessage.textContent = (
      "当前正在运行的任务不会中断，新策略将在下一次任务启动时生效。"
    );
    confirmDialog.showModal();
  }

  document.querySelectorAll("[data-transport-action]").forEach((button) => {
    button.addEventListener("click", () => {
      openConfirm(
        button.dataset.transportAction,
        (button.textContent || "").trim(),
      );
    });
  });

  cancelButton?.addEventListener("click", () => confirmDialog.close());
  confirmDialog.addEventListener("click", (event) => {
    if (event.target === confirmDialog) confirmDialog.close();
  });

  confirmButton.addEventListener("click", async () => {
    if (!pendingAction) return;
    confirmButton.disabled = true;
    try {
      const response = await fetch(
        `/api/transport-sites/${encodeURIComponent(siteKey)}/actions`,
        {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            action: pendingAction,
            note: String(noteInput?.value || "").trim(),
          }),
        },
      );
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(result.detail || "站点操作失败");
      }
      confirmDialog.close();
      window.location.reload();
    } catch (error) {
      message(error.message || String(error), true);
      confirmButton.disabled = false;
    }
  });
})();
