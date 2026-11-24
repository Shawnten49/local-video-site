(() => {
  const button = document.getElementById("edit-video-name");
  const dialog = document.getElementById("rename-video-dialog");
  const form = document.getElementById("rename-video-form");
  const input = document.getElementById("rename-video-input");
  const extensionNode = document.getElementById("rename-video-extension");
  const currentFileNode = document.getElementById("rename-current-file");
  const previewNode = document.getElementById("rename-video-preview");
  const errorNode = document.getElementById("rename-video-error");
  const saveButton = document.getElementById("save-video-name");
  const titleNode = document.getElementById("video-title-text");
  const pathNode = document.getElementById("video-file-path");
  const noticeNode = document.getElementById("video-rename-notice");
  const deleteButton = document.getElementById("delete-video-button");
  const deleteTitleNode = document.getElementById("delete-video-title");

  if (
    !button
    || !dialog
    || !form
    || !input
    || !extensionNode
    || !previewNode
    || !saveButton
    || !titleNode
    || !pathNode
  ) return;

  let submitting = false;

  function normalizedTitle(value) {
    let title = String(value || "").normalize("NFKC");
    title = title.split("#", 1)[0];
    return title.replace(/\s+/g, " ").trim();
  }

  function validationError(title) {
    if (!title) return "请输入视频名称";
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

  function updatePreview() {
    const title = normalizedTitle(input.value);
    const error = validationError(title);
    const extension = button.dataset.extension || "";
    const directory = button.dataset.directory || "";
    previewNode.textContent = title
      ? `${directory}/${title}${extension}`
      : `${directory}/…${extension}`;
    errorNode.textContent = error;
    saveButton.disabled = Boolean(error) || submitting;
    return title;
  }

  function closeDialog() {
    if (dialog.open) dialog.close();
  }

  function showNotice(message, isError = false) {
    if (!noticeNode) return;
    noticeNode.textContent = message;
    noticeNode.classList.toggle("is-error", isError);
    noticeNode.hidden = !message;
    if (message) {
      window.setTimeout(() => {
        noticeNode.hidden = true;
      }, 3500);
    }
  }

  function applyRename(payload) {
    pathNode.textContent = payload.path;
    currentFileNode.textContent = payload.file_name;
    button.dataset.videoPath = payload.path;
    button.dataset.fileStem = payload.file_stem || "";
  }

  button.addEventListener("click", () => {
    if (submitting) return;
    errorNode.textContent = "";
    input.value = button.dataset.fileStem || "";
    extensionNode.textContent = button.dataset.extension || "";
    currentFileNode.textContent = (
      `${button.dataset.fileStem || ""}${button.dataset.extension || ""}`
    );
    updatePreview();
    dialog.showModal();
    input.focus();
    input.select();
  });

  input.addEventListener("input", updatePreview);

  dialog.querySelectorAll("[data-close-rename-video]").forEach((node) => {
    node.addEventListener("click", closeDialog);
  });
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    if (!submitting) closeDialog();
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog && !submitting) closeDialog();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const title = updatePreview();
    if (validationError(title) || submitting) return;
    submitting = true;
    saveButton.disabled = true;
    saveButton.textContent = "保存中…";
    errorNode.textContent = "";
    try {
      const response = await fetch(
        `/api/videos/${button.dataset.videoId}/name`,
        {
          method: "PATCH",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            title,
            expected_path: button.dataset.videoPath,
            return_to: button.dataset.returnTo || "",
          }),
        },
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail || "修改文件名称失败");
      }
      applyRename(payload);
      closeDialog();
      showNotice("文件名称已更新，展示标题保持不变");
    } catch (error) {
      errorNode.textContent = error.message || String(error);
    } finally {
      submitting = false;
      saveButton.textContent = "保存";
      saveButton.disabled = Boolean(validationError(updatePreview()));
    }
  });
})();
