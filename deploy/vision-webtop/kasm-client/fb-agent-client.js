import UI from "./ui.js";

const MAX_CLIPBOARD_BYTES = 256 * 1024;
const CLIPBOARD_MIME = "text/plain";
const encoder = new TextEncoder();

function truncateUtf8(value, maxBytes = MAX_CLIPBOARD_BYTES) {
  if (encoder.encode(value).byteLength <= maxBytes) return value;
  let low = 0;
  let high = value.length;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    if (encoder.encode(value.slice(0, middle)).byteLength <= maxBytes)
      low = middle;
    else high = middle - 1;
  }
  if (low > 0 && /[\uD800-\uDBFF]/.test(value[low - 1])) low -= 1;
  return value.slice(0, low);
}

function button(label, action, className = "") {
  const element = document.createElement("button");
  element.type = "button";
  element.className = `fb-desktop-action ${className}`.trim();
  element.dataset.action = action;
  element.textContent = label;
  return element;
}

function applyResizeMode(mode) {
  const select = document.getElementById("noVNC_setting_resize");
  if (!(select instanceof HTMLSelectElement)) return;
  select.value = mode;
  select.dispatchEvent(new Event("change", { bubbles: true }));
  if (mode === "off" && UI.rfb) UI.rfb.localScale = 1;
}

function upstreamClick(id) {
  const target = document.getElementById(id);
  if (target instanceof HTMLElement) target.click();
}

function failClosed() {
  document.documentElement.dataset.fbDesktopState = "denied";
  const message = document.createElement("main");
  message.className = "fb-desktop-denied";
  message.innerHTML =
    "<h1>Сессия недоступна</h1><p>Вернитесь в FB Agent и откройте рабочий стол заново.</p>";
  document.body.replaceChildren(message);
}

