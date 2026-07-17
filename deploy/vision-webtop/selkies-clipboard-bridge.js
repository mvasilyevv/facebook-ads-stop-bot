(() => {
  "use strict";

  const ua = navigator.userAgent || "";
  const isDesktopChromium = /(?:Chrome|Chromium|Edg)\//.test(ua)
    && !/(?:CriOS|EdgiOS|OPiOS)\//.test(ua);
  if (isDesktopChromium) return;

  const REPLAY_MARKER = "__adpulseClipboardReplay";
  const REPLAY_DELAY_MS = 50;
  const FALLBACK_DELAY_MS = 750;
  const MODIFIER_CODES = new Set([
    "ControlLeft",
    "ControlRight",
    "MetaLeft",
    "MetaRight",
  ]);

  let pendingPaste = null;

  function isPageFormField() {
    const active = document.activeElement;
    return Boolean(active
      && active.id !== "overlayInput"
      && (active.tagName === "INPUT"
        || active.tagName === "TEXTAREA"
        || active.tagName === "SELECT"
        || active.isContentEditable));
  }

  function snapshotKeyboardEvent(event) {
    return {
      key: event.key,
      code: event.code,
      location: event.location,
      ctrlKey: event.ctrlKey,
      metaKey: event.metaKey,
      altKey: event.altKey,
      shiftKey: event.shiftKey,
    };
  }

  function dispatchKeyboardEvent(type, source) {
    const replay = new KeyboardEvent(type, {
      ...source,
      repeat: false,
      bubbles: true,
      cancelable: true,
    });
    Object.defineProperty(replay, REPLAY_MARKER, { value: true });
    window.dispatchEvent(replay);
  }

  function dispatchPasteChord(source) {
    if (source) {
      dispatchKeyboardEvent("keydown", source);
      dispatchKeyboardEvent("keyup", source);
      return;
    }

    const control = {
      key: "Control",
      code: "ControlLeft",
      location: 1,
      ctrlKey: true,
      metaKey: false,
      altKey: false,
      shiftKey: false,
    };
    const keyV = {
      key: "v",
      code: "KeyV",
      location: 0,
      ctrlKey: true,
      metaKey: false,
      altKey: false,
      shiftKey: false,
    };
    dispatchKeyboardEvent("keydown", control);
    dispatchKeyboardEvent("keydown", keyV);
    dispatchKeyboardEvent("keyup", keyV);
    dispatchKeyboardEvent("keyup", { ...control, ctrlKey: false });
  }

  function finishPendingPaste() {
    const pending = pendingPaste;
    if (!pending) return;

    pendingPaste = null;
    clearTimeout(pending.fallbackTimer);
    dispatchPasteChord(pending.keyV);
    for (const modifierKeyUp of pending.modifierKeyUps) {
      dispatchKeyboardEvent("keyup", modifierKeyUp);
    }
  }

  window.addEventListener("keydown", (event) => {
    if (event[REPLAY_MARKER] || isPageFormField()) return;
    if (event.code !== "KeyV" || (!event.ctrlKey && !event.metaKey) || event.altKey) return;

    // Keep the browser default action so Safari/WebKit still emits `paste`.
    // Stopping propagation is enough to hold the remote key event for replay.
    event.stopImmediatePropagation();

    if (pendingPaste) {
      clearTimeout(pendingPaste.fallbackTimer);
    }
    pendingPaste = {
      keyV: snapshotKeyboardEvent(event),
      modifierKeyUps: [],
      fallbackTimer: setTimeout(finishPendingPaste, FALLBACK_DELAY_MS),
    };
  }, true);

  window.addEventListener("keyup", (event) => {
    if (event[REPLAY_MARKER] || !pendingPaste) return;
    if (event.code !== "KeyV" && !MODIFIER_CODES.has(event.code)) return;

    event.stopImmediatePropagation();
    if (MODIFIER_CODES.has(event.code)) {
      pendingPaste.modifierKeyUps.push(snapshotKeyboardEvent(event));
    }
  }, true);

  window.addEventListener("paste", (event) => {
    if (isPageFormField()) return;

    const text = event.clipboardData?.getData("text/plain") || "";
    if (!text) return;

    window.postMessage({ type: "clipboardUpdateFromUI", text }, window.location.origin);
    setTimeout(() => {
      if (pendingPaste) {
        finishPendingPaste();
      } else {
        dispatchPasteChord(null);
      }
    }, REPLAY_DELAY_MS);
  }, true);
})();
