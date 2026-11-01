/* Extra Live2D stage effects driven by the OLV audio payload.
 *
 * The model expressions themselves are still applied by the OLV frontend.
 * This layer adds lightweight, model-independent overlays such as fire for
 * anger and blush for shy/smirk so the change is visible even when a model's
 * expression is subtle.
 */
(() => {
  if (window.__dshLiveEffectsInstalled) return;
  window.__dshLiveEffectsInstalled = true;

  const NativeAudio = window.Audio;
  const appAudios = new Set();
  if (NativeAudio) {
    class TrackedAudio extends NativeAudio {
      constructor(...args) {
        super(...args);
        appAudios.add(this);
        this.addEventListener("ended", () => appAudios.delete(this));
      }
    }
    window.Audio = TrackedAudio;
  }

  const liveSockets = [];
  const NativeWebSocket = window.WebSocket;
  if (NativeWebSocket) {
    class EffectWebSocket extends NativeWebSocket {
      constructor(...args) {
        super(...args);
        liveSockets.push(this);
        this.addEventListener("message", (event) => {
          try {
            const message = JSON.parse(event.data);
            if (!message) return;
            // Emotion tags drive Live2D actions, but must never leak into the
            // chat bubble, subtitle, or streaming text.
            if (
              (message.type === "audio" || message.type === "full-text") &&
              message.display_text &&
              typeof message.display_text.text === "string"
            ) {
              message.display_text.text = message.display_text.text
                .replace(/\[[a-zA-Z][a-zA-Z0-9_\-]{0,24}\]/g, "")
                .replace(/[ \t]{2,}/g, " ")
                .trim();
            }
            if (message.type === "audio") {
              const expressions =
                message.expressions ||
                (message.actions && message.actions.expressions) ||
                [];
              window.__dshShowLiveEffect?.(expressions);
              window.__dshRevealReplyContext?.();
            } else if (message.type === "reply-context") {
              window.__dshSetReplyContext?.(message.text || "");
            } else if (message.type === "song-queue") {
              window.__dshSetSongQueue?.(message.items || []);
            } else if (message.type === "song" && message.action === "play") {
              window.__dshQueueSong?.(message);
            } else if (message.type === "song-control") {
              window.__dshSongControl?.(message.action || "");
            } else if (message.type === "backend-synth-complete") {
              window.__dshBackendSpeechDone?.();
            }
          } catch (_) {
            /* non-JSON frames are irrelevant */
          }
        });
      }
    }
    window.WebSocket = EffectWebSocket;
  }

  window.__dshSendMessage = (payload) => {
    for (const socket of liveSockets) {
      if (socket.readyState === 1) {
        socket.send(JSON.stringify(payload));
        return true;
      }
    }
    return false;
  };

  window.__dshSendText = (text) =>
    window.__dshSendMessage({ type: "text-input", text: String(text || "") });

  let layer = null;
  let clearTimer = 0;
  let fire = null;
  let blushLeft = null;
  let blushRight = null;
  let sparkle = null;

  let pendingSong = null;
  let songPlayTimer = 0;
  let songAudio = null;
  let backendSpeechDoneAt = 0;

  let replyContext = "";
  let replyContextEl = null;
  let replyContextTimers = [];

  let presenceEl = null;
  let presenceState = "";
  let presenceTimer = 0;
  let expressionTimers = [];

  let songQueue = [];
  let playlistEl = null;
  let playlistBody = null;
  let playlistTitle = null;
  let playlistControls = null;
  let currentSongTitle = "";
  let currentQueueIndex = -1;
  let currentEntryId = "";
  let songDurationMs = 0;
  let songArtist = "";
  let songNowEl = null;
  let songProgressFill = null;
  const songData = new Map();
  let songVolume = Number(localStorage.getItem("dsh-song-volume-v2") ?? 0.1);
  if (!Number.isFinite(songVolume) || songVolume < 0 || songVolume > 1) {
    songVolume = 0.1;
  }

  function ensureLayer() {
    if (layer) return;
    layer = document.createElement("div");
    layer.id = "dsh-live-effects";
    layer.innerHTML =
      '<div class="dsh-fx-fire" aria-hidden="true">🔥🔥</div>' +
      '<div class="dsh-fx-blush dsh-fx-blush-left" aria-hidden="true"></div>' +
      '<div class="dsh-fx-blush dsh-fx-blush-right" aria-hidden="true"></div>' +
      '<div class="dsh-fx-sparkle" aria-hidden="true">✦ ✧</div>';
    const style = document.createElement("style");
    style.textContent = `
      #dsh-live-effects {
        position: fixed;
        inset: 0;
        z-index: 9998;
        pointer-events: none;
        overflow: hidden;
      }
      #dsh-live-effects > div {
        position: absolute;
        opacity: 0;
        transform-origin: center;
      }
      .dsh-fx-fire {
        left: 50%;
        top: 12%;
        font-size: clamp(42px, 8vmin, 110px);
        transform: translateX(-50%) scale(.65);
        filter: drop-shadow(0 0 18px rgba(255, 120, 20, .8));
      }
      .dsh-fx-fire.on {
        animation: dsh-fire .9s ease-in-out infinite alternate;
        opacity: 1;
      }
      .dsh-fx-blush {
        top: 38%;
        width: clamp(42px, 8vmin, 110px);
        height: clamp(18px, 3vmin, 42px);
        border-radius: 50%;
        background: radial-gradient(ellipse, rgba(255, 105, 135, .8), rgba(255, 105, 135, 0) 70%);
        filter: blur(4px);
        transform: translateX(-50%);
      }
      .dsh-fx-blush.on { animation: dsh-blush 1.6s ease-in-out infinite alternate; opacity: 1; }
      .dsh-fx-sparkle {
        left: 50%;
        top: 20%;
        color: #ffe58a;
        font-size: clamp(24px, 5vmin, 64px);
        transform: translateX(-50%);
        text-shadow: 0 0 14px rgba(255, 220, 100, .85);
      }
      .dsh-fx-sparkle.on { animation: dsh-sparkle 1.2s ease-out infinite; opacity: 1; }
      @keyframes dsh-fire {
        from { transform: translateX(-50%) scale(.9) rotate(-3deg); }
        to { transform: translateX(-50%) scale(1.06) rotate(3deg); }
      }
      @keyframes dsh-blush {
        from { transform: translateX(-50%) scale(.9); opacity: .55; }
        to { transform: translateX(-50%) scale(1.1); opacity: .95; }
      }
      @keyframes dsh-sparkle {
        from { transform: translateX(-50%) scale(.7); opacity: .35; }
        50% { opacity: 1; }
        to { transform: translateX(-50%) scale(1.08); opacity: 0; }
      }
    `;
    document.head.appendChild(style);
    document.body.appendChild(layer);
    fire = layer.querySelector(".dsh-fx-fire");
    blushLeft = layer.querySelector(".dsh-fx-blush-left");
    blushRight = layer.querySelector(".dsh-fx-blush-right");
    sparkle = layer.querySelector(".dsh-fx-sparkle");
  }

  function ensurePresenceBubble() {
    if (presenceEl) return;
    const style = document.createElement("style");
    style.textContent = `
      #dsh-presence-bubble {
        position: fixed;
        z-index: 9997;
        transform: translateX(-50%);
        min-width: 82px;
        padding: 7px 15px 8px;
        border-radius: 15px 15px 15px 4px;
        border: 1px solid rgba(151, 198, 238, .28);
        background: linear-gradient(135deg, rgba(22, 33, 52, .92), rgba(44, 67, 96, .86));
        color: #e8f4ff;
        font: 13px/1.35 "Microsoft YaHei", "Segoe UI", system-ui, sans-serif;
        letter-spacing: .03em;
        text-align: center;
        box-shadow: 0 8px 24px rgba(0, 0, 0, .3), inset 0 1px 0 rgba(255,255,255,.08);
        backdrop-filter: blur(9px);
        pointer-events: none;
        opacity: 0;
        transition: opacity .25s ease, transform .25s ease;
        white-space: nowrap;
      }
      #dsh-presence-bubble::after {
        content: "";
        position: absolute;
        left: 15px;
        bottom: -6px;
        width: 11px;
        height: 11px;
        border-right: 1px solid rgba(151, 198, 238, .28);
        border-bottom: 1px solid rgba(151, 198, 238, .28);
        background: rgba(37, 57, 82, .9);
        transform: rotate(45deg);
      }
      #dsh-presence-bubble .dsh-presence-dot {
        display: inline-block;
        width: 6px;
        height: 6px;
        margin-right: 7px;
        border-radius: 50%;
        vertical-align: 1px;
        background: #8fd0ff;
        box-shadow: 0 0 8px rgba(112, 198, 255, .8);
      }
      #dsh-presence-bubble[data-state="听歌中"] .dsh-presence-dot {
        background: #b8a4ff;
        box-shadow: 0 0 9px rgba(178, 158, 255, .9);
      }
      #dsh-presence-bubble[data-state="摸鱼中"] .dsh-presence-dot {
        background: #8ff0c8;
        box-shadow: 0 0 9px rgba(118, 240, 194, .9);
      }
      #dsh-presence-bubble .dsh-presence-text {
        font-weight: 600;
      }
      #dsh-presence-bubble.show {
        opacity: 1;
      }
    `;
    document.head.appendChild(style);
    presenceEl = document.createElement("div");
    presenceEl.id = "dsh-presence-bubble";
    presenceEl.innerHTML =
      '<span class="dsh-presence-dot"></span><span class="dsh-presence-text"></span>';
    document.body.appendChild(presenceEl);
  }

  function positionPresenceBubble() {
    if (!presenceEl) return;
    const stage =
      document.querySelector("#live2d-internal-wrapper") ||
      document.querySelector("canvas#canvas")?.parentElement;
    if (!stage) {
      presenceEl.style.left = "50%";
      presenceEl.style.top = "18%";
      return;
    }
    const rect = stage.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    presenceEl.style.left = `${rect.left + rect.width / 2}px`;
    presenceEl.style.top = `${rect.top + rect.height * 0.13}px`;
  }

  function currentPresence() {
    const speaking = Array.from(appAudios).some(
      (audio) =>
        !audio.paused &&
        !audio.ended &&
        audio.currentTime > 0 &&
        audio.readyState > 1
    );
    if (speaking) return "摸鱼中";
    if (songAudio && !songAudio.paused && !songAudio.ended) return "听歌中";
    return "无聊中";
  }

  function updatePresenceBubble() {
    ensurePresenceBubble();
    positionPresenceBubble();
    const next = window.__dshWorkMode ? "工作中" : currentPresence();
    const textEl = presenceEl.querySelector(".dsh-presence-text");
    textEl.textContent = next;
    presenceEl.dataset.state = next;
    if (presenceState !== next) {
      presenceState = next;
      presenceEl.style.transform = "translateX(-50%) translateY(-3px)";
      requestAnimationFrame(() => {
        presenceEl.style.transform = "translateX(-50%) translateY(0)";
      });
    }
    presenceEl.classList.add("show");
  }

  function positionEffectLayer() {
    const stage =
      document.querySelector("#live2d-internal-wrapper") ||
      document.querySelector("canvas#canvas")?.parentElement;
    if (!stage || !fire || !blushLeft || !blushRight || !sparkle) return;
    const rect = stage.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const centerX = rect.left + rect.width / 2;
    fire.style.left = `${centerX}px`;
    fire.style.top = `${rect.top + rect.height * 0.08}px`;
    sparkle.style.left = `${centerX}px`;
    sparkle.style.top = `${rect.top + rect.height * 0.14}px`;
    const cheekY = rect.top + rect.height * 0.38;
    const cheekOffset = rect.width * 0.13;
    blushLeft.style.left = `${centerX - cheekOffset}px`;
    blushLeft.style.top = `${cheekY}px`;
    blushRight.style.left = `${centerX + cheekOffset}px`;
    blushRight.style.top = `${cheekY}px`;
  }

  function truncateText(text, limit) {
    const value = String(text || "").replace(/\s+/g, " ").trim();
    if (value.length <= limit) return value;
    return `${value.slice(0, limit)}…`;
  }

  function ensureReplyContext() {
    if (replyContextEl) return;
    replyContextEl = document.createElement("div");
    replyContextEl.id = "dsh-reply-context";
    replyContextEl.style.cssText = [
      "position:fixed",
      "z-index:9997",
      "transform:translateX(-50%)",
      "max-width:min(620px,72vw)",
      "padding:4px 10px",
      "border-left:2px solid rgba(120,170,215,.8)",
      "border-radius:5px",
      "background:rgba(15,22,36,.68)",
      "color:#b4d2ea",
      "font:12px/1.5 system-ui,sans-serif",
      "white-space:nowrap",
      "overflow:hidden",
      "text-overflow:ellipsis",
      "pointer-events:none",
      "opacity:0",
      "transition:opacity .2s ease",
    ].join(";");
    document.body.appendChild(replyContextEl);
  }

  function findSubtitleBox() {
    const statusLabels = new Set([
      "偷看弹幕中",
      "空闲",
      "思考/说话中",
      "已打断",
      "聆听中",
      "等待中",
      "加载中",
    ]);
    const nodes = document.querySelectorAll("div,span,p");
    let statusText = null;
    for (const node of nodes) {
      if (
        node.childElementCount === 0 &&
        statusLabels.has((node.textContent || "").trim())
      ) {
        statusText = node;
        break;
      }
    }
    let node = statusText;
    for (let depth = 0; node && depth < 7; depth += 1) {
      const rect = node.getBoundingClientRect();
      const position = getComputedStyle(node).position;
      if (
        position === "absolute" &&
        rect.width >= 280 &&
        rect.width <= 620 &&
        rect.height >= 36
      ) {
        return node;
      }
      node = node.parentElement;
    }
    // The built frontend renders the subtitle as a content-sized dark box
    // (`background: rgba(0, 0, 0, 0.7)`) inside a 60%-wide absolute wrapper.
    // Find that box directly; this is the frame the reply quote belongs in.
    const darkBoxes = [];
    for (const el of document.querySelectorAll("div")) {
      const rect = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      const bg = style.backgroundColor.replace(/\s+/g, "");
      const text = (el.textContent || "").trim();
      if (
        bg === "rgba(0,0,0,0.7)" &&
        rect.width >= 280 &&
        rect.height >= 30 &&
        rect.height <= 320 &&
        rect.bottom > window.innerHeight * 0.55 &&
        text.length >= 2 &&
        text.length <= 160
      ) {
        darkBoxes.push({ el, rect });
      }
    }
    if (darkBoxes.length) {
      darkBoxes.sort((a, b) => b.rect.width - a.rect.width);
      return darkBoxes[0].el;
    }

    // Also support the absolute ~440px wrapper used by some frontend builds.
    const candidates = [];
    for (const el of document.querySelectorAll("div")) {
      const rect = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      const text = (el.textContent || "").trim();
      if (
        style.position === "absolute" &&
        rect.width >= 280 &&
        rect.width <= 620 &&
        rect.height >= 30 &&
        rect.height <= 320 &&
        rect.bottom > window.innerHeight * 0.55 &&
        text.length >= 2 &&
        text.length <= 160 &&
        !statusLabels.has(text)
      ) {
        const score =
          Math.abs(rect.width - 440) +
          Math.abs(rect.bottom - (window.innerHeight - 80));
        candidates.push({ el, rect, score });
      }
    }
    candidates.sort((a, b) => a.score - b.score);
    return candidates[0]?.el || null;
  }

  function attachReplyContext() {
    if (!replyContext) return;
    ensureReplyContext();
    replyContextEl.textContent = `回复：${replyContext}`;
    const box = findSubtitleBox();
    if (box) {
      // Put the reply quote inside the subtitle container, flush with the
      // frame's top-left corner. The subtitle box has 15px/30px padding, so
      // pull the quote back by exactly that padding instead of sitting inside
      // the text content area.
      const boxStyle = getComputedStyle(box);
      const padLeft = parseFloat(boxStyle.paddingLeft) || 0;
      const padTop = parseFloat(boxStyle.paddingTop) || 0;
      replyContextEl.style.cssText = [
        "display:inline-flex",
        "align-self:flex-start",
        `margin:${-padTop + 1}px 0 4px ${-padLeft + 1}px`,
        "padding:2px 8px",
        "border-left:2px solid rgba(120,170,215,.85)",
        "border-radius:4px",
        "background:rgba(15,22,36,.42)",
        "color:#b4d2ea",
        "font:11px/1.4 system-ui,sans-serif",
        "white-space:nowrap",
        "overflow:hidden",
        "text-overflow:ellipsis",
        "pointer-events:none",
      ].join(";");
      box.insertBefore(replyContextEl, box.firstChild);
      return;
    }
    // Fallback before the subtitle container has mounted: place it at the
    // expected subtitle left/top area rather than at the chat list.
    const stage =
      document.querySelector("#live2d-internal-wrapper") ||
      document.querySelector("canvas#canvas")?.parentElement;
    const rect = stage?.getBoundingClientRect();
    if (!rect || !rect.width) return;
    replyContextEl.style.cssText = [
      "position:fixed",
      "z-index:9997",
      "box-sizing:border-box",
      "max-width:min(560px,72vw)",
      "padding:3px 10px",
      "border-left:2px solid rgba(120,170,215,.85)",
      "border-radius:4px",
      "background:rgba(15,22,36,.72)",
      "color:#b4d2ea",
      "font:11px/1.4 system-ui,sans-serif",
      "white-space:nowrap",
      "overflow:hidden",
      "text-overflow:ellipsis",
      "pointer-events:none",
    ].join(";");
    replyContextEl.style.left = `${rect.left + rect.width / 2 - 200}px`;
    replyContextEl.style.top = `${rect.bottom - 220}px`;
    document.body.appendChild(replyContextEl);
  }

  function scheduleReplyContext() {
    for (const timer of replyContextTimers) clearTimeout(timer);
    replyContextTimers = [];
    // The subtitle overlay is mounted slightly after the reply-context event.
    // Retry so the quote moves from the stage fallback into the real subtitle
    // container once React has rendered it.
    for (const delay of [80, 280, 700, 1300, 2200]) {
      replyContextTimers.push(setTimeout(attachReplyContext, delay));
    }
  }

  window.__dshSetReplyContext = (text) => {
    replyContext = truncateText(text, 34);
    if (!replyContext) {
      if (replyContextEl && replyContextEl.parentElement) {
        replyContextEl.remove();
      }
      return;
    }
    scheduleReplyContext();
  };

  window.__dshRevealReplyContext = () => {
    if (replyContext) scheduleReplyContext();
  };

  function ensurePlaylist() {
    if (playlistEl) return;
    playlistEl = document.createElement("div");
    playlistEl.id = "dsh-song-playlist";
    playlistEl.style.cssText = [
      "position:fixed",
      "top:92px",
      "right:22px",
      "z-index:9996",
      "width:min(270px,30vw)",
      "padding:10px 12px",
      "border-radius:12px",
      "background:rgba(12,18,30,.7)",
      "border:1px solid rgba(150,190,230,.16)",
      "box-shadow:0 6px 24px rgba(0,0,0,.25)",
      "backdrop-filter:blur(7px)",
      "color:#dbeaff",
      "font:12px/1.5 system-ui,sans-serif",
      "pointer-events:none",
      "opacity:0",
      "visibility:hidden",
      "transition:opacity .25s ease",
    ].join(";");
    playlistTitle = document.createElement("div");
    playlistTitle.style.cssText = [
      "display:flex",
      "align-items:center",
      "justify-content:space-between",
      "gap:8px",
      "padding-bottom:6px",
      "border-bottom:1px solid rgba(150,190,230,.14)",
      "color:#f1f7ff",
      "font-weight:600",
      "cursor:grab",
      "pointer-events:auto",
      "user-select:none",
      "touch-action:none",
    ].join(";");
    playlistControls = document.createElement("div");
    playlistControls.style.cssText = [
      "display:flex",
      "flex-wrap:wrap",
      "align-items:center",
      "gap:6px",
      "padding:7px 0 5px",
      "pointer-events:auto",
      "border-bottom:1px solid rgba(150,190,230,.10)",
    ].join(";");
    playlistBody = document.createElement("div");
    playlistBody.style.cssText = "padding-top:6px;max-height:230px;overflow:hidden;";
    playlistEl.appendChild(playlistTitle);
    playlistEl.appendChild(playlistControls);
    buildSongControls();
    playlistEl.appendChild(playlistBody);
    document.body.appendChild(playlistEl);
    installPlaylistDrag();
    restorePlaylistPosition();
  }

  let playlistDrag = null;

  function clampPlaylist(left, top) {
    const rect = playlistEl.getBoundingClientRect();
    const maxLeft = Math.max(8, window.innerWidth - rect.width - 8);
    const maxTop = Math.max(8, window.innerHeight - Math.min(rect.height, 260) - 8);
    return {
      left: Math.min(maxLeft, Math.max(8, left)),
      top: Math.min(maxTop, Math.max(8, top)),
    };
  }

  function applyPlaylistPosition(left, top) {
    const position = clampPlaylist(left, top);
    playlistEl.style.left = `${position.left}px`;
    playlistEl.style.top = `${position.top}px`;
    playlistEl.style.right = "auto";
    return position;
  }

  function restorePlaylistPosition() {
    try {
      const saved = JSON.parse(localStorage.getItem("dsh-playlist-pos") || "null");
      if (saved && Number.isFinite(saved.left) && Number.isFinite(saved.top)) {
        applyPlaylistPosition(saved.left, saved.top);
      }
    } catch (_) {
      /* ignore malformed saved position */
    }
  }

  function installPlaylistDrag() {
    if (!playlistTitle || playlistTitle.dataset.dragReady) return;
    playlistTitle.dataset.dragReady = "1";
    playlistTitle.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      const rect = playlistEl.getBoundingClientRect();
      playlistDrag = {
        dx: event.clientX - rect.left,
        dy: event.clientY - rect.top,
      };
      playlistTitle.style.cursor = "grabbing";
      playlistTitle.setPointerCapture?.(event.pointerId);
      event.preventDefault();
    });
    const move = (event) => {
      if (!playlistDrag) return;
      applyPlaylistPosition(
        event.clientX - playlistDrag.dx,
        event.clientY - playlistDrag.dy
      );
    };
    const end = () => {
      if (!playlistDrag) return;
      playlistDrag = null;
      playlistTitle.style.cursor = "grab";
      const rect = playlistEl.getBoundingClientRect();
      localStorage.setItem(
        "dsh-playlist-pos",
        JSON.stringify({ left: rect.left, top: rect.top })
      );
    };
    document.addEventListener("pointermove", move);
    document.addEventListener("pointerup", end);
    document.addEventListener("pointercancel", end);
    window.addEventListener("resize", () => {
      const rect = playlistEl.getBoundingClientRect();
      applyPlaylistPosition(rect.left, rect.top);
    });
  }

  let prevButton = null;
  let nextButton = null;
  let pauseButton = null;
  let stopButton = null;
  let volumeInput = null;
  let volumeLabel = null;
  let songStatusEl = null;

  function buildSongControls() {
    const buttonStyle = [
      "pointer-events:auto",
      "border:1px solid rgba(150,190,230,.22)",
      "background:rgba(255,255,255,.06)",
      "color:#e7f2ff",
      "border-radius:6px",
      "padding:3px 8px",
      "font:12px/1.4 system-ui,sans-serif",
      "cursor:pointer",
    ].join(";");
    prevButton = document.createElement("button");
    prevButton.textContent = "⏮";
    prevButton.title = "上一首";
    prevButton.style.cssText = buttonStyle;
    prevButton.addEventListener("click", () => playAdjacent(-1, false));

    pauseButton = document.createElement("button");
    pauseButton.textContent = "⏸";
    pauseButton.title = "暂停/继续音乐";
    pauseButton.style.cssText = buttonStyle;
    pauseButton.addEventListener("click", () => {
      if (!songAudio) return;
      if (songAudio.paused) {
        songAudio.play().catch(() => showSongChip("点击页面后重试播放"));
      } else {
        songAudio.pause();
      }
      updateSongControls();
    });

    nextButton = document.createElement("button");
    nextButton.textContent = "⏭";
    nextButton.title = "下一首 / 切歌";
    nextButton.style.cssText = buttonStyle;
    nextButton.addEventListener("click", () => playAdjacent(1, false));

    stopButton = document.createElement("button");
    stopButton.textContent = "⏹";
    stopButton.title = "关闭音乐";
    stopButton.style.cssText = buttonStyle;
    stopButton.addEventListener("click", stopSong);

    volumeLabel = document.createElement("span");
    volumeLabel.style.cssText = "color:#8fb7dc;flex:0 0 auto;";
    volumeInput = document.createElement("input");
    volumeInput.type = "range";
    volumeInput.min = "0";
    volumeInput.max = "100";
    volumeInput.step = "1";
    volumeInput.value = String(Math.round(songVolume * 100));
    volumeInput.title = "音乐音量";
    volumeInput.style.cssText =
      "pointer-events:auto;flex:1;min-width:70px;accent-color:#78a9d4;cursor:pointer;";
    volumeInput.addEventListener("input", () => {
      songVolume = Math.max(0, Math.min(1, Number(volumeInput.value) / 100));
      localStorage.setItem("dsh-song-volume", String(songVolume));
      if (songAudio) songAudio.volume = songVolume;
      updateSongControls();
    });

    songStatusEl = document.createElement("div");
    songStatusEl.style.cssText = [
      "pointer-events:none",
      "min-height:16px",
      "padding:2px 0 4px",
      "color:#ffb4b4",
      "font:11px/1.4 system-ui,sans-serif",
      "white-space:nowrap",
      "overflow:hidden",
      "text-overflow:ellipsis",
    ].join(";");
    const buttonRow = document.createElement("div");
    buttonRow.style.cssText =
      "display:flex;align-items:center;gap:6px;width:100%;";
    buttonRow.append(prevButton, pauseButton, nextButton, stopButton);
    const volumeRow = document.createElement("div");
    volumeRow.style.cssText =
      "display:flex;align-items:center;gap:7px;width:100%;padding-top:2px;";
    volumeRow.append(volumeLabel, volumeInput);
    playlistControls.append(buttonRow, volumeRow);

    songNowEl = document.createElement("div");
    songNowEl.style.cssText = [
      "padding:5px 0 3px",
      "color:#cfe5f8",
      "font:11px/1.4 system-ui,sans-serif",
      "white-space:nowrap",
      "overflow:hidden",
      "text-overflow:ellipsis",
    ].join(";");
    const progressWrap = document.createElement("div");
    progressWrap.style.cssText = [
      "width:100%",
      "height:4px",
      "border-radius:999px",
      "background:rgba(255,255,255,.12)",
      "overflow:hidden",
    ].join(";");
    songProgressFill = document.createElement("div");
    songProgressFill.style.cssText = [
      "width:0%",
      "height:100%",
      "border-radius:999px",
      "background:linear-gradient(90deg,#78a9d4,#a8d4ff)",
      "transition:width .2s linear",
    ].join(";");
    progressWrap.appendChild(songProgressFill);

    playlistEl.appendChild(songNowEl);
    playlistEl.appendChild(progressWrap);
    playlistEl.appendChild(songStatusEl);
    updateSongControls();
  }

  function updateSongControls() {
    if (
      !prevButton ||
      !pauseButton ||
      !nextButton ||
      !stopButton ||
      !volumeInput ||
      !volumeLabel
    ) {
      return;
    }
    const active = Boolean(songAudio);
    const canPrev = songQueue.length > 0 && currentQueueIndex > 0;
    const canNext =
      songQueue.length > 0 &&
      currentQueueIndex >= 0 &&
      currentQueueIndex < songQueue.length - 1;
    prevButton.disabled = !canPrev;
    nextButton.disabled = !canNext;
    pauseButton.disabled = !active;
    stopButton.disabled = !active;
    volumeInput.disabled = false;
    pauseButton.textContent = active && songAudio.paused ? "▶" : "⏸";
    for (const button of [prevButton, nextButton, pauseButton, stopButton]) {
      button.style.opacity = button.disabled ? ".38" : "1";
    }
    volumeLabel.textContent = `音量 ${Math.round(songVolume * 100)}%`;
  }

  function updateSongProgress() {
    if (!songNowEl || !songProgressFill) return;
    if (!currentSongTitle) {
      songNowEl.textContent = "";
      songProgressFill.style.width = "0%";
      return;
    }
    const artist = songArtist ? ` — ${songArtist}` : "";
    songNowEl.textContent = `正在播放：${currentSongTitle}${artist}`;
    const total =
      (songAudio && Number.isFinite(songAudio.duration) && songAudio.duration > 0
        ? songAudio.duration
        : songDurationMs / 1000) || 0;
    const elapsed =
      songAudio && Number.isFinite(songAudio.currentTime)
        ? songAudio.currentTime
        : 0;
    const pct = total > 0 ? Math.max(0, Math.min(100, (elapsed / total) * 100)) : 0;
    songProgressFill.style.width = `${pct}%`;
  }

  function notifySongFinished(title, entryId) {
    if (!title && !entryId) return;
    window.__dshSendMessage?.({
      type: "song-finished",
      title: title || "",
      entry_id: entryId || "",
    });
  }

  function dropFinishedSong(title, entryId) {
    const normalized = String(title || "").trim();
    let index = entryId
      ? songQueue.findIndex((item) => String(item.id || "") === entryId)
      : -1;
    if (
      index < 0 &&
      currentQueueIndex >= 0 &&
      currentQueueIndex < songQueue.length
    ) {
      index = currentQueueIndex;
    }
    if (index < 0) {
      index = songQueue.findIndex(
        (item) => String(item.title || "").trim() === normalized
      );
    }
    if (index >= 0) songQueue.splice(index, 1);
    return index;
  }

  function stopSong() {
    pendingSong = null;
    clearTimeout(songPlayTimer);
    const finishedTitle = currentSongTitle;
    const finishedEntryId = currentEntryId;
    notifySongFinished(finishedTitle, finishedEntryId);
    dropFinishedSong(finishedTitle, finishedEntryId);
    currentEntryId = "";
    if (songAudio) {
      try {
        songAudio.pause();
        songAudio.src = "";
      } catch (_) {
        /* ignore */
      }
      songAudio = null;
    }
    currentSongTitle = "";
    songArtist = "";
    songDurationMs = 0;
    currentQueueIndex = -1;
    hideSongChip();
    renderPlaylist();
    updateSongProgress();
    updateSongControls();
  }

  function renderPlaylist() {
    ensurePlaylist();
    if (!songQueue.length) {
      playlistEl.style.opacity = "0";
      playlistEl.style.visibility = "hidden";
      playlistBody.innerHTML = "";
      return;
    }
    playlistEl.style.opacity = "1";
    playlistEl.style.visibility = "visible";
    playlistTitle.innerHTML =
      `<span>⠿ 🎵 歌单</span><span style="color:#8fb7dc;font-weight:400">${songQueue.length} 首</span>`;
    if (currentSongTitle) {
      const found = songQueue.findIndex(
        (item) => String(item.title || "").trim() === currentSongTitle.trim()
      );
      if (found >= 0) currentQueueIndex = found;
    }
    updateSongProgress();
    updateSongControls();
    playlistBody.innerHTML = "";
    const isLiveView = window.location.pathname.includes("live-view");
    for (const item of songQueue.slice(0, 5)) {
      const row = document.createElement("div");
      const isCurrent =
        currentSongTitle &&
        String(item.title || "").trim() === currentSongTitle.trim();
      row.style.cssText = [
        "display:flex",
        "gap:7px",
        "align-items:baseline",
        "padding:4px 0",
        "color:" + (isCurrent ? "#ffe6a3" : "#c8ddf0"),
        "opacity:" + (isCurrent ? "1" : ".82"),
        "white-space:nowrap",
        "overflow:hidden",
        "text-overflow:ellipsis",
        "pointer-events:auto",
      ].join(";");
      const num = document.createElement("span");
      num.textContent = isCurrent ? "▶" : `${item.position}.`;
      num.style.cssText = "color:#78a9d4;flex:0 0 auto;min-width:14px;";
      const song = document.createElement("span");
      song.textContent = truncateText(item.title, 20);
      song.style.cssText = "flex:1;overflow:hidden;text-overflow:ellipsis;";
      const user = document.createElement("span");
      user.textContent = truncateText(item.user, 8);
      user.style.cssText = "color:#7e9bb7;flex:0 0 auto;";
      row.append(num, song, user);
      if (!isLiveView) {
        const remove = document.createElement("button");
        remove.textContent = "×";
        remove.title = `取消点歌：${item.title}`;
        remove.style.cssText = [
          "pointer-events:auto",
          "border:0",
          "background:transparent",
          "color:#8fb0ca",
          "font:14px/1 system-ui,sans-serif",
          "cursor:pointer",
          "padding:0 1px",
          "flex:0 0 auto",
        ].join(";");
        remove.addEventListener("click", (event) => {
          event.stopPropagation();
          window.__dshSendText?.(`取消点歌 ${item.title}`);
        });
        row.appendChild(remove);
      }
      playlistBody.appendChild(row);
    }
    if (songQueue.length > 5) {
      const more = document.createElement("div");
      more.textContent = `还有 ${songQueue.length - 5} 首…`;
      more.style.cssText = "padding-top:4px;color:#7e9bb7;";
      playlistBody.appendChild(more);
    }
  }

  window.__dshSetSongQueue = (items) => {
    const incoming = Array.isArray(items) ? items : [];
    songQueue = incoming.map((item) => {
      const existing = songData.get(String(item.title || "").trim()) || {};
      return {
        ...item,
        artist: item.artist || existing.artist || "",
        track_id: item.track_id || existing.track_id || "",
        url: item.url || existing.url || "",
      };
    });
    if (currentSongTitle) {
      currentQueueIndex = currentEntryId
        ? songQueue.findIndex(
            (item) => String(item.id || "") === currentEntryId
          )
        : -1;
      if (currentQueueIndex < 0) {
        currentQueueIndex = songQueue.findIndex(
          (item) => String(item.title || "").trim() === currentSongTitle.trim()
        );
      }
      if (currentQueueIndex >= 0 && !currentEntryId) {
        currentEntryId = String(songQueue[currentQueueIndex].id || "");
      }
    } else if (currentQueueIndex >= songQueue.length) {
      currentQueueIndex = -1;
    }
    renderPlaylist();
  };

  function showSongChip(text) {
    if (!songStatusEl) return;
    songStatusEl.textContent = String(text || "").replace(/^♪\s*/, "");
  }

  function hideSongChip() {
    if (songStatusEl) songStatusEl.textContent = "";
  }

  function pauseTtsAudio() {
    for (const audio of appAudios) {
      try {
        audio.pause();
      } catch (_) {
        /* already stopped */
      }
    }
  }

  function playAdjacent(step, autoAdvance) {
    if (!songQueue.length) {
      if (!autoAdvance) showSongChip("歌单是空的");
      return;
    }
    let base = currentQueueIndex;
    if (base < 0) {
      base = songQueue.findIndex(
        (item) => String(item.title || "").trim() === currentSongTitle.trim()
      );
    }
    const target = base + step;
    if (target < 0 || target >= songQueue.length) {
      if (!autoAdvance) {
        showSongChip(step > 0 ? "已经是最后一首" : "已经是第一首");
      }
      return;
    }
    const item = songQueue[target];
    const data = songData.get(String(item.title || "").trim());
    if (!data || !data.url) {
      showSongChip("这首歌的播放地址还没取到");
      return;
    }
    currentQueueIndex = target;
    playSong(data);
  }

  function playSong(song) {
    if (!song || !song.url) return;
    clearTimeout(songPlayTimer);
    pendingSong = null;
    pauseTtsAudio();
    if (songAudio) {
      try {
        songAudio.pause();
      } catch (_) {
        /* ignore */
      }
      songAudio = null;
    }
    currentSongTitle = String(song.title || "");
    currentEntryId = String(song.entry_id || "");
    songArtist = String(song.artist || "");
    songDurationMs = Number(song.duration_ms) || 0;
    const title = currentSongTitle.trim();
    const index = songQueue.findIndex(
      (item) =>
        (currentEntryId && String(item.id || "") === currentEntryId) ||
        String(item.title || "").trim() === title
    );
    if (index >= 0) {
      currentQueueIndex = index;
      currentEntryId = currentEntryId || String(songQueue[index].id || "");
    }
    songData.set(title, song);
    const audio = new NativeAudio(`/song-audio?url=${encodeURIComponent(song.url)}`);
    audio.volume = songVolume;
    audio.preload = "auto";
    songAudio = audio;
    hideSongChip();
    renderPlaylist();
    updateSongProgress();
    updateSongControls();
    const syncProgress = () => {
      if (songAudio === audio) updateSongProgress();
    };
    audio.addEventListener("timeupdate", syncProgress);
    audio.addEventListener("loadedmetadata", syncProgress);
    audio.addEventListener("ended", () => {
      if (songAudio !== audio) return;
      songAudio = null;
      const finishedTitle = currentSongTitle;
      const finishedEntryId = currentEntryId;
      notifySongFinished(finishedTitle, finishedEntryId);
      const removedAt = dropFinishedSong(finishedTitle, finishedEntryId);
      currentEntryId = "";
      songArtist = "";
      songDurationMs = 0;
      currentSongTitle = "";
      const nextIndex = removedAt >= 0 ? removedAt : Math.max(0, currentQueueIndex);
      const nextItem = songQueue[nextIndex];
      const nextData = nextItem
        ? songData.get(String(nextItem.title || "").trim())
        : null;
      if (nextData && nextData.url) {
        currentQueueIndex = nextIndex;
        playSong(nextData);
      } else {
        currentQueueIndex = -1;
        hideSongChip();
        renderPlaylist();
        updateSongProgress();
        updateSongControls();
      }
    });
    audio.addEventListener("error", () => {
      showSongChip("这首歌暂时播放失败");
      updateSongControls();
    });
    audio.play().catch(() => {
      showSongChip("点击页面后重试播放");
    });
    updateSongControls();
  }

  window.__dshQueueSong = (song) => {
    if (!song || !song.url) return;
    const title = String(song.title || "");
    const normalized = title.trim();
    songData.set(normalized, song);
    const entryId = String(song.entry_id || "");
    const index = songQueue.findIndex(
      (item) =>
        (entryId && String(item.id || "") === entryId) ||
        String(item.title || "").trim() === normalized
    );
    if (index >= 0) {
      songQueue[index] = {
        ...songQueue[index],
        id: entryId || songQueue[index].id,
        url: song.url,
        artist: song.artist || songQueue[index].artist,
        track_id: song.track_id || songQueue[index].track_id,
      };
      renderPlaylist();
    }
    // A new request must never replace the song that is currently audible.
    if (songAudio || pendingSong) return;
    currentSongTitle = title;
    currentEntryId =
      String(song.entry_id || "") ||
      (index >= 0 ? String(songQueue[index].id || "") : "");
    songArtist = String(song.artist || "");
    songDurationMs = Number(song.duration_ms) || 0;
    currentQueueIndex = index;
    pendingSong = song;
    // Give the acknowledgement TTS a chance to finish first. A real
    // backend-synth-complete cancels this fallback timer.
    clearTimeout(songPlayTimer);
    songPlayTimer = setTimeout(() => {
      if (pendingSong) playSong(pendingSong);
    }, 7000);
    if (backendSpeechDoneAt > 0) {
      songPlayTimer = setTimeout(() => {
        if (pendingSong) playSong(pendingSong);
      }, 500);
    }
  };

  window.__dshSongControl = (action) => {
    if (action === "next") {
      playAdjacent(1, false);
    } else if (action === "prev") {
      playAdjacent(-1, false);
    } else if (action === "stop") {
      stopSong();
    } else if (action === "pause") {
      if (songAudio && !songAudio.paused) songAudio.pause();
      updateSongControls();
    } else if (action === "resume") {
      if (songAudio && songAudio.paused) {
        songAudio.play().catch(() => showSongChip("点击页面后重试播放"));
      }
      updateSongControls();
    }
  };

  window.__dshBackendSpeechDone = () => {
    backendSpeechDoneAt = Date.now();
    if (!pendingSong) return;
    clearTimeout(songPlayTimer);
    songPlayTimer = setTimeout(() => {
      if (pendingSong) playSong(pendingSong);
    }, 600);
  };

  const EMOTION_TAG_PATTERN =
    /\[(?:neutral|joy|smirk|fear|surprise|sadness|anger|disgust|happy2?|puzzled|relax|unhappy|sad|excited|shy|blush)\]/gi;

  function sanitizeRenderedTags(root = document.body) {
    if (!root) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const parent = node.parentElement;
        if (
          !parent ||
          /^(SCRIPT|STYLE|TEXTAREA|INPUT|CODE|PRE)$/.test(parent.tagName)
        ) {
          return NodeFilter.FILTER_REJECT;
        }
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    const nodes = [];
    let node = walker.nextNode();
    while (node) {
      nodes.push(node);
      node = walker.nextNode();
    }
    for (const textNode of nodes) {
      const value = textNode.nodeValue || "";
      if (!EMOTION_TAG_PATTERN.test(value)) continue;
      EMOTION_TAG_PATTERN.lastIndex = 0;
      const cleaned = value
        .replace(EMOTION_TAG_PATTERN, "")
        .replace(/[ \t]{2,}/g, " ")
        .trim();
      if (cleaned !== value) textNode.nodeValue = cleaned;
    }
    EMOTION_TAG_PATTERN.lastIndex = 0;
  }

  let tagSanitizeFrame = 0;
  function watchRenderedTags() {
    sanitizeRenderedTags();
    if (!window.MutationObserver) return;
    const observer = new MutationObserver(() => {
      if (tagSanitizeFrame) return;
      tagSanitizeFrame = requestAnimationFrame(() => {
        tagSanitizeFrame = 0;
        sanitizeRenderedTags();
      });
    });
    observer.observe(document.body, {
      childList: true,
      characterData: true,
      subtree: true,
    });
  }

  function hideAll() {
    [fire, blushLeft, blushRight, sparkle].forEach((el) => {
      if (el) el.classList.remove("on");
    });
  }

  function applyExpressionOnce(rawValue) {
    let applied = false;
    let name = rawValue;
    try {
      const adapter = window.getLAppAdapter?.();
      if (adapter && typeof adapter.getExpressionName === "function" && typeof rawValue === "number") {
        name = adapter.getExpressionName(rawValue) || name;
      }
      if (adapter && typeof adapter.setExpression === "function") {
        adapter.setExpression(name);
        applied = true;
      }
    } catch (_) {
      /* the built frontend may not expose the adapter */
    }
    try {
      const manager = window.getLive2DManager?.();
      let model = manager?.getModel?.(0);
      if (!model && manager?.getModel) {
        // Usually getModel(0); retry indices defensively for older builds.
        for (let index = 0; index < 3 && !model; index += 1) {
          model = manager.getModel(index);
        }
      }
      if (!model && window.getLAppAdapter) {
        model = window.getLAppAdapter()?.getModel?.();
      }
      if (model && typeof model.setExpression === "function") {
        let expressionName = name;
        if (typeof rawValue === "number" && model._modelSetting?.getExpressionName) {
          expressionName = model._modelSetting.getExpressionName(rawValue) || expressionName;
        }
        model.setExpression(String(expressionName));
        applied = true;
      }
    } catch (error) {
      console.warn("[dsh] expression apply failed", error);
    }
    window.__dshLastExpression = String(name);
    return applied;
  }

  function applyExpressionSustained(rawValue) {
    for (const timer of expressionTimers) clearTimeout(timer);
    expressionTimers = [];
    // OLV can reset the default expression on AI-state transitions. Re-apply
    // a few times while the new audio utterance settles so the requested
    // expression is actually visible.
    for (const delay of [0, 120, 450, 1000, 1800]) {
      expressionTimers.push(
        setTimeout(() => {
          const applied = applyExpressionOnce(rawValue);
          console.debug("[dsh] expression", rawValue, applied);
        }, delay)
      );
    }
  }

  window.__dshShowLiveEffect = (expressions) => {
    const values = (Array.isArray(expressions) ? expressions : []).map((v) =>
      typeof v === "number" ? v : String(v).toLowerCase()
    );
    if (!values.length) return;
    ensureLayer();
    clearTimeout(clearTimer);
    hideAll();
    positionEffectLayer();
    if (replyContext) scheduleReplyContext();
    applyExpressionSustained(values[0]);
    const joined = values.join(" ");
    if (/anger|angry/.test(joined)) {
      fire?.classList.add("on");
    } else if (/happy2|smirk|shy|blush/.test(joined)) {
      blushLeft?.classList.add("on");
      blushRight?.classList.add("on");
    } else if (/happy|joy/.test(joined)) {
      sparkle?.classList.add("on");
    } else {
      return;
    }
    clearTimer = setTimeout(hideAll, 2600);
  };

  function replaceThinkingLabels() {
    const walker = document.createTreeWalker(
      document.body || document.documentElement,
      NodeFilter.SHOW_TEXT
    );
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) {
      const parent = node.parentElement;
      if (!parent || /^(SCRIPT|STYLE|TEXTAREA|INPUT)$/.test(parent.tagName)) {
        continue;
      }
      const value = node.nodeValue;
      if (!value || !/thinking/i.test(value)) continue;
      const replaced = value.replace(/thinking[.。…]*/gi, "偷看弹幕中");
      if (replaced !== value) node.nodeValue = replaced;
    }
  }

  function installTextPatches() {
    ensureLayer();
    ensureReplyContext();
    ensurePlaylist();
    ensurePresenceBubble();
    positionEffectLayer();
    updatePresenceBubble();
    clearInterval(presenceTimer);
    presenceTimer = setInterval(updatePresenceBubble, 500);
    window.addEventListener("resize", updatePresenceBubble);
    replaceThinkingLabels();
    watchRenderedTags();
    const observer = new MutationObserver(() => {
      replaceThinkingLabels();
    });
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true,
    });
  }

  document.addEventListener("DOMContentLoaded", installTextPatches, { once: true });
})();
