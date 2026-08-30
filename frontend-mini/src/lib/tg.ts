/**
 * tg.ts — обёртка над Telegram WebApp API.
 *
 * Production-shell — только Telegram Mini App. При отсутствии Telegram API
 * money-confirmation закрывается fail-closed; браузерные native-dialog
 * подмены не используются.
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
    impactOccurred(
      style: "light" | "medium" | "heavy" | "rigid" | "soft",
    ): void;
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
    /** Не во всех клиентах — feature-detect перед вызовом. */
    enable?(): void;
    disable?(): void;
    showProgress?(leaveActive?: boolean): void;
    hideProgress?(): void;
  };
  viewportHeight: number;
  viewportStableHeight: number;
  safeAreaInset?: TelegramSafeArea;
  contentSafeAreaInset?: TelegramSafeArea;
  isExpanded: boolean;
  expand(): void;
  close(): void;
  ready(): void;
  setHeaderColor(color: string): void;
  setBackgroundColor(color: string): void;
  setBottomBarColor?(color: string): void;
  onEvent(eventType: string, fn: () => void): void;
  offEvent(eventType: string, fn: () => void): void;
  showAlert(message: string, callback?: () => void): void;
  showConfirm(message: string, callback: (confirmed: boolean) => void): void;
  openLink(url: string, options?: { try_instant_view?: boolean }): void;
  openTelegramLink(url: string): void;
}

interface TelegramSafeArea {
  top: number;
  bottom: number;
  left: number;
  right: number;
}

function getTg(): TelegramWebApp | undefined {
  return (window as unknown as { Telegram?: { WebApp?: TelegramWebApp } })
    .Telegram?.WebApp;
}

// ─── Инициализация темы ───────────────────────────────────────────────────

/**
 * Синхронизирует тему Telegram с editorial-monochrome (наш design canon).
 * Мы ИГНОРИРУЕМ themeParams TG — используем собственные CSS-токены «Точного журнала».
 * Но вызываем tg.ready() и expand() — обязательно для Mini App.
 */
export function initTheme(): () => void {
  const tg = getTg();
  document.documentElement.dataset.theme = "dark";
  document.documentElement.style.colorScheme = "dark";
  if (!tg) {
    syncViewportCss();
    return () => {};
  }
  tg.setHeaderColor?.("#0b0d10");
  tg.setBackgroundColor?.("#0b0d10");
  tg.setBottomBarColor?.("#0e1114");
  tg.ready();
  tg.expand();
  syncViewportCss();

  const events = [
    "viewportChanged",
    "safeAreaChanged",
    "contentSafeAreaChanged",
    "activated",
  ];
  events.forEach((event) => tg.onEvent(event, syncViewportCss));
  return () => events.forEach((event) => tg.offEvent(event, syncViewportCss));
}

function syncViewportCss(): void {
  const tg = getTg();
  const root = document.documentElement;
  root.style.setProperty(
    "--tg-viewport-stable-height",
    `${Math.max(tg?.viewportStableHeight ?? window.innerHeight, 1)}px`,
  );
  setInsets(root, "--tg-safe", tg?.safeAreaInset);
  setInsets(
    root,
    "--tg-content-safe",
    tg?.contentSafeAreaInset ?? tg?.safeAreaInset,
  );
}

function setInsets(
  root: HTMLElement,
  prefix: string,
  inset?: TelegramSafeArea,
): void {
  if (!inset) return;
  root.style.setProperty(`${prefix}-top`, `${Math.max(inset.top, 0)}px`);
  root.style.setProperty(`${prefix}-bottom`, `${Math.max(inset.bottom, 0)}px`);
  root.style.setProperty(`${prefix}-left`, `${Math.max(inset.left, 0)}px`);
  root.style.setProperty(`${prefix}-right`, `${Math.max(inset.right, 0)}px`);
}

// ─── Haptic feedback ─────────────────────────────────────────────────────

export const haptic = {
  /** Тактильный удар (при нажатии кнопок действий). */
  impact(
    style: "light" | "medium" | "heavy" | "rigid" | "soft" = "medium",
  ): void {
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
 * Нативный Telegram confirm. В неподдерживаемом shell возвращает false.
 */
export function tgConfirm(message: string): Promise<boolean> {
  return new Promise((resolve) => {
    const tg = getTg();
    if (tg?.showConfirm) {
      tg.showConfirm(message, resolve);
    } else {
      resolve(false);
    }
  });
}

/**
 * Нативный Telegram alert. В неподдерживаемом shell ничего не показывает:
 * AuthGuard не допускает такой shell до product routes.
 */
export function tgAlert(message: string): Promise<void> {
  return new Promise((resolve) => {
    const tg = getTg();
    if (tg?.showAlert) {
      tg.showAlert(message, resolve);
    } else {
      resolve();
    }
  });
}

// ─── Ссылки ───────────────────────────────────────────────────────────────

/** Открывает внешнюю ссылку только через подтверждённый Telegram shell. */
export function openLink(url: string): void {
  getTg()?.openLink(url);
}

/** Открывает ссылку t.me (Telegram Deep Link). */
export function openTelegramLink(url: string): void {
  getTg()?.openTelegramLink(url);
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

// ─── MainButton ───────────────────────────────────────────────────────────

export type TelegramMainButtonHandle = TelegramWebApp["MainButton"];

/**
 * Низкоуровневый доступ к Telegram MainButton. `undefined` вне Telegram —
 * вызывающий код обязан остаться на своей fallback-кнопке (fail-closed).
 * Используется только из `useTelegramMainButton`.
 */
export function getMainButton(): TelegramMainButtonHandle | undefined {
  return getTg()?.MainButton;
}

// ─── Vertical swipes (pull-to-refresh vs нативное закрытие) ───────────────

/**
 * Отключает нативный жест Telegram «смахнуть вниз чтобы закрыть» (Bot API
 * 7.7+). Не во всех клиентах доступно — feature-detect и no-op иначе.
 */
export function disableVerticalSwipes(): void {
  (
    getTg() as unknown as { disableVerticalSwipes?(): void } | undefined
  )?.disableVerticalSwipes?.();
}

/** Возвращает нативный жест закрытия (пара к {@link disableVerticalSwipes}). */
export function enableVerticalSwipes(): void {
  (
    getTg() as unknown as { enableVerticalSwipes?(): void } | undefined
  )?.enableVerticalSwipes?.();
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

/** Opaque launch capability only; backend still binds it to the verified principal. */
export function getTgStartParam(): string | null {
  const value = getTg()?.initDataUnsafe?.start_param;
  return typeof value === "string" && /^[A-Za-z0-9_-]{22}$/.test(value)
    ? value
    : null;
}
