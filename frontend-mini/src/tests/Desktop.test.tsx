import { render, screen } from "@testing-library/react";
import type { ComponentType } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const useQuery = vi.hoisted(() => vi.fn());
const impact = vi.hoisted(() => vi.fn());
const notify = vi.hoisted(() => vi.fn());

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => options,
}));

vi.mock("@/lib/auth", () => ({
  tmaApi: { useQuery: (...args: unknown[]) => useQuery(...args) },
}));
vi.mock("@/lib/tg", () => ({
  haptic: { impact, notify },
}));

import { Route } from "@/routes/desktop/index";

const RemoteDesktopPage = (Route as unknown as { component: ComponentType }).component;

function channel(overrides: Record<string, unknown> = {}) {
  return {
    data: {
      available: true,
      server: "100.73.162.127",
      key: "QJztruGKKjvEcX9XBLMixf21wieLGYABEaWby97JP5s=",
      device_id: "253474910",
      ...overrides,
    },
    isPending: false,
    isError: false,
    refetch: vi.fn(),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  useQuery.mockReturnValue(channel());
});

describe("Mini App RemoteDesktopPage", () => {
  it("ведёт в нативное приложение и показывает значения для ручного ввода", () => {
    render(<RemoteDesktopPage />);

    expect(screen.getByRole("link", { name: /Открыть в приложении/ })).toHaveAttribute(
      "href",
      "rustdesk://253474910",
    );
    expect(screen.getByText("253474910")).toBeInTheDocument();
    expect(screen.getByText("100.73.162.127")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Скопировать: ID стола" })).toBeInTheDocument();
  });

  it("живёт тем же эндпоинтом, что и веб — /api/desktop/native", () => {
    render(<RemoteDesktopPage />);

    expect(useQuery).toHaveBeenCalledWith(
      "get",
      "/api/desktop/native",
      {},
      expect.objectContaining({ refetchOnMount: "always" }),
    );
  });

  it("до публикации ID честно говорит, что стол ещё поднимается", () => {
    useQuery.mockReturnValue(channel({ available: false, device_id: null }));

    render(<RemoteDesktopPage />);

    expect(screen.getByText(/ещё не опубликовал ID/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Открыть в приложении/ })).not.toBeInTheDocument();
  });

  it("при ошибке предлагает повторить", () => {
    const refetch = vi.fn();
    useQuery.mockReturnValue({ data: undefined, isPending: false, isError: true, refetch });

    render(<RemoteDesktopPage />);

    expect(screen.getByRole("alert")).toHaveTextContent("Данные канала недоступны");
    screen.getByRole("button", { name: "Повторить" }).click();
    expect(refetch).toHaveBeenCalledOnce();
  });
});
