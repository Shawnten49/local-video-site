(() => {
  const button = document.getElementById("edit-manual-categories");
  const dialog = document.getElementById("manual-category-edit-dialog");
  const form = document.getElementById("manual-category-edit-form");
  const search = document.getElementById("manual-category-option-search");
  const errorNode = document.getElementById("manual-category-edit-error");
  const saveButton = document.getElementById("save-video-manual-categories");
  if (!button || !dialog || !form || !search || !saveButton) return;

  const options = Array.from(
    dialog.querySelectorAll(".manual-category-option"),
  );

  function filterOptions() {
    const query = search.value.trim().toLocaleLowerCase();
    for (const option of options) {
      const name = (option.dataset.categoryName || "").toLocaleLowerCase();
      option.hidden = Boolean(query && !name.includes(query));
    }
  }

  function closeDialog() {
    if (dialog.open) dialog.close();
  }

  button.addEventListener("click", () => {
    if (errorNode) errorNode.textContent = "";
    search.value = "";
    filterOptions();
    dialog.showModal();
    search.focus();
  });

  search.addEventListener("input", filterOptions);
  dialog.querySelectorAll("[data-close-manual-category-edit]").forEach((node) => {
    node.addEventListener("click", closeDialog);
  });
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeDialog();
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) closeDialog();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    saveButton.disabled = true;
    if (errorNode) errorNode.textContent = "";
    const ids = Array.from(
      dialog.querySelectorAll(".manual-category-option input:checked"),
    ).map(input => Number(input.value));
    try {
      const response = await fetch(
        `/api/videos/${button.dataset.videoId}/manual-categories`,
        {
          method: "PUT",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({manual_category_ids: ids}),
        },
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail || "保存人工标签失败");
      }
      window.location.reload();
    } catch (error) {
      if (errorNode) {
        errorNode.textContent = error.message || String(error);
      }
      saveButton.disabled = false;
    }
  });
})();
