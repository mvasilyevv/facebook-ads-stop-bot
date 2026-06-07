/**
 * tg.ts — обёртка над Telegram WebApp API.
 *
 * Все вызовы безопасны: при отсутствии window.Telegram (браузер / тесты)
 * функции возвращают fallback или ничего не делают. Экспортирует только
 * стабильный публичный контракт — не трогай TG-объект напрямую.
 */

// ─── Тип из Telegram WebApp SDK (достаточно для нашего использования) ─────

interface TelegramWebApp {
  initData: string;
  initDataUnsafe: {
    user?: {
      id: number;
      first_name?: string;
      last_name?: string;
      username?: string;
      language_code?: string;
    };
    query_id?: string;
    start_param?: string;
    auth_date?: number;
    hash?: string;
  };
  themeParams: {
    bg_color?: string;
    text_color?: string;
    hint_color?: string;
    link_color?: string;
    button_color?: string;
    button_text_color?: string;
    secondary_bg_color?: string;
  };
  BackButton: {
    isVisible: boolean;
    show(): void;
    hide(): void;
    onClick(fn: () => void): void;
    offClick(fn: () => void): void;
  };
  HapticFeedback: {
    impactOccurred(style: "light" | "medium" | "heavy" | "rigid" | "soft"): void;
    notificationOccurred(type: "error" | "success" | "warning"): void;
    selectionChanged(): void;
  };
  MainButton: {
    show(): void;
    hide(): void;
    setText(text: string): void;
    onClick(fn: () => void): void;
    offClick(fn: () => void): void;
    isVisible: boolean;
  };
  viewportHeight: number;
  viewportStableHeight: number;
  isExpanded: boolean;
  expand(): void;
  close(): void;
  ready(): void;
  onEvent(eventType: string, fn: () => void): void;
  offEvent(eventType: string, fn: () => void): void;
  showAlert(message: string, callback?: () => void): void;
  showConfirm(message: string, callback: (confirmed: boolean) => void): void;
  openLink(url: string, options?: { try_instant_view?: boolean }): void;
  openTelegramLink(url: string): void;
}

function getTg(): TelegramWebApp | undefined {
  return (window as unknown as { Telegram?: { WebApp?: TelegramWebApp } }).Telegram?.WebApp;
}

// ─── Инициализация темы ───────────────────────────────────────────────────

/**
 * Синхронизирует тему Telegram с editorial-monochrome (наш design canon).
 * Мы ИГНОРИРУЕМ themeParams TG — используем собственные CSS-токены (#0a0a0b).
 * Но вызываем tg.ready() и expand() — обязательно для Mini App.
 */
export function initTheme(): void {
  const tg = getTg();
  if (!tg) return;
  tg.ready();
  tg.expand();
}

// ─── Haptic feedback ─────────────────────────────────────────────────────

export const haptic = {
  /** Тактильный удар (при нажатии кнопок действий). */
  impact(style: "light" | "medium" | "heavy" | "rigid" | "soft" = "medium"): void {
    try {
      getTg()?.HapticFeedback.impactOccurred(style);
    } catch {
      // Если HapticFeedback недоступен (Android < 10, Desktop) — молча игнорируем.
    }
  },
  /** Уведомление (success / error / warning). */
  notify(type: "success" | "error" | "warning" = "success"): void {
    try {
      getTg()?.HapticFeedback.notificationOccurred(type);
    } catch {
      /* best-effort: HapticFeedback недоступен вне Telegram */
    }
  },
  /** Лёгкий клик при переключении фильтра / чипа. */
  selection(): void {
    try {
      getTg()?.HapticFeedback.selectionChanged();
    } catch {
      /* best-effort: HapticFeedback недоступен вне Telegram */
    }
  },
} as const;

// ─── Нативные диалоги ─────────────────────────────────────────────────────

/**
 * Нативный Telegram confirm. Fallback → window.confirm (браузер / dev).
 * Всегда асинхронный — возвращает Promise<boolean>.
 */
export function tgConfirm(message: string): Promise<boolean> {
  return new Promise((resolve) => {
    const tg = getTg();
    if (tg?.showConfirm) {
      tg.showConfirm(message, resolve);
    } else {
      resolve(window.confirm(message));
    }
  });
}

/**
 * Нативный Telegram alert. Fallback → window.alert.
 */
export function tgAlert(message: string): Promise<void> {
  return new Promise((resolve) => {
    const tg = getTg();
    if (tg?.showAlert) {
      tg.showAlert(message, resolve);
    } else {
      window.alert(message);
      resolve();
    }
  });
}

// ─── Ссылки ───────────────────────────────────────────────────────────────

/** Открывает внешнюю ссылку через TG или новую вкладку (fallback). */
export function openLink(url: string): void {
  const tg = getTg();
  if (tg?.openLink) {
    tg.openLink(url);
  } else {
    window.open(url, "_blank", "noopener,noreferrer");
  }
}

/** Открывает ссылку t.me (Telegram Deep Link). */
export function openTelegramLink(url: string): void {
  const tg = getTg();
  if (tg?.openTelegramLink) {
    tg.openTelegramLink(url);
  } else {
    window.open(url, "_blank", "noopener,noreferrer");
  }
}

// ─── BackButton ───────────────────────────────────────────────────────────

/**
 * Управление Telegram BackButton.
 * Возвращает cleanup-функцию для useEffect.
 */
export function registerBackButton(onBack: () => void): () => void {
  const tg = getTg();
  if (!tg) return () => {};
  tg.BackButton.show();
  tg.BackButton.onClick(onBack);
  return () => {
    tg.BackButton.offClick(onBack);
    tg.BackButton.hide();
  };
}

/** Скрыть BackButton без callback (при переходе на root-вкладку). */
export function hideBackButton(): void {
  getTg()?.BackButton.hide();
}

// ─── Viewport ─────────────────────────────────────────────────────────────

/** Стабильная высота viewport (без появляющейся клавиатуры). */
export function getStableViewportHeight(): number {
  const tg = getTg();
  return tg?.viewportStableHeight ?? window.innerHeight;
}

// ─── initData ────────────────────────────────────────────────────────────

/** Сырая строка initData (нужна для POST /tma/auth). */
export function getInitData(): string {
  return getTg()?.initData ?? "";
}

/** Данные пользователя из initDataUnsafe (НЕ для security-проверок — только UI). */
export function getTgUser() {
  return getTg()?.initDataUnsafe?.user ?? null;
}
