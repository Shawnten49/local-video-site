const MOVI_TAG = "movi-player";

function withTimeout(promise, timeoutMs, message) {
  let timer = null;
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      timer = window.setTimeout(
        () => reject(new Error(message)),
        timeoutMs,
      );
    }),
  ]).finally(() => window.clearTimeout(timer));
}

function browserCanLoadMovi() {
  return Boolean(
    window.WebAssembly
    && window.AudioContext
    && window.customElements
    && HTMLCanvasElement.prototype.getContext
  );
}

function transferAttributes(source, target) {
  for (const name of source.getAttributeNames()) {
    if (name === "src") continue;
    target.setAttribute(name, source.getAttribute(name) || "");
  }
  target.id = source.id;
  target.className = source.className;
}

export class PlaybackSession {
  constructor(nativeElement, options = {}) {
    this.nativeElement = nativeElement;
    this.element = nativeElement;
    this.options = options;
    this.moviLoaded = false;
  }

  async initialize() {
    if (this.options.route !== "movi") return this;
    if (!browserCanLoadMovi()) {
      throw new Error("当前浏览器不具备兼容播放器所需能力");
    }
    const timeoutMs = Math.max(
      5000,
      Number(this.options.loadTimeoutSeconds || 20) * 1000,
    );
    await withTimeout(
      import(this.options.scriptUrl),
      timeoutMs,
      "兼容播放器脚本加载超时",
    );
    await withTimeout(
      window.customElements.whenDefined(MOVI_TAG),
      timeoutMs,
      "兼容播放器组件初始化超时",
    );
    const player = document.createElement(MOVI_TAG);
    transferAttributes(this.nativeElement, player);
    for (const track of this.nativeElement.querySelectorAll("track")) {
      player.appendChild(track.cloneNode(true));
    }
    player.setAttribute("controls", "");
    player.setAttribute("playsinline", "");
    player.setAttribute("nohotkeys", "");
    player.setAttribute("nolinkwarning", "");
    player.setAttribute("buffersize", String(this.options.bufferMb || 96));
    player.setAttribute("wasmurl", this.options.wasmUrl);
    if (this.options.poster) player.setAttribute("poster", this.options.poster);
    if (this.options.title) player.setAttribute("title", this.options.title);
    this.nativeElement.replaceWith(player);
    this.element = player;
    this.moviLoaded = true;
    return this;
  }

  load(url) {
    if (!url || this.element.getAttribute("src") === url) return;
    this.element.src = url;
    if (typeof this.element.load === "function") this.element.load();
  }

  play() {
    return this.element.play();
  }

  pause() {
    this.element.pause();
  }

  seek(seconds) {
    const maximum = Number(this.element.duration);
    this.element.currentTime = Math.max(
      0,
      Math.min(Number.isFinite(maximum) ? maximum : seconds, seconds),
    );
  }

  addEventListener(type, handler, options) {
    this.element.addEventListener(type, handler, options);
  }

  get paused() {
    return Boolean(this.element.paused);
  }

  get currentTime() {
    return Number(this.element.currentTime || 0);
  }

  get duration() {
    return Number(this.element.duration || 0);
  }

  get muted() {
    return Boolean(this.element.muted);
  }

  set muted(value) {
    this.element.muted = Boolean(value);
  }

  requestFullscreen() {
    return this.element.requestFullscreen();
  }

  destroy() {
    if (this.moviLoaded && this.element === this.nativeElement) return;
    if (typeof this.element.pause === "function") this.element.pause();
    if (this.moviLoaded && this.element.isConnected) {
      this.element.remove();
    }
  }
}

export async function createPlaybackSession(nativeElement, options = {}) {
  const session = new PlaybackSession(nativeElement, options);
  return session.initialize();
}
