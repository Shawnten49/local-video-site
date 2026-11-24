(() => {
  if (!window.matchMedia("(hover: hover) and (pointer: fine)").matches) {
    return;
  }

  const cards = Array.from(
    document.querySelectorAll(".thumb[data-preview-url]"),
  );
  if (!cards.length) return;

  let active = null;
  let intentTimer = null;
  const releaseTimers = new WeakMap();

  function clearReleaseTimer(video) {
    const timer = releaseTimers.get(video);
    if (timer) window.clearTimeout(timer);
    releaseTimers.delete(video);
  }

  function clearWatchdog(item) {
    if (item?.watchdog) {
      window.clearInterval(item.watchdog);
      item.watchdog = null;
    }
  }

  function clearFrameRequest(item) {
    if (!item) return;
    if (item.frameRequest != null && item.video.cancelVideoFrameCallback) {
      item.video.cancelVideoFrameCallback(item.frameRequest);
    }
    item.frameRequest = null;
    if (item.playingHandler) {
      item.video.removeEventListener("playing", item.playingHandler);
      item.playingHandler = null;
    }
  }

  function stopPreview(item, releaseSource = true) {
    if (!item) return;
    window.clearTimeout(item.playTimer);
    clearWatchdog(item);
    clearFrameRequest(item);
    const video = item.video;
    clearReleaseTimer(video);
    video.pause();
    video.currentTime = 0;
    item.card.classList.remove("is-previewing");
    if (releaseSource) {
      const releaseTimer = window.setTimeout(() => {
        releaseTimers.delete(video);
        if (active?.video !== video) {
          video.removeAttribute("src");
          video.load();
        }
      }, 2000);
      releaseTimers.set(video, releaseTimer);
    }
    if (active === item) active = null;
  }

  function startPreview(card) {
    if (
      document.hidden
      || card.dataset.previewVisible === "false"
      || !card.dataset.previewUrl
    ) {
      return;
    }
    const video = card.querySelector(".hover-preview-video");
    if (!video) return;
    if (active && active.card !== card) {
      stopPreview(active, true);
    }
    const item = active?.card === card
      ? active
      : {
          card,
          video,
          playTimer: null,
          watchdog: null,
          frameRequest: null,
          playingHandler: null,
          lastTime: 0,
          stalledChecks: 0,
        };
    active = item;
    clearReleaseTimer(video);
    if (!video.getAttribute("src")) {
      video.src = card.dataset.previewUrl;
      video.load();
    }
    window.clearTimeout(item.playTimer);
    item.playTimer = window.setTimeout(() => {
      if (active === item && video.readyState < 2) {
        stopPreview(item, true);
      }
    }, 3500);
    const reveal = () => {
      window.clearTimeout(item.playTimer);
      if (active !== item) return;
      clearFrameRequest(item);
      card.classList.add("is-previewing");
      clearWatchdog(item);
      item.lastTime = video.currentTime;
      item.stalledChecks = 0;
      item.watchdog = window.setInterval(() => {
        if (active !== item) {
          clearWatchdog(item);
          return;
        }
        const time = video.currentTime;
        const advanced = Math.abs(time - item.lastTime) > 0.03;
        item.lastTime = time;
        if (advanced && video.readyState >= 2 && !video.paused) {
          item.stalledChecks = 0;
          return;
        }
        item.stalledChecks += 1;
        if (item.stalledChecks >= 3) {
          stopPreview(item, true);
        }
      }, 1000);
    };
    clearFrameRequest(item);
    if (video.requestVideoFrameCallback) {
      item.frameRequest = video.requestVideoFrameCallback(reveal);
    } else {
      item.playingHandler = reveal;
      video.addEventListener("playing", item.playingHandler, {once: true});
    }
    video.play().catch(() => stopPreview(item, true));
  }

  for (const card of cards) {
    const video = card.querySelector(".hover-preview-video");
    if (!video) continue;
    video.muted = true;
    video.loop = true;
    video.playsInline = true;
    video.addEventListener("error", () => {
      card.removeAttribute("data-preview-url");
      if (active?.card === card) stopPreview(active, true);
    });
    video.addEventListener("pause", () => {
      if (active?.card === card && !document.hidden) {
        stopPreview(active, true);
      }
    });
    for (const eventName of ["waiting", "stalled"]) {
      video.addEventListener(eventName, () => {
        if (
          active?.card === card
          && card.classList.contains("is-previewing")
        ) {
          stopPreview(active, true);
        }
      });
    }
    video.addEventListener("emptied", () => {
      if (
        active?.card === card
        && card.classList.contains("is-previewing")
      ) {
        stopPreview(active, true);
      }
    });

    card.addEventListener("mouseenter", () => {
      window.clearTimeout(intentTimer);
      intentTimer = window.setTimeout(() => startPreview(card), 200);
    });
    card.addEventListener("mouseleave", () => {
      window.clearTimeout(intentTimer);
      if (active?.card === card) stopPreview(active, true);
    });

    const coverButton = card
      .closest(".video-card-shell")
      ?.querySelector(".change-cover-button");
    if (coverButton) {
      const suppressPreview = () => {
        window.clearTimeout(intentTimer);
        if (active?.card === card) stopPreview(active, true);
      };
      coverButton.addEventListener("pointerenter", suppressPreview);
      coverButton.addEventListener("focus", suppressPreview);
      coverButton.addEventListener("click", suppressPreview);
    }

    if ("IntersectionObserver" in window) {
      const observer = new IntersectionObserver((entries) => {
        for (const entry of entries) {
          card.dataset.previewVisible = entry.isIntersecting ? "true" : "false";
          if (!entry.isIntersecting && active?.card === card) {
            stopPreview(active, true);
          }
        }
      }, {rootMargin: "120px"});
      observer.observe(card);
    }
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      window.clearTimeout(intentTimer);
      if (active) stopPreview(active, true);
    }
  });
  window.addEventListener("pagehide", () => {
    if (active) stopPreview(active, true);
  });
})();