async function loadProfile() {
  const response = await fetch("/desktop-auth/profile", {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  if (!response.ok) throw new Error("desktop_session_required");
  const payload = await response.json();
  if (
    payload?.presentation !== "desktop" &&
    payload?.presentation !== "mobile"
  ) {
    throw new Error("desktop_profile_invalid");
  }
  return payload.presentation;
}

function installClipboardLimit() {
  const textarea = document.getElementById("noVNC_clipboard_text");
  if (!(textarea instanceof HTMLTextAreaElement)) return;
  textarea.dataset.clipboardMime = CLIPBOARD_MIME;
  textarea.setAttribute("aria-label", "Буфер обмена, максимум 256 КиБ");
  const enforce = () => {
    const safe = truncateUtf8(textarea.value);
    if (safe !== textarea.value) {
      textarea.value = safe;
      textarea.setCustomValidity("Максимальный размер текста — 256 КиБ");
      textarea.reportValidity();
    } else {
      textarea.setCustomValidity("");
    }
  };
  textarea.addEventListener("beforeinput", enforce);
  textarea.addEventListener("input", enforce);
  textarea.addEventListener("change", enforce, { capture: true });
}

function installToolbar(presentation) {
  let interactionMode = "cursor";
  let screenMode = presentation === "mobile" ? "scale" : "off";
  let pinchDistance = 0;
  let pinchScale = 1;
  let appliedRfb = null;

  const toolbar = document.createElement("nav");
  toolbar.id = "fb-desktop-toolbar";
  toolbar.setAttribute("aria-label", "Управление рабочим столом");

  const screen = button("Экран", "screen");
  const interaction = button("Курсор", "interaction");
  const keyboard = button("Клавиатура", "keyboard");
  const clipboard = button("Буфер", "clipboard");
  const fullscreen = button("На весь экран", "fullscreen");
  const logout = button("Выйти", "logout", "fb-desktop-danger");
  toolbar.append(screen, interaction, keyboard, clipboard, fullscreen, logout);

  const options = document.createElement("div");
  options.id = "fb-desktop-options";
  options.hidden = true;
  options.innerHTML = `
    <fieldset>
      <legend>Масштаб</legend>
      <label><input type="radio" name="fb-scale" value="scale"> Fit</label>
      <label><input type="radio" name="fb-scale" value="off"> 100%</label>
    </fieldset>
    <label class="fb-desktop-switch"><input type="checkbox" id="fb-literal-linux"> Literal Linux</label>
  `;
  document.body.append(options, toolbar);

  const selectedScale = options.querySelector(
    `input[name="fb-scale"][value="${screenMode}"]`,
  );
  if (selectedScale instanceof HTMLInputElement) selectedScale.checked = true;

  function applyInteraction() {
    if (!UI.rfb) return;
    if (interactionMode === "navigation") {
      UI.rfb.clipViewport = true;
      UI.rfb.dragViewport = true;
    } else {
      UI.rfb.dragViewport = false;
      applyResizeMode(screenMode);
    }
    interaction.textContent =
      interactionMode === "navigation" ? "Навигация" : "Курсор";
    interaction.setAttribute(
      "aria-pressed",
      String(interactionMode === "navigation"),
    );
  }

  applyResizeMode(screenMode);
  UI.forceSetting("reconnect", true);
  UI.forceSetting("reconnect_delay", 1000);
  UI.forceSetting("translate_shortcuts", true);

  screen.addEventListener("click", () => {
    options.hidden = !options.hidden;
    screen.setAttribute("aria-expanded", String(!options.hidden));
  });
  options.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    if (target.name === "fb-scale") {
      screenMode = target.value === "scale" ? "scale" : "off";
      if (interactionMode === "cursor") applyResizeMode(screenMode);
    }
    if (target.id === "fb-literal-linux") {
      UI.forceSetting("translate_shortcuts", !target.checked);
      if (UI.rfb) UI.updateShortcutTranslation();
    }
  });
  interaction.addEventListener("click", () => {
    interactionMode = interactionMode === "cursor" ? "navigation" : "cursor";
    if (interactionMode === "navigation") applyResizeMode("off");
    applyInteraction();
  });
  keyboard.addEventListener("click", () =>
    upstreamClick("noVNC_keyboard_button"),
  );
  clipboard.addEventListener("click", () =>
    upstreamClick("noVNC_clipboard_button"),
  );
  fullscreen.addEventListener("click", () =>
    upstreamClick("noVNC_fullscreen_button"),
  );
  logout.addEventListener("click", () => {
    const form = document.createElement("form");
    form.method = "post";
    form.action = "/desktop/logout";
    document.body.append(form);
    form.submit();
  });

  const container = document.getElementById("noVNC_container");
  if (container) {
    container.addEventListener(
      "touchstart",
      (event) => {
        if (
          interactionMode !== "navigation" ||
          event.touches.length !== 2 ||
          !UI.rfb
        )
          return;
        const [first, second] = event.touches;
        pinchDistance = Math.hypot(
          second.clientX - first.clientX,
          second.clientY - first.clientY,
        );
        pinchScale = UI.rfb.localScale;
        event.preventDefault();
        event.stopPropagation();
      },
      { passive: false, capture: true },
    );
    container.addEventListener(
      "touchmove",
      (event) => {
        if (
          interactionMode !== "navigation" ||
          event.touches.length !== 2 ||
          !UI.rfb
        )
          return;
        const [first, second] = event.touches;
        const distance = Math.hypot(
          second.clientX - first.clientX,
          second.clientY - first.clientY,
        );
        if (pinchDistance > 0) {
          UI.rfb.localScale = Math.min(
            2,
            Math.max(0.25, pinchScale * (distance / pinchDistance)),
          );
        }
        event.preventDefault();
        event.stopPropagation();
      },
      { passive: false, capture: true },
    );
  }

  const observer = new MutationObserver(() => {
    if (UI.rfb && UI.rfb !== appliedRfb) {
      appliedRfb = UI.rfb;
      applyInteraction();
    }
  });
  observer.observe(document.documentElement, {
    attributes: true,
    childList: true,
    subtree: true,
  });
  if (UI.rfb) {
    appliedRfb = UI.rfb;
    applyInteraction();
  }
}

window.addEventListener("load", async () => {
  try {
    const presentation = await loadProfile();
    document.documentElement.dataset.fbDesktopPresentation = presentation;
    installClipboardLimit();
    installToolbar(presentation);
  } catch {
    failClosed();
  }
});

export { CLIPBOARD_MIME, MAX_CLIPBOARD_BYTES, truncateUtf8 };
