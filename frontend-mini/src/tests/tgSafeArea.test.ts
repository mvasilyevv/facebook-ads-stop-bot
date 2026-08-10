import { afterEach, describe, expect, it, vi } from "vitest";

import { initTheme, tgAlert, tgConfirm } from "@/lib/tg";

describe("Telegram viewport and content safe areas", () => {
  afterEach(() => {
    delete (window as typeof window & { Telegram?: unknown }).Telegram;
    document.documentElement.removeAttribute("style");
    vi.restoreAllMocks();
  });

  it("tracks stable height and all four content insets across activation events", () => {
    const listeners = new Map<string, () => void>();
    const offEvent = vi.fn();
    const webApp = {
      initData: "",
      initDataUnsafe: {},
      themeParams: {},
      BackButton: {
        isVisible: false,
        show: vi.fn(),
        hide: vi.fn(),
        onClick: vi.fn(),
        offClick: vi.fn(),
      },
      HapticFeedback: {
        impactOccurred: vi.fn(),
        notificationOccurred: vi.fn(),
        selectionChanged: vi.fn(),
      },
      MainButton: {
        isVisible: false,
        show: vi.fn(),
        hide: vi.fn(),
        setText: vi.fn(),
        onClick: vi.fn(),
        offClick: vi.fn(),
      },
      viewportHeight: 680,
      viewportStableHeight: 700,
      safeAreaInset: { top: 10, right: 11, bottom: 12, left: 13 },
      contentSafeAreaInset: { top: 20, right: 21, bottom: 22, left: 23 },
      isExpanded: false,
      expand: vi.fn(),
      close: vi.fn(),
      ready: vi.fn(),
      setHeaderColor: vi.fn(),
      setBackgroundColor: vi.fn(),
      setBottomBarColor: vi.fn(),
      onEvent: vi.fn((event: string, callback: () => void) =>
        listeners.set(event, callback),
      ),
      offEvent,
      showAlert: vi.fn(),
      showConfirm: vi.fn(),
      openLink: vi.fn(),
      openTelegramLink: vi.fn(),
    };
    (window as typeof window & { Telegram?: unknown }).Telegram = {
      WebApp: webApp,
    };

    const cleanup = initTheme();
    const style = document.documentElement.style;
    expect(style.getPropertyValue("--tg-viewport-stable-height")).toBe("700px");
    expect(style.getPropertyValue("--tg-content-safe-top")).toBe("20px");
    expect(style.getPropertyValue("--tg-content-safe-right")).toBe("21px");
    expect(style.getPropertyValue("--tg-content-safe-bottom")).toBe("22px");
    expect(style.getPropertyValue("--tg-content-safe-left")).toBe("23px");
    expect(listeners.has("activated")).toBe(true);

    webApp.viewportStableHeight = 640;
    webApp.contentSafeAreaInset = { top: 30, right: 31, bottom: 32, left: 33 };
    listeners.get("activated")?.();
    expect(style.getPropertyValue("--tg-viewport-stable-height")).toBe("640px");
    expect(style.getPropertyValue("--tg-content-safe-top")).toBe("30px");
    expect(style.getPropertyValue("--tg-content-safe-right")).toBe("31px");
    expect(style.getPropertyValue("--tg-content-safe-bottom")).toBe("32px");
    expect(style.getPropertyValue("--tg-content-safe-left")).toBe("33px");

    cleanup();
    expect(offEvent).toHaveBeenCalledTimes(4);
  });

  it("fails money confirmation closed without browser-native dialog fallbacks", async () => {
    const browserConfirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const browserAlert = vi.spyOn(window, "alert").mockImplementation(() => {});

    await expect(tgConfirm("Отключить объявление?")).resolves.toBe(false);
    await expect(tgAlert("Ошибка команды")).resolves.toBeUndefined();

    expect(browserConfirm).not.toHaveBeenCalled();
    expect(browserAlert).not.toHaveBeenCalled();
  });
});
