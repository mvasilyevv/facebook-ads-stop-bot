// Синхронизация темы Telegram Mini App с CSS-переменными
// Fallback — тёмная тема (соответствует большинству Telegram-клиентов)
const DARK_FALLBACK = {
  bg_color: "#1c1c1e",
  text_color: "#ffffff",
  hint_color: "#8e8e93",
  link_color: "#0a84ff",
  button_color: "#0a84ff",
  button_text_color: "#ffffff",
  secondary_bg_color: "#2c2c2e",
};

function applyThemeParams(params) {
  const p = params || {};
  const root = document.documentElement;

  root.style.setProperty("--tg-bg-color", p.bg_color || DARK_FALLBACK.bg_color);
  root.style.setProperty("--tg-text-color", p.text_color || DARK_FALLBACK.text_color);
  root.style.setProperty("--tg-hint-color", p.hint_color || DARK_FALLBACK.hint_color);
  root.style.setProperty("--tg-link-color", p.link_color || DARK_FALLBACK.link_color);
  root.style.setProperty("--tg-button-color", p.button_color || DARK_FALLBACK.button_color);
  root.style.setProperty("--tg-button-text-color", p.button_text_color || DARK_FALLBACK.button_text_color);
  root.style.setProperty("--tg-secondary-bg-color", p.secondary_bg_color || DARK_FALLBACK.secondary_bg_color);
}

export function initTheme() {
  const tg = window.Telegram?.WebApp;

  if (tg) {
    // Применяем текущую тему
    applyThemeParams(tg.themeParams);
    // Подписываемся на изменения темы (переключение светлая/тёмная в клиенте)
    tg.onEvent("themeChanged", () => applyThemeParams(tg.themeParams));
  } else {
    // Запуск вне Telegram — применяем тёмный fallback
    applyThemeParams(null);
  }
}

// Хелпер для haptic feedback
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
