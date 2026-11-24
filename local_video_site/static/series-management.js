(() => {
  function errorMessage(payload, status) {
    const detail = payload?.detail;
    return detail?.message || detail || `HTTP ${status}`;
  }

  const deleteDialog = document.getElementById("collection-delete-dialog");
  const deleteForm = document.getElementById("collection-delete-form");
  const deleteTitle = document.getElementById("collection-delete-title");
  const deleteWarning = document.getElementById("collection-delete-warning");
  const deleteTarget = document.getElementById("collection-delete-target");
  const deleteScope = document.getElementById("collection-delete-scope");
  const deleteCode = document.getElementById("collection-delete-code");
  const deleteError = document.getElementById("collection-delete-error");
  const deleteSubmit = document.getElementById("collection-delete-submit");
  let selectedDelete = null;

  function resetDelete() {
    selectedDelete = null;
    deleteCode.value = "";
    deleteSubmit.disabled = true;
    deleteSubmit.textContent = "确认创建删除任务";
    deleteError.textContent = "";
  }

  document.addEventListener("click", (event) => {
    const deleteTrigger = event.target.closest("[data-delete-scope]");
    if (deleteTrigger) {
      selectedDelete = {
        scope_type: deleteTrigger.dataset.deleteScope,
        target_id: Number(deleteTrigger.dataset.deleteTarget),
        name: deleteTrigger.dataset.deleteName || "",
        expected_updated_at: deleteTrigger.dataset.deleteUpdatedAt || "",
        summary: deleteTrigger.dataset.deleteSummary || "",
      };
      const scopeLabel = selectedDelete.scope_type === "series"
        ? "视频集及其全部视频季、剧集和视频"
        : "视频季及其全部视频";
      deleteTitle.textContent =
        `确认删除${selectedDelete.scope_type === "series" ? "整个视频集" : "整个视频季"}？`;
      deleteTarget.textContent = selectedDelete.name;
      deleteScope.textContent = scopeLabel;
      deleteWarning.textContent =
        `将删除 ${selectedDelete.summary}。视频文件会物理删除，记录会软删除；` +
        "任务逐个执行，后续文件失败时已经删除的文件无法自动恢复。";
      deleteCode.value = "";
      deleteSubmit.disabled = true;
      deleteError.textContent = "";
      deleteDialog.showModal();
      deleteCode.focus();
      return;
    }
    if (event.target.closest("[data-cancel-collection-delete]")) {
      deleteDialog.close();
    }
  });

  deleteCode.addEventListener("input", () => {
    deleteSubmit.disabled = !deleteCode.value || !selectedDelete;
    deleteError.textContent = "";
  });

  deleteForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!selectedDelete || !deleteCode.value) return;
    deleteSubmit.disabled = true;
    deleteSubmit.textContent = "正在创建任务…";
    deleteError.textContent = "";
    try {
      const response = await fetch("/api/manage/delete-jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scope_type: selectedDelete.scope_type,
          target_id: selectedDelete.target_id,
          verification_code: deleteCode.value,
          expected_updated_at: selectedDelete.expected_updated_at,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(errorMessage(payload, response.status));
      }
      window.location.href = "/manage/delete-tasks";
    } catch (requestError) {
      deleteSubmit.disabled = false;
      deleteSubmit.textContent = "确认创建删除任务";
      deleteError.textContent =
        requestError.message || String(requestError);
    }
  });

  deleteDialog.addEventListener("close", resetDelete);

  const editDialog = document.getElementById("series-edit-dialog");
  const editForm = document.getElementById("series-edit-form");
  const editTitle = document.getElementById("series-edit-title");
  const editSourceName = document.getElementById("series-edit-source-name");
  const editName = document.getElementById("series-edit-name");
  const editError = document.getElementById("series-edit-error");
  const editSubmit = document.getElementById("series-edit-submit");
  let selectedEdit = null;

  function editUrl(selection) {
    if (selection.scope_type === "season") {
      return `/api/manage/series/${selection.series_id}/seasons/${selection.target_id}`;
    }
    return `/api/manage/series/${selection.target_id}`;
  }

  document.addEventListener("click", (event) => {
    const editTrigger = event.target.closest("[data-edit-scope]");
    if (editTrigger) {
      selectedEdit = {
        scope_type: editTrigger.dataset.editScope,
        target_id: Number(editTrigger.dataset.editTarget),
        series_id: Number(editTrigger.dataset.editSeriesId || 0),
        source_name: editTrigger.dataset.editName || "",
        display_name: editTrigger.dataset.editDisplayName || "",
        expected_updated_at: editTrigger.dataset.editUpdatedAt || "",
      };
      const isSeason = selectedEdit.scope_type === "season";
      editTitle.textContent = isSeason ? "修改视频季展示名称" : "修改视频集展示名称";
      editSourceName.textContent = selectedEdit.source_name;
      editName.value = selectedEdit.display_name;
      editName.maxLength = isSeason ? 80 : 120;
      editError.textContent = "";
      editSubmit.disabled = false;
      editSubmit.textContent = "保存修改";
      editDialog.showModal();
      editName.focus();
      editName.select();
      return;
    }
    if (event.target.closest("[data-cancel-series-edit]")) {
      editDialog.close();
    }
  });

  editName.addEventListener("input", () => {
    editError.textContent = "";
    editSubmit.disabled = !editName.value.trim();
  });

  editForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!selectedEdit) return;
    const displayName = editName.value.trim();
    if (!displayName) {
      editError.textContent = "展示名称不能为空";
      return;
    }
    editSubmit.disabled = true;
    editSubmit.textContent = "正在保存…";
    editError.textContent = "";
    try {
      const response = await fetch(editUrl(selectedEdit), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          display_name: displayName,
          expected_updated_at: selectedEdit.expected_updated_at,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(errorMessage(payload, response.status));
      }
      window.location.reload();
    } catch (requestError) {
      editSubmit.disabled = false;
      editSubmit.textContent = "保存修改";
      editError.textContent =
        requestError.message || String(requestError);
    }
  });

  editDialog.addEventListener("close", () => {
    selectedEdit = null;
    editError.textContent = "";
  });
})();
