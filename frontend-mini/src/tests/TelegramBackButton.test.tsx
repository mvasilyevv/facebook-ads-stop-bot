import { act, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  historyBack: vi.fn(),
  location: { pathname: "/open" },
  handler: null as (() => void) | null,
  hideBackButton: vi.fn(),
}));

vi.mock("@tanstack/react-router", () => ({
  useLocation: () => mocks.location,
  useRouter: () => ({
    navigate: mocks.navigate,
    history: { back: mocks.historyBack },
  }),
}));

vi.mock("@/lib/tg", () => ({
  registerBackButton: (handler: () => void) => {
    mocks.handler = handler;
    return vi.fn();
  },
  hideBackButton: mocks.hideBackButton,
}));

import { TelegramBackButton } from "@/components/layout/TelegramBackButton";

describe("TelegramBackButton", () => {
  it("escapes a redeemed /open deep link to the main tab", () => {
    render(<TelegramBackButton />);

    expect(mocks.handler).not.toBeNull();
    act(() => mocks.handler?.());
    expect(mocks.navigate).toHaveBeenCalledWith({ to: "/", replace: true });
    expect(mocks.historyBack).not.toHaveBeenCalled();
  });

  it("returns from the analytics secondary screen through Telegram history", () => {
    mocks.location.pathname = "/analytics";
    mocks.handler = null;
    render(<TelegramBackButton />);

    expect(mocks.handler).not.toBeNull();
    act(() => mocks.handler?.());
    expect(mocks.historyBack).toHaveBeenCalled();
  });
});
