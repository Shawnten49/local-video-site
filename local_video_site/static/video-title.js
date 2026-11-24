(() => {
  const section = document.getElementById("video-title-section");
  const title = document.getElementById("video-title-text");
  const toggle = document.getElementById("toggle-video-title");
  if (!section || !title || !toggle) return;

  const syncToggle = () => {
    const expanded = section.classList.contains("is-title-expanded");
    if (expanded) {
      toggle.hidden = false;
      return;
    }
    toggle.hidden = title.scrollHeight <= title.clientHeight + 1;
  };

  toggle.addEventListener("click", () => {
    const expanded = section.classList.toggle("is-title-expanded");
    toggle.setAttribute("aria-expanded", String(expanded));
    toggle.textContent = expanded ? "收起标题" : "展开完整标题";
    syncToggle();
  });

  const observer = new MutationObserver(syncToggle);
  observer.observe(title, {
    childList: true,
    characterData: true,
    subtree: true,
  });
  if ("ResizeObserver" in window) {
    new ResizeObserver(syncToggle).observe(title);
  } else {
    window.addEventListener("resize", syncToggle);
  }
  syncToggle();
})();
