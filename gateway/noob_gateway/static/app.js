(() => {
  "use strict";

  function createSerializedInputQueue(send, onFailure, isActive, maxDepth = 64) {
    let tail = Promise.resolve();
    let depth = 0;

    async function reportFailure(error) {
      try { await onFailure(error); } catch (_) { /* deadman remains active */ }
    }

    function enqueue(command) {
      if (!isActive()) return Promise.resolve(false);
      if (depth >= maxDepth) {
        void reportFailure(new Error("Input queue overflow"));
        return Promise.resolve(false);
      }
      depth += 1;
      const task = tail.then(async () => {
        if (!isActive()) return false;
        try {
          await send(command);
          return true;
        } catch (error) {
          await reportFailure(error);
          return false;
        }
      }).finally(() => { depth -= 1; });
      tail = task.catch(() => false);
      return task;
    }

    return Object.freeze({enqueue, idle: () => tail, depth: () => depth});
  }

  function leaseRenewInterval(ttlMs) {
    const ttl = Number(ttlMs);
    if (!Number.isFinite(ttl) || ttl < 500) return 2000;
    return Math.max(250, Math.floor(ttl / 2));
  }

  if (typeof document === "undefined" && typeof module !== "undefined" && module.exports) {
    module.exports = {createSerializedInputQueue, leaseRenewInterval};
    return;
  }

  const tokenInput = document.querySelector("#token");
  const connectButton = document.querySelector("#connect");
  const releaseButton = document.querySelector("#release");
  const captureToggle = document.querySelector("#capture");
  const typeButton = document.querySelector("#typeButton");
  const typeText = document.querySelector("#typeText");
  const intervalInput = document.querySelector("#interval");
  const screen = document.querySelector("#screen");
  const state = document.querySelector("#state");
  const statusView = document.querySelector("#status");

  let token = "";
  let lease = "";
  let frameUrl = "";
  let renewTimer = 0;
  let frameTimer = 0;
  let statusTimer = 0;
  let mouseX = 0;
  let mouseY = 0;
  let mouseScheduled = false;
  let failClosedPromise = null;
  let renewInFlight = false;

  const keyMap = {
    Enter: "ENTER", Escape: "ESCAPE", Backspace: "BACKSPACE", Tab: "TAB", Space: "SPACE",
    Minus: "MINUS", Equal: "EQUALS", BracketLeft: "LEFT_BRACKET", BracketRight: "RIGHT_BRACKET",
    Backslash: "BACKSLASH", Semicolon: "SEMICOLON", Quote: "QUOTE", Backquote: "GRAVE_ACCENT",
    Comma: "COMMA", Period: "PERIOD", Slash: "FORWARD_SLASH", CapsLock: "CAPS_LOCK",
    PrintScreen: "PRINT_SCREEN", ScrollLock: "SCROLL_LOCK", Pause: "PAUSE", Insert: "INSERT",
    Home: "HOME", PageUp: "PAGE_UP", Delete: "DELETE", End: "END", PageDown: "PAGE_DOWN",
    ArrowRight: "RIGHT_ARROW", ArrowLeft: "LEFT_ARROW", ArrowDown: "DOWN_ARROW", ArrowUp: "UP_ARROW",
    ContextMenu: "APPLICATION", ControlLeft: "LEFT_CONTROL", ShiftLeft: "LEFT_SHIFT",
    AltLeft: "LEFT_ALT", MetaLeft: "LEFT_GUI", ControlRight: "RIGHT_CONTROL",
    ShiftRight: "RIGHT_SHIFT", AltRight: "RIGHT_ALT", MetaRight: "RIGHT_GUI"
  };
  const digits = ["ZERO", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE"];
  for (let i = 1; i <= 12; i += 1) keyMap[`F${i}`] = `F${i}`;

  function canonicalKey(code) {
    if (/^Key[A-Z]$/.test(code)) return code.slice(3);
    if (/^Digit[0-9]$/.test(code)) return digits[Number(code.slice(5))];
    return keyMap[code] || "";
  }

  function authHeaders(withLease = false) {
    const headers = {Authorization: `Bearer ${token}`, "Content-Type": "application/json"};
    if (withLease && lease) headers["X-NOOB-Lease"] = lease;
    return headers;
  }

  async function post(path, body = {}, withLease = false) {
    const response = await fetch(path, {method: "POST", headers: authHeaders(withLease), body: JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  function setConnected(value, message = "") {
    state.textContent = message || (value ? "Control active" : "Disconnected");
    state.className = `state ${value ? "online" : "offline"}`;
    captureToggle.disabled = !value;
    typeButton.disabled = !value;
    if (!value) captureToggle.checked = false;
  }

  function stopControl(message) {
    clearInterval(renewTimer);
    renewTimer = 0;
    captureToggle.checked = false;
    mouseX = 0;
    mouseY = 0;
    if (document.pointerLockElement) document.exitPointerLock();
    setConnected(false, message);
  }

  function failClosed(message) {
    if (failClosedPromise) return failClosedPromise;
    stopControl(message);
    const leaseBeingReleased = lease;
    failClosedPromise = (async () => {
      try {
        if (token) await post("/api/v1/release-all");
      } catch (_) {
        /* Renewal is stopped, so the server and Pico deadmen remain authoritative. */
      } finally {
        if (lease === leaseBeingReleased) lease = "";
        stopControl(message);
        failClosedPromise = null;
      }
    })();
    return failClosedPromise;
  }

  async function renewLease() {
    if (!lease || failClosedPromise || renewInFlight) return;
    renewInFlight = true;
    try {
      await post("/api/v1/control/renew", {}, true);
    } catch (error) {
      await failClosed(error.message || "Lease lost");
    } finally {
      renewInFlight = false;
    }
  }

  async function connect() {
    if (lease || failClosedPromise) return;
    token = tokenInput.value.trim();
    if (!token) return;
    try {
      const data = await post("/api/v1/control/claim");
      lease = data.lease;
      tokenInput.value = "";
      setConnected(true);
      clearInterval(renewTimer);
      renewTimer = setInterval(() => { void renewLease(); }, leaseRenewInterval(data.ttl_ms));
      startPolling();
    } catch (error) {
      setConnected(false, error.message);
    }
  }

  const inputQueue = createSerializedInputQueue(
    (command) => post("/api/v1/input", command, true),
    (error) => failClosed(error.message || "Input failed"),
    () => Boolean(lease) && !failClosedPromise,
  );

  function sendInput(command) {
    return inputQueue.enqueue(command);
  }

  async function releaseAll() {
    if (!token && !lease) return;
    await failClosed("Input released");
  }

  async function pollFrame() {
    if (!token) return;
    try {
      const response = await fetch("/api/v1/frame.jpg", {headers: {Authorization: `Bearer ${token}`}, cache: "no-store"});
      if (!response.ok) return;
      const next = URL.createObjectURL(await response.blob());
      screen.src = next;
      if (frameUrl) URL.revokeObjectURL(frameUrl);
      frameUrl = next;
    } catch (_) { /* status polling reports availability */ }
  }

  async function pollStatus() {
    if (!token) return;
    try {
      const response = await fetch("/api/v1/status", {headers: {Authorization: `Bearer ${token}`}, cache: "no-store"});
      statusView.textContent = JSON.stringify(await response.json(), null, 2);
    } catch (_) { statusView.textContent = "Status unavailable"; }
  }

  function startPolling() {
    clearInterval(frameTimer);
    clearInterval(statusTimer);
    pollFrame(); pollStatus();
    frameTimer = setInterval(pollFrame, 200);
    statusTimer = setInterval(pollStatus, 1000);
  }

  connectButton.addEventListener("click", connect);
  releaseButton.addEventListener("click", releaseAll);
  typeButton.addEventListener("click", () => {
    const text = typeText.value;
    const interval = Math.max(0, Math.min(25, Number(intervalInput.value) || 0));
    if (text) sendInput({op: "type", text, interval_ms: interval});
  });

  window.addEventListener("keydown", (event) => {
    if (!lease || !captureToggle.checked || event.repeat) return;
    const key = canonicalKey(event.code);
    if (!key) return;
    event.preventDefault();
    sendInput({op: "key", event: "down", key});
  }, true);
  window.addEventListener("keyup", (event) => {
    if (!lease || !captureToggle.checked) return;
    const key = canonicalKey(event.code);
    if (!key) return;
    event.preventDefault();
    sendInput({op: "key", event: "up", key});
  }, true);

  screen.addEventListener("click", () => {
    if (lease && captureToggle.checked) screen.requestPointerLock();
  });
  window.addEventListener("mousemove", (event) => {
    if (document.pointerLockElement !== screen || !lease || !captureToggle.checked) return;
    mouseX += event.movementX;
    mouseY += event.movementY;
    if (mouseScheduled) return;
    mouseScheduled = true;
    requestAnimationFrame(() => {
      const dx = Math.max(-127, Math.min(127, mouseX));
      const dy = Math.max(-127, Math.min(127, mouseY));
      mouseX -= dx; mouseY -= dy; mouseScheduled = false;
      if (dx || dy) sendInput({op: "mouse_move", dx, dy, wheel: 0});
    });
  });
  screen.addEventListener("mousedown", (event) => {
    if (!lease || !captureToggle.checked) return;
    const button = ["left", "middle", "right"][event.button];
    if (button) { event.preventDefault(); sendInput({op: "mouse_button", button, event: "down"}); }
  });
  window.addEventListener("mouseup", (event) => {
    if (!lease || !captureToggle.checked) return;
    const button = ["left", "middle", "right"][event.button];
    if (button) { event.preventDefault(); sendInput({op: "mouse_button", button, event: "up"}); }
  });
  screen.addEventListener("contextmenu", (event) => event.preventDefault());
  window.addEventListener("blur", () => { if (lease) releaseAll(); });
  document.addEventListener("visibilitychange", () => { if (document.hidden && lease) releaseAll(); });
  window.addEventListener("pagehide", () => { if (lease) void releaseAll(); });
})();
