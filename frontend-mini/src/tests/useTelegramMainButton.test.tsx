/**
 * useTelegramMainButton: показ/скрытие, disabled/loading, cleanup,
 * fail-closed fallback вне Telegram.
 */
import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  button: {
    show: vi.fn(),
    hide: vi.fn(),
    setText: vi.fn(),
    onClick: vi.fn(),
    offClick: vi.fn(),
    enable: vi.fn(),
    disable: vi.fn(),
    showProgress: vi.fn(),
    hideProgress: vi.fn(),
    isVisible: false,
  },
  getMainButton: vi.fn(),
}));

vi.mock("@/lib/tg", () => ({
  getMainButton: mocks.getMainButton,
}));

import { useTelegramMainButton } from "@/lib/useTelegramMainButton";

describe("useTelegramMainButton", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getMainButton.mockReturnValue(mocks.button);
  });

  it("fail-closed: available=false и никаких вызовов вне Telegram", () => {
    mocks.getMainButton.mockReturnValue(undefined);
    const { result } = renderHook(() =>
      useTelegramMainButton({ text: "Далее", onClick: vi.fn() }),
    );
    expect(result.current.available).toBe(false);
    expect(mocks.button.show).not.toHaveBeenCalled();
    expect(mocks.button.setText).not.toHaveBeenCalled();
  });

  it("показывает кнопку с текстом и включает по умолчанию", () => {
    const { result } = renderHook(() =>
      useTelegramMainButton({ text: "Далее", onClick: vi.fn() }),
    );
    expect(result.current.available).toBe(true);
    expect(mocks.button.setText).toHaveBeenCalledWith("Далее");
    expect(mocks.button.show).toHaveBeenCalledTimes(1);
    expect(mocks.button.enable).toHaveBeenCalledTimes(1);
    expect(mocks.button.hideProgress).toHaveBeenCalledTimes(1);
  });

  it("visible=false прячет кнопку и не показывает", () => {
    renderHook(() =>
      useTelegramMainButton({ text: "Далее", onClick: vi.fn(), visible: false }),
    );
    expect(mocks.button.hide).toHaveBeenCalledTimes(1);
    expect(mocks.button.show).not.toHaveBeenCalled();
  });

  it("disabled вызывает disable(), а не enable()", () => {
    renderHook(() =>
      useTelegramMainButton({ text: "Далее", onClick: vi.fn(), disabled: true }),
    );
    expect(mocks.button.disable).toHaveBeenCalledTimes(1);
    expect(mocks.button.enable).not.toHaveBeenCalled();
  });

  it("loading показывает встроенный прогресс вместо hideProgress", () => {
    renderHook(() =>
      useTelegramMainButton({ text: "Далее", onClick: vi.fn(), loading: true }),
    );
    expect(mocks.button.showProgress).toHaveBeenCalledWith(false);
    expect(mocks.button.hideProgress).not.toHaveBeenCalled();
  });

  it("переключение шага обновляет текст без перерегистрации клика", () => {
    const { rerender } = renderHook(
      ({ text }: { text: string }) =>
        useTelegramMainButton({ text, onClick: vi.fn() }),
      { initialProps: { text: "Далее" } },
    );
    expect(mocks.button.onClick).toHaveBeenCalledTimes(1);

    rerender({ text: "Запустить" });
    expect(mocks.button.setText).toHaveBeenLastCalledWith("Запустить");
    expect(mocks.button.onClick).toHaveBeenCalledTimes(1);
  });

  it("вызывает актуальный onClick без повторной регистрации обработчика", () => {
    const onClickA = vi.fn();
    const onClickB = vi.fn();
    const { rerender } = renderHook(
      ({ onClick }: { onClick: () => void }) =>
        useTelegramMainButton({ text: "Далее", onClick }),
      { initialProps: { onClick: onClickA } },
    );
    const registeredHandler = mocks.button.onClick.mock.calls[0]?.[0] as () => void;

    rerender({ onClick: onClickB });
    expect(mocks.button.onClick).toHaveBeenCalledTimes(1);

    act(() => registeredHandler());
    expect(onClickA).not.toHaveBeenCalled();
    expect(onClickB).toHaveBeenCalledTimes(1);
  });

  it("cleanup на размонтировании снимает обработчик и прячет кнопку", () => {
    const { unmount } = renderHook(() =>
      useTelegramMainButton({ text: "Далее", onClick: vi.fn() }),
    );
    const registeredHandler = mocks.button.onClick.mock.calls[0]?.[0];

    unmount();

    expect(mocks.button.offClick).toHaveBeenCalledWith(registeredHandler);
    expect(mocks.button.hide).toHaveBeenCalledTimes(1);
  });
});
