// Синхронизация темы Telegram Mini App с CSS-переменными
// Fallback — Neo Control Room
const OPS_FALLBACK = {
  bg_color: "#0E1116",
  text_color: "#E8EBEE",
  hint_color: "#8A929D",
  link_color: "#FF6B00",
  button_color: "#FF6B00",
  button_text_color: "#0E1116",
  secondary_bg_color: "#14181E",
};

const DARK_FALLBACK = OPS_FALLBACK;

function applyThemeParams(params) {
  const p = params || {};
  const root = document.documentElement;

  root.style.setProperty("--tg-bg-color", p.bg_color || OPS_FALLBACK.bg_color);
  root.style.setProperty("--tg-text-color", p.text_color || OPS_FALLBACK.text_color);
  root.style.setProperty("--tg-hint-color", p.hint_color || OPS_FALLBACK.hint_color);
  root.style.setProperty("--tg-link-color", p.link_color || OPS_FALLBACK.link_color);
  root.style.setProperty("--tg-button-color", p.button_color || OPS_FALLBACK.button_color);
  root.style.setProperty("--tg-button-text-color", p.button_text_color || OPS_FALLBACK.button_text_color);
  root.style.setProperty("--tg-secondary-bg-color", p.secondary_bg_color || OPS_FALLBACK.secondary_bg_color);

  root.style.setProperty("--ops-accent", "#FF6B00");
  root.style.setProperty("--ops-accent-muted", "rgba(255, 107, 0, 0.13)");
}

export function initTheme() {
  const tg = window.Telegram?.WebApp;

  if (tg) {
    applyThemeParams(tg.themeParams);
    tg.onEvent("themeChanged", () => applyThemeParams(tg.themeParams));
  } else {
    applyThemeParams(null);
  }
}

export const haptic = {
  impact(style = "medium") {
    try {
      window.Telegram?.WebApp?.HapticFeedback?.impactOccurred(style);
    } catch {}
  },
  notify(type = "success") {
    try {
      window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred(type);
    } catch {}
  },
  selection() {
    try {
      window.Telegram?.WebApp?.HapticFeedback?.selectionChanged();
    } catch {}
  },
};
