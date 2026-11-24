(() => {
  const trigger = document.getElementById("delete-video-button");
  const dialog = document.getElementById("delete-video-dialog");
  const confirmStep = document.getElementById("delete-confirm-step");
  const form = document.getElementById("delete-confirm-form");
  const password = document.getElementById("delete-password");
  const submit = document.getElementById("confirm-delete-button");
  const error = document.getElementById("delete-error");
  const continueButton = document.getElementById("continue-delete");
  const cancelStep = document.getElementById("cancel-delete-step");
  const cancelPassword = document.getElementById("cancel-delete-password");
  if (!trigger || !dialog || !form || !password || !submit) return;

  let busy = false;

  function resetDialog() {
    confirmStep.hidden = false;
    form.hidden = true;
    password.value = "";
    submit.disabled = true;
    submit.textContent = "确认删除";
    error.textContent = "";
    continueButton.disabled = false;
    cancelStep.disabled = false;
    cancelPassword.disabled = false;
    busy = false;
  }

  function openDialog() {
    resetDialog();
    dialog.showModal();
  }

  function closeDialog() {
    if (busy) return;
    if (dialog.open) dialog.close();
  }

  trigger.addEventListener("click", openDialog);
  cancelStep.addEventListener("click", closeDialog);
  cancelPassword.addEventListener("click", closeDialog);
  continueButton.addEventListener("click", () => {
    confirmStep.hidden = true;
    form.hidden = false;
    password.focus();
  });

  password.addEventListener("input", () => {
    submit.disabled = !password.value;
    error.textContent = "";
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy || !password.value) return;
    busy = true;
    submit.disabled = true;
    submit.textContent = "正在创建任务…";
    continueButton.disabled = true;
    cancelStep.disabled = true;
    cancelPassword.disabled = true;
    error.textContent = "";
    try {
      const response = await fetch(
        `/api/videos/${trigger.dataset.videoId}`,
        {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            password: password.value,
            return_to: trigger.dataset.returnTo || "/",
          }),
        },
      );
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        const detail = payload.detail;
        throw new Error(
          detail?.message || detail || `HTTP ${response.status}`,
        );
      }
      const result = await response.json();
      window.location.href = result.redirect_url || "/";
    } catch (requestError) {
      busy = false;
      submit.disabled = !password.value;
      submit.textContent = "确认删除";
      cancelPassword.disabled = false;
      error.textContent = requestError.message;
    }
  });

  dialog.addEventListener("close", resetDialog);
})();
