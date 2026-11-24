(() => {
  const dialog = document.getElementById("manual-category-dialog");
  const form = document.getElementById("manual-category-form");
  const openButton = document.getElementById("add-manual-category");
  const nameInput = document.getElementById("manual-category-name");
  const errorNode = document.getElementById("manual-category-error");
  const saveButton = document.getElementById("save-manual-category");
  const list = document.getElementById("manual-category-list");
  const toggleDeleteButton = document.getElementById(
    "toggle-manual-category-delete",
  );
  const section = document.querySelector(".manual-category-section");
  const deleteDialog = document.getElementById(
    "manual-category-delete-dialog",
  );
  const deleteForm = document.getElementById(
    "manual-category-delete-form",
  );
  const deleteDescription = document.getElementById(
    "manual-category-delete-description",
  );
  const deleteError = document.getElementById(
    "manual-category-delete-error",
  );
  const confirmDeleteButton = document.getElementById(
    "confirm-manual-category-delete",
  );
  if (
    !dialog
    || !form
    || !openButton
    || !nameInput
    || !list
    || !toggleDeleteButton
    || !section
    || !deleteDialog
    || !deleteForm
    || !confirmDeleteButton
  ) return;

  let pendingDeleteButton = null;
  let pendingDeleteChip = null;

  function showError(message) {
    if (errorNode) errorNode.textContent = message || "";
  }

  openButton.addEventListener("click", () => {
    form.reset();
    showError("");
    dialog.showModal();
    nameInput.focus();
  });

  toggleDeleteButton.addEventListener("click", () => {
    const deleting = !section.classList.contains("is-delete-mode");
    section.classList.toggle("is-delete-mode", deleting);
    toggleDeleteButton.setAttribute(
      "aria-pressed",
      deleting ? "true" : "false",
    );
    toggleDeleteButton.textContent = deleting ? "完成" : "删除";
  });

  function closeDeleteDialog() {
    if (deleteDialog.open) deleteDialog.close();
  }

  function openDeleteDialog(button) {
    pendingDeleteButton = button;
    pendingDeleteChip = button.closest("[data-manual-category-id]");
    const name = button.dataset.categoryName || "";
    const count = Number(button.dataset.videoCount || 0);
    deleteDescription.textContent = (
      `人工标签：${name}\n`
      + `当前关联视频：${count} 部`
    );
    deleteError.textContent = "";
    confirmDeleteButton.disabled = false;
    deleteDialog.showModal();
    deleteDialog
      .querySelector("[data-close-manual-category-delete]")
      ?.focus();
  }

  dialog.querySelectorAll("[data-close-manual-category]").forEach((button) => {
    button.addEventListener("click", () => dialog.close());
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    saveButton.disabled = true;
    showError("");
    try {
      const response = await fetch("/api/manual-categories", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({name: nameInput.value}),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail || "新增人工标签失败");
      }
      window.location.reload();
    } catch (error) {
      showError(error.message || String(error));
      saveButton.disabled = false;
    }
  });

  list.addEventListener("click", (event) => {
    const button = event.target.closest("[data-delete-manual-category]");
    if (!button) return;
    openDeleteDialog(button);
  });

  deleteDialog
    .querySelectorAll("[data-close-manual-category-delete]")
    .forEach((button) => {
      button.addEventListener("click", closeDeleteDialog);
    });
  deleteDialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeDeleteDialog();
  });
  deleteDialog.addEventListener("click", (event) => {
    if (event.target === deleteDialog) closeDeleteDialog();
  });
  deleteDialog.addEventListener("close", () => {
    if (pendingDeleteButton) pendingDeleteButton.disabled = false;
    pendingDeleteButton = null;
    pendingDeleteChip = null;
    deleteError.textContent = "";
  });

  deleteForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!pendingDeleteChip || !pendingDeleteButton) return;
    confirmDeleteButton.disabled = true;
    deleteError.textContent = "";
    try {
      const response = await fetch(
        `/api/manual-categories/${pendingDeleteChip.dataset.manualCategoryId}`,
        {method: "DELETE"},
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail || "删除人工标签失败");
      }
      window.location.reload();
    } catch (error) {
      deleteError.textContent = error.message || String(error);
      confirmDeleteButton.disabled = false;
    }
  });
})();
