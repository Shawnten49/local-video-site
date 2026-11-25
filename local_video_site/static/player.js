import {
  createPlaybackSession,
} from "./playback-session.js?v=20261125-subtitles";

const nativePlayer = document.getElementById("player");
if (nativePlayer) initialize();

async function initialize() {
  const storageKey = `local-video-position:${location.pathname}`;
  const saved = Number(localStorage.getItem(storageKey) || 0);
  const warning = document.getElementById("playback-warning");
  const warningTitle = document.getElementById("playback-warning-title");
  const warningText = document.getElementById("playback-warning-text");
  const warningAction = document.getElementById("playback-warning-action");
  const retryButton = document.getElementById("playback-retry");
  const standardizeButton = document.getElementById(
    "playback-standardize",
  );
  const standardizeDialog = document.getElementById(
    "standardize-video-dialog",
  );
  const standardizeForm = document.getElementById(
    "standardize-video-form",
  );
  const standardizeStrategy = document.getElementById(
    "standardize-strategy",
  );
  const standardizeSource = document.getElementById(
    "standardize-source",
  );
  const standardizeTarget = document.getElementById(
    "standardize-target",
  );
  const standardizeWarning = document.getElementById(
    "standardize-warning",
  );
  const standardizeLossyConfirm = document.getElementById(
    "standardize-lossy-confirm",
  );
  const standardizeLossyCheckbox = document.getElementById(
    "standardize-confirm-lossy",
  );
  const standardizeError = document.getElementById(
    "standardize-error",
  );
  const standardizeConfirmButton = document.getElementById(
    "confirm-standardize-video",
  );
  const externalPlaybackUrl =
    nativePlayer.dataset.externalPlaybackUrl || "";
  const externalPlaylistUrl =
    nativePlayer.dataset.externalPlaylistUrl || "";
  const externalPlaybackStatus = document.getElementById(
    "external-playback-status",
  );
  const externalOpenButtons = document.querySelectorAll(
    "[data-open-vlc-playback]",
  );
  const externalCopyButtons = document.querySelectorAll(
    "[data-copy-external-playback]",
  );
  const externalOnly = nativePlayer.dataset.externalOnly === "1";
  const startPlayback = nativePlayer.dataset.startPlayback === "1";
  const currentVideoId = Number(nativePlayer.dataset.videoId || 0);
  const returnTo = nativePlayer.dataset.returnTo || "";
  const decisionDialog = document.getElementById(
    "playback-decision-dialog",
  );
  const decisionTitle = document.getElementById(
    "playback-decision-title",
  );
  const decisionMessage = document.getElementById(
    "playback-decision-message",
  );
  const decisionRetry = document.getElementById(
    "retry-playback-decision",
  );
  const decisionCopy = document.getElementById(
    "copy-playback-decision-url",
  );
  const stateUrl = nativePlayer.dataset.stateUrl;
  const prepareUrl = nativePlayer.dataset.prepareUrl;
  const standardizePreviewUrl =
    nativePlayer.dataset.standardizePreviewUrl || "";
  const standardizeUrl = nativePlayer.dataset.standardizeUrl || "";
  let preparing = false;
  let session = null;
  let standardizePreview = null;
  let standardizePollTimer = null;
  let cachedExternalPlayback = null;
  let decisionVideoId = null;

  const supportsExternalPlayback = (() => {
    const extension = String(
      nativePlayer.dataset.videoExtension || "",
    ).toLowerCase();
    const videoCodec = String(
      nativePlayer.dataset.videoCodec || "",
    ).toLowerCase();
    const audioCodec = String(
      nativePlayer.dataset.audioCodec || "",
    ).toLowerCase();
    return (
      extension === ".mkv"
      || ["hevc", "h265"].includes(videoCodec)
      || ["truehd", "ac3", "eac3"].includes(audioCodec)
    );
  })();

  function showExternalStatus(message, error = false) {
    if (!externalPlaybackStatus) return;
    externalPlaybackStatus.hidden = !message;
    externalPlaybackStatus.textContent = message || "";
    externalPlaybackStatus.classList.toggle("is-error", error);
  }

  async function fetchExternalPlayback(videoId = currentVideoId) {
    if (!videoId) {
      throw new Error("当前视频不支持外部播放");
    }
    const response = await fetch(`/api/external-playback/${videoId}`, {
      cache: "no-store",
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(
        payload.detail?.message || payload.detail || `HTTP ${response.status}`,
      );
    }
    cachedExternalPlayback = payload;
    return payload;
  }

  function absoluteExternalUrl(info) {
    return new URL(info.stream_url, window.location.href).href;
  }

  async function copyText(value) {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return;
    }
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
  }

  async function copyExternalPlaybackUrl(videoId = currentVideoId) {
    try {
      const info = await fetchExternalPlayback(videoId);
      await copyText(absoluteExternalUrl(info));
      showExternalStatus("原始视频地址已复制。");
    } catch (error) {
      showExternalStatus(
        error.message || "无法复制原始视频地址",
        true,
      );
    }
  }

  async function requestExternalOpen(videoId) {
    const response = await fetch(
      `/api/external-playback/${videoId}/open`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: "{}",
        cache: "no-store",
      },
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(
        payload.detail?.message
          || payload.detail
          || `HTTP ${response.status}`,
      );
    }
    return payload;
  }

  async function openExternalPlayback(videoId = currentVideoId) {
    try {
      showExternalStatus("正在请求本机 VLC 打开原片…");
      const payload = await requestExternalOpen(videoId);
      showExternalStatus(payload.message || "已请求 VLC 打开原始视频。");
      return true;
    } catch (error) {
      const message = error.message || "无法启动 VLC";
      showExternalStatus(
        `${message} 可以改用“复制原始地址”或下载播放列表。`,
        true,
      );
      return false;
    }
  }

  function showPlaybackDecision({
    title = "无法播放",
    message = "当前视频暂时无法播放",
    retryable = false,
    videoId = null,
    canCopy = false,
  }) {
    decisionVideoId = videoId;
    if (decisionTitle) decisionTitle.textContent = title;
    if (decisionMessage) decisionMessage.textContent = message;
    if (decisionRetry) decisionRetry.hidden = !retryable;
    if (decisionCopy) decisionCopy.hidden = !canCopy;
    decisionDialog?.showModal();
  }

  function markCurrentEpisode(card) {
    document.querySelectorAll(
      ".season-episode-button.is-current",
    ).forEach((item) => {
      if (item !== card) item.classList.remove("is-current");
    });
    card?.classList.add("is-current");
  }

  async function resolveEpisodePlayback(card) {
    const videoId = Number(card.dataset.resolveVideo || 0);
    if (!videoId || card.dataset.decisionPending === "1") return;
    card.dataset.decisionPending = "1";
    card.classList.add("is-checking");
    try {
      const response = await fetch(
        `/api/playback/resolve/${videoId}?return_to=${
          encodeURIComponent(returnTo)
        }`,
        {
          cache: "no-store",
        },
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          payload.detail?.message
            || payload.detail
            || `HTTP ${response.status}`,
        );
      }
      if (payload.action === "native_playback" && payload.native_url) {
        window.location.assign(payload.native_url);
        return;
      }
      if (payload.action === "external_vlc") {
        markCurrentEpisode(card);
        const opened = await openExternalPlayback(videoId);
        if (!opened) {
          showPlaybackDecision({
            title: "无法打开 VLC",
            message: (
              "VLC 没有接受当前视频地址。可以重试，"
              + "或复制地址后在 VLC 中手动打开。"
            ),
            retryable: true,
            videoId,
            canCopy: payload.can_open_vlc,
          });
        }
        return;
      }
      showPlaybackDecision({
        title: payload.title || "无法播放",
        message: payload.message || "当前视频暂时无法播放",
        retryable: Boolean(payload.retryable),
        videoId,
        canCopy: Boolean(payload.can_open_vlc),
      });
    } catch (error) {
      showPlaybackDecision({
        title: "无法检查视频状态",
        message: error.message || "请检查服务器连接后重试",
        retryable: true,
        videoId,
        canCopy: false,
      });
    } finally {
      card.dataset.decisionPending = "0";
      card.classList.remove("is-checking");
    }
  }

  async function fetchStandardizationPreview() {
    if (!standardizePreviewUrl) return null;
    const response = await fetch(standardizePreviewUrl, {
      cache: "no-store",
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(
        payload.detail?.message || payload.detail || `HTTP ${response.status}`,
      );
    }
    return payload;
  }

  async function refreshStandardizeAction() {
    if (!standardizeButton) return;
    standardizeButton.hidden = true;
    try {
      const preview = await fetchStandardizationPreview();
      standardizePreview = preview;
      if (!preview) return;
      if (preview.active_job?.id) {
        standardizeButton.hidden = false;
        standardizeButton.textContent = "查看转码进度";
        startStandardizePolling(preview.active_job.id);
        return;
      }
      if (!preview.eligible) return;
      standardizeButton.hidden = false;
      standardizeButton.textContent = "转标准码";
    } catch (_) {
      standardizeButton.hidden = true;
    }
  }

  function showWarning(state, refreshStandardize = true) {
    if (!warning) return;
    warning.hidden = false;
    warning.dataset.state = state.state || "playback_failed";
    warning.className =
      `playback-warning playback-state-${state.state || "playback_failed"}`;
    if (warningTitle) {
      warningTitle.textContent = state.title || "视频暂时无法播放";
    }
    if (warningText) warningText.textContent = state.message || "";
    if (warningAction) warningAction.textContent = state.action_text || "";
    if (retryButton) retryButton.hidden = !state.retryable;
    document.querySelectorAll("#playback-warning [data-open-vlc-playback], "
      + "#playback-warning [data-copy-external-playback]").forEach((button) => {
      button.hidden = !supportsExternalPlayback;
    });
    if (
      refreshStandardize
      &&
      standardizeButton
      && [
        "playback_failed",
        "movi_failed",
        "unsupported_codec",
      ].includes(state.state || "")
    ) {
      refreshStandardizeAction();
    } else if (standardizeButton) {
      standardizeButton.hidden = true;
    }
  }

  function hideWarning() {
    if (warning) warning.hidden = true;
    if (standardizeButton) standardizeButton.hidden = true;
  }

  externalOpenButtons.forEach((button) => {
    button.addEventListener("click", () => openExternalPlayback(currentVideoId));
  });
  externalCopyButtons.forEach((button) => {
    button.addEventListener("click", () => copyExternalPlaybackUrl(currentVideoId));
  });
  const externalPlaylistLink = document.getElementById(
    "download-external-playlist",
  );
  if (externalPlaylistLink && externalPlaylistUrl) {
    externalPlaylistLink.href = externalPlaylistUrl;
  }
  document.addEventListener("click", (event) => {
    const episode = event.target.closest("[data-resolve-video]");
    if (!episode) return;
    event.preventDefault();
    resolveEpisodePlayback(episode);
  });
  document.querySelectorAll("[data-close-playback-decision]").forEach(
    (button) => {
      button.addEventListener("click", () => decisionDialog?.close());
    },
  );
  decisionRetry?.addEventListener("click", () => {
    const card = document.querySelector(
      `[data-resolve-video="${decisionVideoId}"]`,
    );
    decisionDialog?.close();
    if (card) resolveEpisodePlayback(card);
  });
  decisionCopy?.addEventListener("click", () => {
    if (decisionVideoId) copyExternalPlaybackUrl(decisionVideoId);
  });
  if (externalOnly) {
    if (!supportsExternalPlayback) {
      showExternalStatus("当前视频需要使用外部播放器打开。", true);
      return;
    }
    if (!["127.0.0.1", "localhost", "::1"].includes(location.hostname)) {
      showExternalStatus(
        "当前通过局域网访问，请复制视频地址后在 VLC 中打开，"
        + "或下载播放列表。",
      );
    }
    return;
  }
  if (!startPlayback) {
    return;
  }

  function showCodecFailure(detail = "") {
    showWarning({
      state: "playback_failed",
      title: "当前浏览器无法播放此视频",
      message: detail
        ? `视频文件在线，但浏览器播放失败：${detail}`
        : "视频文件在线，但当前浏览器无法解码该视频，或兼容播放器加载失败。",
      action_text: "可以重新检测，或尝试转成标准 MP4。",
      retryable: true,
    });
  }

  function standardizeDescription(preview) {
    if (!preview) return "";
    if (preview.strategy === "remux") {
      return "无损重封装为 H.264 + AAC 标准 MP4";
    }
    return "重新编码为 H.264 + AAC 标准 MP4";
  }

  function updateStandardizeConfirmState() {
    if (!standardizeConfirmButton) return;
    const lossyRequired = Boolean(
      standardizePreview?.lossy
      && !standardizeLossyCheckbox?.checked,
    );
    standardizeConfirmButton.disabled = lossyRequired;
    standardizeConfirmButton.textContent = standardizePreview?.lossy
      ? "确认有损转标准码"
      : "确认无损转标准码";
  }

  function openStandardizeDialog(preview) {
    if (!standardizeDialog || !preview) return;
    standardizePreview = preview;
    if (standardizeStrategy) {
      standardizeStrategy.textContent = standardizeDescription(preview);
    }
    if (standardizeSource) {
      const source = preview.source || {};
      standardizeSource.textContent =
        `源：${source.container || "未知容器"} · `
        + `${source.video_codec || "未知视频"} / `
        + `${source.audio_codec || "无音频"}`;
    }
    if (standardizeTarget) {
      const target = preview.target || {};
      standardizeTarget.textContent =
        `输出：${target.container || "mp4"} · `
        + `${target.video_codec || "h264"} / `
        + `${target.audio_codec || "无音频"}`;
    }
    if (standardizeWarning) {
      standardizeWarning.hidden = !preview.lossy;
    }
    if (standardizeLossyConfirm) {
      standardizeLossyConfirm.hidden = !preview.lossy;
    }
    if (standardizeLossyCheckbox) {
      standardizeLossyCheckbox.checked = false;
    }
    if (standardizeError) standardizeError.textContent = "";
    updateStandardizeConfirmState();
    standardizeDialog.showModal();
  }

  function stopStandardizePolling() {
    if (standardizePollTimer) {
      window.clearTimeout(standardizePollTimer);
      standardizePollTimer = null;
    }
  }

  async function startStandardizePolling(jobId) {
    stopStandardizePolling();
    if (!jobId) return;
    const poll = async () => {
      try {
        const response = await fetch(
          `/api/manage/format-jobs/live?ids=${encodeURIComponent(jobId)}`,
          { cache: "no-store" },
        );
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = await response.json();
        const job = (payload.items || [])[0];
        if (!job) throw new Error("未找到转码任务");
        if (job.status === "success") {
          if (standardizeButton) standardizeButton.hidden = true;
          hideWarning();
          await refreshPlayback(true);
          return;
        }
        if (["failed", "cancelled"].includes(job.status)) {
          showWarning({
            state: "playback_failed",
            title: "转标准码失败",
            message: job.error_detail || "任务未能完成。",
            action_text: "可以重新检测，或再次尝试转标准码。",
            retryable: true,
          });
          return;
        }
        showWarning({
          state: "cache_required",
          title: "正在转标准码",
          message:
            `${job.stage_label || job.stage || "处理中"} · `
            + `${Number(job.progress_percent || 0).toFixed(1)}%`,
          action_text: "转换完成后会自动重新检测并播放。",
          retryable: false,
        }, false);
        standardizePollTimer = window.setTimeout(poll, 2000);
      } catch (error) {
        showWarning({
          state: "playback_failed",
          title: "无法读取转码进度",
          message: error.message || String(error),
          action_text: "请稍后重新检测。",
          retryable: true,
        });
      }
    };
    standardizePollTimer = window.setTimeout(poll, 600);
  }

  async function fetchPlaybackState() {
    if (!stateUrl) return null;
    const response = await fetch(stateUrl, { cache: "no-store" });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }
    return response.json();
  }

  function loadPlaybackUrl(url) {
    if (!url || !session) return;
    session.load(url);
    hideWarning();
  }

  async function preparePlayback(preparationState = null) {
    if (!prepareUrl || preparing) return;
    preparing = true;
    showWarning(
      preparationState || {
        state: "cache_required",
        title: "正在准备播放缓存",
        message: "视频文件在线，首次打开可能需要一些时间。",
        action_text: "",
        retryable: false,
      },
    );
    try {
      const response = await fetch(prepareUrl, { method: "POST" });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || `HTTP ${response.status}`);
      }
      const result = await response.json();
      loadPlaybackUrl(
        result.playback_url || nativePlayer.dataset.playbackUrl || "",
      );
    } catch (error) {
      let state = null;
      try {
        state = await fetchPlaybackState();
      } catch (_) {
        state = null;
      }
      if (
        state
        && !state.playable
        && state.state !== "cache_required"
        && state.state !== "transcode_required"
      ) {
        showWarning(state);
      } else {
        showCodecFailure(`播放缓存准备失败：${error.message}`);
      }
    } finally {
      preparing = false;
    }
  }

  async function refreshPlayback(autoPrepare = true) {
    try {
      const state = await fetchPlaybackState();
      if (!state) return null;
      if (state.playable) {
        if (state.engine === "movi" && session?.element === nativePlayer) {
          showWarning({
            state: "movi_required",
            title: "需要重新加载兼容播放器",
            message: "当前页面尚未启用兼容播放器，请重新加载页面。",
            action_text: "",
            retryable: true,
          });
          return state;
        }
        loadPlaybackUrl(state.playback_url);
        return state;
      }
      if (
        ["cache_required", "transcode_required"].includes(state.state)
        && autoPrepare
        && prepareUrl
      ) {
        showWarning(state);
        await preparePlayback(state);
        return state;
      }
      showWarning(state);
      return state;
    } catch (error) {
      showCodecFailure(error.message);
      return null;
    }
  }

  function restorePosition() {
    if (!Number.isFinite(saved) || saved <= 3) return;
    const maximum = session.duration;
    if (maximum && saved < maximum - 10) {
      session.element.currentTime = saved;
    }
  }

  function bindPlayerEvents() {
    session.addEventListener("error", async () => {
      const state = await refreshPlayback(false);
      if (state?.playable) showCodecFailure();
    });
    session.addEventListener("loadedmetadata", restorePosition, { once: true });
    if (session.element.tagName.toLowerCase() === "movi-player") {
      session.addEventListener("loadeddata", restorePosition, { once: true });
    }

    let lastSaved = 0;
    session.addEventListener("timeupdate", () => {
      const now = Date.now();
      if (now - lastSaved > 3000) {
        localStorage.setItem(
          storageKey,
          String(session.currentTime),
        );
        lastSaved = now;
      }
    });
    session.addEventListener(
      "ended",
      () => localStorage.removeItem(storageKey),
    );
  }

  function seek(delta) {
    session.seek(session.currentTime + delta);
  }

  retryButton?.addEventListener("click", () => {
    if (!session) {
      window.location.reload();
      return;
    }
    refreshPlayback(true);
  });

  standardizeButton?.addEventListener("click", async () => {
    if (standardizeButton.disabled) return;
    standardizeButton.disabled = true;
    try {
      const preview = await fetchStandardizationPreview();
      standardizePreview = preview;
      if (preview?.active_job?.id) {
        startStandardizePolling(preview.active_job.id);
        return;
      }
      if (!preview?.eligible) {
        throw new Error(preview?.reason || "当前视频不能转标准码");
      }
      openStandardizeDialog(preview);
    } catch (error) {
      showWarning({
        state: "playback_failed",
        title: "当前浏览器无法播放此视频",
        message: error.message || String(error),
        action_text: "可以重新检测，或检查视频文件状态。",
        retryable: true,
      });
    } finally {
      standardizeButton.disabled = false;
    }
  });

  standardizeLossyCheckbox?.addEventListener(
    "change",
    updateStandardizeConfirmState,
  );

  standardizeDialog?.querySelectorAll("[data-close-standardize]").forEach(
    (node) => {
      node.addEventListener("click", () => standardizeDialog.close());
    },
  );

  standardizeForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!standardizePreview || !standardizeUrl) return;
    if (standardizePreview.lossy && !standardizeLossyCheckbox?.checked) {
      if (standardizeError) {
        standardizeError.textContent = "请先确认有损转换。";
      }
      return;
    }
    if (standardizeConfirmButton) {
      standardizeConfirmButton.disabled = true;
      standardizeConfirmButton.textContent = "正在创建任务…";
    }
    if (standardizeError) standardizeError.textContent = "";
    try {
      const response = await fetch(standardizeUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          confirmed: true,
          confirm_lossy: Boolean(standardizePreview.lossy),
          expected_path: nativePlayer.dataset.videoPath || "",
          expected_mtime_ns: nativePlayer.dataset.videoMtimeNs || "",
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          payload.detail?.message
          || payload.detail
          || `HTTP ${response.status}`,
        );
      }
      standardizeDialog?.close();
      if (payload.id || payload.existing_job_id) {
        startStandardizePolling(payload.id || payload.existing_job_id);
      } else {
        const preview = await fetchStandardizationPreview();
        if (preview?.active_job?.id) {
          startStandardizePolling(preview.active_job.id);
        }
      }
    } catch (error) {
      if (standardizeError) {
        standardizeError.textContent = error.message || String(error);
      }
    } finally {
      updateStandardizeConfirmState();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (!session || event.target.matches("input, textarea, select")) return;
    switch (event.key.toLowerCase()) {
      case " ":
        event.preventDefault();
        session.paused ? session.play() : session.pause();
        break;
      case "arrowleft":
        event.preventDefault();
        seek(-5);
        break;
      case "arrowright":
        event.preventDefault();
        seek(5);
        break;
      case "j":
        seek(-10);
        break;
      case "l":
        seek(10);
        break;
      case "m":
        session.muted = !session.muted;
        break;
      case "f":
        if (document.fullscreenElement) document.exitFullscreen();
        else session.requestFullscreen();
        break;
    }
  });

  try {
    session = await createPlaybackSession(nativePlayer, {
      route: nativePlayer.dataset.playbackEngine || "native",
      scriptUrl: nativePlayer.dataset.moviScript,
      wasmUrl: nativePlayer.dataset.moviWasm,
      bufferMb: Number(nativePlayer.dataset.moviBufferMb || 96),
      loadTimeoutSeconds: Number(
        nativePlayer.dataset.moviLoadTimeout || 20,
      ),
      poster: nativePlayer.getAttribute("poster") || "",
      title: document.title,
    });
    bindPlayerEvents();
    await refreshPlayback(true);
  } catch (error) {
    showWarning({
      state: "movi_failed",
      title: "兼容播放器加载失败",
      message: error.message || "无法加载浏览器兼容播放器。",
      action_text: "点击重新检测，或使用支持该编码的播放器打开原文件。",
      retryable: true,
    });
  }
}
