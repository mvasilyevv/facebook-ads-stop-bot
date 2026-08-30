/**
 * PullToRefresh: жест сверху при scrollTop=0 инвалидирует снимок и бьёт
 * haptic; короткий жест или жест не от верха страницы — no-op; на время
 * жизни компонента отключается нативный вертикальный свайп Telegram.
 */
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  hapticImpact: vi.fn(),
  disableVerticalSwipes: vi.fn(),
  enableVerticalSwipes: vi.fn(),
}));

vi.mock("@/lib/tg", () => ({
  haptic: { impact: mocks.hapticImpact },
  disableVerticalSwipes: mocks.disableVerticalSwipes,
  enableVerticalSwipes: mocks.enableVerticalSwipes,
}));

import { PullToRefresh } from "@/components/layout/PullToRefresh";

function dispatchTouch(type: "touchstart" | "touchmove" | "touchend", clientY: number) {
  const touches =
    type === "touchend"
      ? []
      : [{ clientX: 0, clientY, identifier: 1 } as unknown as Touch];
  const event = new TouchEvent(type, { touches, bubbles: true, cancelable: true });
  act(() => {
    window.dispatchEvent(event);
  });
}

function setScrollY(value: number) {
  Object.defineProperty(window, "scrollY", { value, configurable: true });
}

describe("PullToRefresh", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setScrollY(0);
  });

  afterEach(() => {
    setScrollY(0);
  });

  it("рендерит children", () => {
    render(
      <PullToRefresh onRefresh={vi.fn()}>
        <div>Контент</div>
      </PullToRefresh>,
    );
    expect(screen.getByText("Контент")).toBeInTheDocument();
  });

  it("отключает нативный вертикальный свайп на монтировании и включает обратно на размонтировании", () => {
    const { unmount } = render(
      <PullToRefresh onRefresh={vi.fn()}>
        <div />
      </PullToRefresh>,
    );
    expect(mocks.disableVerticalSwipes).toHaveBeenCalledTimes(1);
    expect(mocks.enableVerticalSwipes).not.toHaveBeenCalled();
    unmount();
    expect(mocks.enableVerticalSwipes).toHaveBeenCalledTimes(1);
  });

  it("жест вниз от верха страницы за порогом вызывает onRefresh и haptic-удар", async () => {
    const onRefresh = vi.fn().mockResolvedValue(undefined);
    render(
      <PullToRefresh onRefresh={onRefresh}>
        <div />
      </PullToRefresh>,
    );

    dispatchTouch("touchstart", 0);
    dispatchTouch("touchmove", 200);
    dispatchTouch("touchend", 200);

    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
    expect(mocks.hapticImpact).toHaveBeenCalledWith("light");
  });

  it("короткий жест ниже порога не вызывает onRefresh", () => {
    const onRefresh = vi.fn();
    render(
      <PullToRefresh onRefresh={onRefresh}>
        <div />
      </PullToRefresh>,
    );

    dispatchTouch("touchstart", 0);
    dispatchTouch("touchmove", 20);
    dispatchTouch("touchend", 20);

    expect(onRefresh).not.toHaveBeenCalled();
    expect(mocks.hapticImpact).not.toHaveBeenCalled();
  });

  it("жест, начатый не от верха страницы (scrollTop > 0), игнорируется", () => {
    setScrollY(50);
    const onRefresh = vi.fn();
    render(
      <PullToRefresh onRefresh={onRefresh}>
        <div />
      </PullToRefresh>,
    );

    dispatchTouch("touchstart", 0);
    dispatchTouch("touchmove", 200);
    dispatchTouch("touchend", 200);

    expect(onRefresh).not.toHaveBeenCalled();
  });

  it("свайп вверх (delta < 0) не запускает обновление", () => {
    const onRefresh = vi.fn();
    render(
      <PullToRefresh onRefresh={onRefresh}>
        <div />
      </PullToRefresh>,
    );

    dispatchTouch("touchstart", 200);
    dispatchTouch("touchmove", 0);
    dispatchTouch("touchend", 0);

    expect(onRefresh).not.toHaveBeenCalled();
  });

  it("показывает статус «Обновляем снимок…» пока идёт обновление и снимает его после", async () => {
    let resolvePromise: () => void = () => {};
    const onRefresh = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolvePromise = resolve;
        }),
    );
    render(
      <PullToRefresh onRefresh={onRefresh}>
        <div />
      </PullToRefresh>,
    );

    dispatchTouch("touchstart", 0);
    dispatchTouch("touchmove", 200);
    dispatchTouch("touchend", 200);

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("Обновляем снимок…"),
    );

    await act(async () => {
      resolvePromise();
      await Promise.resolve();
    });

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(""));
  });
});
