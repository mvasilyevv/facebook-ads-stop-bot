import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Моки навигации и data-хуков (палитра не должна ходить в сеть в тестах).
const navigateMock = vi.fn();
const useOperatorAdsMock = vi.fn();
vi.mock("@tanstack/react-router", () => ({ useNavigate: () => navigateMock }));
vi.mock("@/lib/api/offers", () => ({ useOffers: () => ({ data: [] }) }));
vi.mock("@/lib/api/operator", () => ({
  useOperatorAds: (...args: unknown[]) => useOperatorAdsMock(...args),
}));

import { CommandPalette } from "@/components/layout/CommandPalette";
import { useCommandPalette } from "@/stores/commandPalette";

describe("CommandPalette", () => {
  beforeEach(() => {
    navigateMock.mockClear();
    useOperatorAdsMock.mockReset();
    useOperatorAdsMock.mockReturnValue({ data: { rows: [] } });
    useCommandPalette.setState({ open: false });
  });

  // Закрыта по умолчанию — поля поиска нет в DOM
  it("закрыта по умолчанию", () => {
    render(<CommandPalette />);
    expect(screen.queryByLabelText("Поиск")).toBeNull();
  });

  // ⌘K открывает палитру (глобальный хоткей)
  it("⌘K открывает палитру", async () => {
    render(<CommandPalette />);
    await userEvent.keyboard("{Meta>}k{/Meta}");
    expect(screen.getByLabelText("Поиск")).toBeInTheDocument();
  });

  // Открытая палитра показывает разделы навигации
  it("показывает разделы навигации", () => {
    useCommandPalette.setState({ open: true });
    render(<CommandPalette />);
    expect(screen.getByText("Обзор")).toBeInTheDocument();
    expect(screen.getByText("Настройки")).toBeInTheDocument();
  });

  // Ввод запроса фильтрует список разделов
  it("фильтрует разделы по запросу", async () => {
    useCommandPalette.setState({ open: true });
    render(<CommandPalette />);
    await userEvent.type(screen.getByLabelText("Поиск"), "настр");
    expect(screen.getByText("Настройки")).toBeInTheDocument();
    expect(screen.queryByText("Обзор")).toBeNull();
  });

  // Enter по первому результату вызывает navigate на нужный раздел
  it("Enter навигирует на выбранный раздел", async () => {
    useCommandPalette.setState({ open: true });
    render(<CommandPalette />);
    await userEvent.type(screen.getByLabelText("Поиск"), "объяв");
    await userEvent.keyboard("{Enter}");
    expect(navigateMock).toHaveBeenCalledWith({ to: "/ads" });
  });

  it("ищет объявления только через typed operator endpoint", async () => {
    useOperatorAdsMock.mockReturnValue({
      data: {
        rows: [
          {
            fb_ad_id: "238500000000001",
            name: "GH control",
          },
        ],
      },
    });
    useCommandPalette.setState({ open: true });
    render(<CommandPalette />);

    await userEvent.type(screen.getByLabelText("Поиск"), "GH");

    expect(useOperatorAdsMock).toHaveBeenLastCalledWith(
      { search: "gh", page: 1, page_size: 10 },
      { enabled: true },
    );
    expect(screen.getByText("GH control")).toBeInTheDocument();
  });
});
