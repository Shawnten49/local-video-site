(() => {
  const button = document.getElementById("refresh-video-metadata");
  const status = document.getElementById("video-metadata-refresh-status");
  const confirmDialog = document.getElementById(
    "metadata-refresh-confirm-dialog",
  );
  const cancelConfirm = document.getElementById("cancel-metadata-refresh");
  const acceptConfirm = document.getElementById("confirm-metadata-refresh");
  if (
    !button
    || !status
    || !confirmDialog
    || !cancelConfirm
    || !acceptConfirm
  ) return;

  const originalLabel = button.textContent.trim();
  let confirmationResolve = null;

  function setStatus(message, isError = false) {
    status.textContent = message;
    status.classList.toggle("is-error", isError);
    status.hidden = !message;
  }

  function renderItems(kind, items) {
    const section = document.getElementById(`video-${kind}-section`);
    const count = section?.querySelector(".watch-section-count");
    let list = document.getElementById(`video-${kind}-list`);
    const empty = document.getElementById(`video-${kind}-empty`);
    if (!section || !count) return;

    if (!list) {
      list = document.createElement("div");
      list.id = `video-${kind}-list`;
      list.className = "watch-category-list";
      section.querySelector("h2").after(list);
    }
    empty?.remove();
    list.replaceChildren();
    count.textContent = String(items.length);

    if (!items.length) {
      const message = document.createElement("p");
      message.id = `video-${kind}-empty`;
      message.className = "watch-categories-empty";
      message.textContent = kind === "actors" ? "暂无演员信息" : "暂无分类";
      list.after(message);
      list.hidden = true;
      return;
    }

    list.hidden = false;
    for (const item of items) {
      const link = document.createElement("a");
      link.className = "watch-category-tag";
      if (kind === "actors") link.classList.add("watch-actor-tag");
      link.href = `/${kind === "actors" ? "actors" : "categories"}/${item.id}`;
      link.textContent = item.name;
      list.append(link);
    }
  }

  function settleConfirmation(value) {
    const resolve = confirmationResolve;
    confirmationResolve = null;
    if (confirmDialog.open) confirmDialog.close();
    if (resolve) resolve(value);
  }

  function askConfirmation() {
    return new Promise((resolve) => {
      confirmationResolve = resolve;
      confirmDialog.showModal();
      cancelConfirm.focus();
    });
  }

  cancelConfirm.addEventListener("click", () => settleConfirmation(false));
  acceptConfirm.addEventListener("click", () => settleConfirmation(true));
  confirmDialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    settleConfirmation(false);
  });
  confirmDialog.addEventListener("click", (event) => {
    if (event.target === confirmDialog) settleConfirmation(false);
  });

  button.addEventListener("click", async () => {
    if (button.disabled) return;
    if (!await askConfirmation()) return;
    button.disabled = true;
    button.textContent = "正在重新获取…";
    setStatus("正在访问视频原始页面，请稍候…");
    try {
      const response = await fetch(
        `/api/videos/${button.dataset.videoId}/metadata/refresh`,
        {method: "POST"},
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail || "重新获取演员和分类失败");
      }
      renderItems("actors", payload.actors || []);
      renderItems("categories", payload.categories || []);
      setStatus(
        `重新获取成功：演员 ${payload.actor_count}，分类 ${payload.category_count}`,
      );
    } catch (error) {
      setStatus(error.message || String(error), true);
    } finally {
      button.disabled = false;
      button.textContent = originalLabel;
    }
  });
})();
