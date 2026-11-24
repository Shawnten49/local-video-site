(() => {
  document.addEventListener("change", (event) => {
    const selector = event.target.closest("[data-auto-submit]");
    if (selector?.form) selector.form.submit();
  });
})();
