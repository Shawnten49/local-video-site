(() => {
  async function readError(response, fallback) {
    const payload = await response.json().catch(() => ({}));
    return payload.detail || fallback;
  }

  document.addEventListener("click", async (event) => {
    const restore = event.target.closest("[data-restore-trim-backup]");
    if (restore) {
      const title = restore.dataset.trimVideoTitle || "该视频";
      const sourceDuration = Number(restore.dataset.trimSourceDuration || 0);
      const outputDuration = Number(restore.dataset.trimOutputDuration || 0);
      if (!window.confirm(
        `确认恢复原始视频？\n\n${title}\n`
        + `原始时长：${sourceDuration.toFixed(3)} 秒\n`
        + `当前时长：${outputDuration.toFixed(3)} 秒\n\n`
        + "恢复后会覆盖当前裁剪版本，但安全备份仍会保留。",
      )) return;
      restore.disabled = true;
      restore.textContent = "恢复中…";
      try {
        const response = await fetch(
          `/api/video-trim-jobs/${encodeURIComponent(
            restore.dataset.restoreTrimBackup,
          )}/restore`,
          {method: "POST"},
        );
        if (!response.ok) {
          throw new Error(await readError(response, "恢复原始视频失败"));
        }
        window.location.reload();
      } catch (error) {
        window.alert(error.message || String(error));
        restore.disabled = false;
        restore.textContent = "恢复原始视频";
      }
      return;
    }

    const remove = event.target.closest("[data-delete-trim-backup]");
    if (!remove) return;
    const title = remove.dataset.trimVideoTitle || "该视频";
    const confirmation = window.prompt(
      `将永久删除 ${title} 在任务 #${remove.dataset.deleteTrimBackup} `
      + "中的安全备份。\n删除后无法恢复此裁剪前的原始视频。\n\n"
      + "请输入“删除备份”确认：",
    );
    if (confirmation !== "删除备份") return;
    remove.disabled = true;
    remove.textContent = "删除中…";
    try {
      const response = await fetch(
        `/api/video-trim-jobs/${encodeURIComponent(
          remove.dataset.deleteTrimBackup,
        )}/backup/delete`,
        {method: "POST"},
      );
      if (!response.ok) {
        throw new Error(await readError(response, "删除安全备份失败"));
      }
      window.location.reload();
    } catch (error) {
      window.alert(error.message || String(error));
      remove.disabled = false;
      remove.textContent = "删除安全备份";
    }
  });
})();
