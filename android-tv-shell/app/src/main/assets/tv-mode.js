(function () {
  "use strict";

  var STYLE_ID = "local-video-tv-style";
  var SETTINGS_ID = "local-video-tv-settings";
  var CONTROL_ID = "local-video-tv-playback-controls";
  var SEARCH_SELECTOR = ".topbar-search input[type='search'], input[type='search'][name='q']";

  function installStyle() {
    var style = document.getElementById(STYLE_ID);
    if (!style) {
      style = document.createElement("style");
      style.id = STYLE_ID;
      document.head.appendChild(style);
    }
    style.textContent = `
      html { scroll-padding-top: 110px; }
      body.tv-shell { font-size: 20px; }
      body.tv-shell .site-header { min-height: 84px; padding-top: 14px; padding-bottom: 14px; }
      body.tv-shell .brand { font-size: 21px; }
      body.tv-shell .category-button,
      body.tv-shell button,
      body.tv-shell input,
      body.tv-shell select { min-height: 52px; font-size: 18px; }
      body.tv-shell .video-grid {
        grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
        gap: 30px;
      }
      body.tv-shell .card-title-link h2 { font-size: 22px; line-height: 1.25; }
      body.tv-shell .card-meta-row p { font-size: 15px; }
      body.tv-shell .category-grid {
        grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
        gap: 16px;
      }
      body.tv-shell .actor-grid {
        grid-template-columns: repeat(auto-fill, minmax(270px, 1fr));
        gap: 16px;
      }
      body.tv-shell .actor-name { font-size: 18px; }
      body.tv-shell .actor-video-count { font-size: 14px; }
      body.tv-shell a:focus,
      body.tv-shell button:focus,
      body.tv-shell input:focus,
      body.tv-shell select:focus,
      body.tv-shell [tabindex]:focus {
        outline: 5px solid #ffffff !important;
        outline-offset: 4px !important;
        box-shadow: 0 0 0 10px rgba(255, 255, 255, .19) !important;
      }
      body.tv-shell .video-card-shell:focus-within .video-card {
        border-color: #ffffff;
        box-shadow: 0 0 0 5px #ffffff, 0 0 0 11px rgba(255, 255, 255, .18);
        transform: scale(1.025);
      }
      body.tv-shell .video-card { transition: transform .12s ease, box-shadow .12s ease; }
      body.tv-shell .episode-nav-button,
      body.tv-shell .season-episode-button { min-height: 70px; }
      body.tv-shell .season-episode-grid {
        grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
        gap: 12px;
      }
      body.tv-shell .episode-nav-label { font-size: 16px; }
      body.tv-shell .episode-nav-button strong { font-size: 17px; }
      body.tv-shell .shortcuts { display: none; }
      body.tv-shell #player:focus {
        outline: 5px solid #ffffff;
        outline-offset: 5px;
      }
      #${SETTINGS_ID} {
        position: fixed;
        right: 20px;
        bottom: 20px;
        z-index: 2147483000;
        min-width: 0;
        min-height: 48px;
        padding: 0 18px;
        border: 1px solid rgba(255,255,255,.28);
        border-radius: 12px;
        background: rgba(11, 13, 18, .88);
        color: #fff;
        font: 700 16px/1 sans-serif;
        opacity: .7;
      }
      #${SETTINGS_ID}:focus { opacity: 1; background: #ff4f64; }
      #${CONTROL_ID} {
        position: fixed;
        left: 50%;
        bottom: 20px;
        z-index: 2147482999;
        display: flex;
        gap: 10px;
        padding: 8px;
        border: 1px solid rgba(255,255,255,.18);
        border-radius: 14px;
        background: rgba(11, 13, 18, .86);
        transform: translateX(-50%);
        backdrop-filter: blur(12px);
      }
      #${CONTROL_ID} button {
        min-width: 72px;
        min-height: 48px;
        padding: 0 18px;
        border-radius: 10px;
        font-size: 16px;
      }
      @media (min-width: 1500px) {
        body.tv-shell .video-grid {
          grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
        }
      }
    `;
  }

  function ensureSettingsButton() {
    var button = document.getElementById(SETTINGS_ID);
    if (button) {
      return;
    }
    button = document.createElement("button");
    button.id = SETTINGS_ID;
    button.type = "button";
    button.textContent = "设置";
    button.setAttribute("aria-label", "设置视频服务器地址");
    button.addEventListener("click", function () {
      window.location.href = "localvideotv://settings";
    });
    document.body.appendChild(button);
  }

  function searchInput() {
    return document.querySelector(SEARCH_SELECTOR);
  }

  function isSearchInput(element) {
    return Boolean(element && element.matches(SEARCH_SELECTOR));
  }

  function prepareSearchInput() {
    var input = searchInput();
    if (!input) {
      return;
    }
    input.dataset.tvSearchEditing = "false";
    input.setAttribute("tabindex", "-1");

    if (!input.form || input.form.dataset.tvSearchBound === "true") {
      return;
    }
    input.form.dataset.tvSearchBound = "true";
    var button = input.form.querySelector("button[type='submit'], button");
    if (button) {
      button.addEventListener("click", function (event) {
        if (input.dataset.tvSearchEditing === "true") {
          return;
        }
        event.preventDefault();
        event.stopImmediatePropagation();
        input.dataset.tvSearchEditing = "true";
        input.setAttribute("tabindex", "0");
        window.requestAnimationFrame(function () {
          input.focus();
          input.click();
        });
      }, true);
    }
    input.addEventListener("blur", function () {
      window.setTimeout(function () {
        input.dataset.tvSearchEditing = "false";
        input.setAttribute("tabindex", "-1");
      }, 80);
    });
  }

  function finishSearchEditing(input) {
    if (!input) {
      return;
    }
    input.dataset.tvSearchEditing = "false";
    input.setAttribute("tabindex", "-1");
    input.blur();
    var button = input.form && input.form.querySelector("button[type='submit'], button");
    if (button) {
      button.focus();
    } else {
      focusFirst();
    }
  }

  function isVisible(element) {
    if (!element || element.disabled || element.getAttribute("aria-hidden") === "true") {
      return false;
    }
    if (element.tabIndex < 0) {
      return false;
    }
    var style = window.getComputedStyle(element);
    if (style.display === "none" || style.visibility === "hidden") {
      return false;
    }
    var rect = element.getBoundingClientRect();
    return rect.width > 2 && rect.height > 2;
  }

  function focusables() {
    var selector = [
      "a[href]",
      "button:not([disabled])",
      "input:not([disabled])",
      "select:not([disabled])",
      "[tabindex]:not([tabindex='-1'])"
    ].join(",");
    return Array.prototype.slice.call(document.querySelectorAll(selector))
      .filter(isVisible)
      .filter(function (element) {
        return !isSearchInput(element)
          || element.dataset.tvSearchEditing === "true";
      });
  }

  function focusFirst() {
    var input = searchInput();
    if (
      input
      && document.activeElement === input
      && input.dataset.tvSearchEditing !== "true"
    ) {
      input.blur();
    }
    var player = document.getElementById("player");
    if (player && isVisible(player)) {
      player.setAttribute("tabindex", "0");
      player.focus();
      return;
    }
    var preferred = document.querySelector(
      ".video-card-media, .episode-nav-button, .season-episode-button, .category-button"
    );
    if (preferred && isVisible(preferred)) {
      preferred.focus();
      return;
    }
    var first = focusables()[0];
    if (first) {
      first.focus();
    }
  }

  function moveFocus(key) {
    var items = focusables();
    if (!items.length) {
      return false;
    }
    var active = document.activeElement;
    if (items.indexOf(active) < 0) {
      focusFirst();
      return true;
    }

    var activeRect = active.getBoundingClientRect();
    var activeX = activeRect.left + activeRect.width / 2;
    var activeY = activeRect.top + activeRect.height / 2;
    var best = null;
    var bestScore = Infinity;

    items.forEach(function (item) {
      if (item === active) {
        return;
      }
      var rect = item.getBoundingClientRect();
      var x = rect.left + rect.width / 2;
      var y = rect.top + rect.height / 2;
      var dx = x - activeX;
      var dy = y - activeY;
      var primary;
      var orthogonal;

      if (key === "ArrowLeft") {
        if (dx >= -4) return;
        primary = -dx;
        orthogonal = Math.abs(dy);
      } else if (key === "ArrowRight") {
        if (dx <= 4) return;
        primary = dx;
        orthogonal = Math.abs(dy);
      } else if (key === "ArrowUp") {
        if (dy >= -4) return;
        primary = -dy;
        orthogonal = Math.abs(dx);
      } else if (key === "ArrowDown") {
        if (dy <= 4) return;
        primary = dy;
        orthogonal = Math.abs(dx);
      } else {
        return;
      }

      var score = primary + orthogonal * 1.9;
      if (score < bestScore) {
        best = item;
        bestScore = score;
      }
    });

    if (best) {
      best.focus({ preventScroll: true });
      best.scrollIntoView({ block: "nearest", inline: "nearest" });
      return true;
    }
    return false;
  }

  function installPlaybackControls() {
    var player = document.getElementById("player");
    if (!player || document.getElementById(CONTROL_ID)) {
      return;
    }

    var bar = document.createElement("div");
    bar.id = CONTROL_ID;
    bar.setAttribute("aria-label", "电视播放控制");

    function addControl(label, action) {
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.addEventListener("click", action);
      bar.appendChild(button);
    }

    function seek(delta) {
      player.currentTime = Math.max(
        0,
        Math.min(player.duration || Infinity, player.currentTime + delta)
      );
    }

    addControl("-10 秒", function () { seek(-10); });
    addControl("播放/暂停", function () {
      if (player.paused) {
        player.play();
      } else {
        player.pause();
      }
    });
    addControl("+10 秒", function () { seek(10); });
    document.body.appendChild(bar);
  }

  function isEditable(element) {
    return element && (
      element.matches("input, textarea, select")
      || element.isContentEditable
    );
  }

  function enforceInitialFocus() {
    var input = searchInput();
    var active = document.activeElement;
    if (
      !active
      || active === document.body
      || (active === input && input.dataset.tvSearchEditing !== "true")
    ) {
      focusFirst();
    }
  }

  function isInteractive(element) {
    return element && element.matches(
      "a, button, input, select, textarea, [role='button']"
    );
  }

  installStyle();
  document.body.classList.add("tv-shell");
  prepareSearchInput();
  ensureSettingsButton();
  installPlaybackControls();

  if (window.__LOCAL_VIDEO_TV_BOUND__) {
    setTimeout(enforceInitialFocus, 50);
    return;
  }
  window.__LOCAL_VIDEO_TV_BOUND__ = true;

  document.addEventListener("keydown", function (event) {
    var key = event.key;
    var active = document.activeElement;
    var input = searchInput();
    if (
      active === input
      && input.dataset.tvSearchEditing === "true"
    ) {
      if (key === "Enter" && input.form) {
        event.preventDefault();
        event.stopPropagation();
        if (input.form.requestSubmit) {
          input.form.requestSubmit();
        } else {
          input.form.submit();
        }
      } else if (key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        finishSearchEditing(input);
      }
      return;
    }
    if (isEditable(active)) {
      return;
    }

    var player = document.getElementById("player");
    var onWatchPage = location.pathname.indexOf("/watch/") === 0;
    var playerHasReaderFocus = !active
      || active === document.body
      || active === player;

    if (
      onWatchPage
      && player
      && playerHasReaderFocus
      && (key === "ArrowLeft" || key === "ArrowRight")
    ) {
      return;
    }

    if (
      key === "ArrowLeft"
      || key === "ArrowRight"
      || key === "ArrowUp"
      || key === "ArrowDown"
    ) {
      if (moveFocus(key)) {
        event.preventDefault();
        event.stopPropagation();
      }
      return;
    }

    if (
      onWatchPage
      && player
      && key === "Enter"
      && !isInteractive(active)
    ) {
      event.preventDefault();
      if (player.paused) {
        player.play();
      } else {
        player.pause();
      }
    }
  }, true);

  document.addEventListener("focusin", function (event) {
    if (
      isSearchInput(event.target)
      && event.target.dataset.tvSearchEditing !== "true"
    ) {
      window.setTimeout(function () {
        if (document.activeElement === event.target) {
          focusFirst();
        }
      }, 0);
    }
  }, true);

  setTimeout(enforceInitialFocus, 80);
  setTimeout(enforceInitialFocus, 220);
  setTimeout(enforceInitialFocus, 550);
  setTimeout(enforceInitialFocus, 1000);
})();
