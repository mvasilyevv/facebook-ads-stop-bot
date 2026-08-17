import { render, screen } from "@testing-library/react";
import type { ComponentType } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const useDesktopNativeChannel = vi.fn();
const launchMutate = vi.fn();

vi.mock("@/lib/api/desktop", () => ({
  useDesktopNativeChannel: () => useDesktopNativeChannel(),
  useDesktopLaunchLink: () => ({ mutate: launchMutate, isPending: false }),
}));

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => options,
}));

import { Route } from "@/routes/remote-desktop/index";

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
  useDesktopNativeChannel.mockReturnValue(channel());
});

describe("RemoteDesktopPage", () => {
  it("забирает ссылку запуска только по нажатию и сразу открывает её", () => {
    // Ссылка несёт пароль канала. Отрендерить её в href значило бы держать
    // пароль в разметке открытого экрана — ровно то, ради чего ручку и
    // отделили от данных канала.
    const { container } = render(<RemoteDesktopPage />);

    expect(launchMutate).not.toHaveBeenCalled();
    expect(container.innerHTML).not.toContain("rustdesk://");

    screen.getByRole("button", { name: /Открыть в приложении/ }).click();

    expect(launchMutate).toHaveBeenCalledOnce();
    const [, handlers] = launchMutate.mock.calls[0]!;
    const assign = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...window.location, assign },
    });
    handlers.onSuccess({ url: "rustdesk://connection/new/253474910?password=secret" });
    expect(assign).toHaveBeenCalledWith("rustdesk://connection/new/253474910?password=secret");
  });

  it("показывает всё, что оператор вводит в клиент, с копированием", () => {
    render(<RemoteDesktopPage />);

    expect(screen.getByText("253474910")).toBeInTheDocument();
    expect(screen.getByText("100.73.162.127")).toBeInTheDocument();
    expect(screen.getByText("QJztruGKKjvEcX9XBLMixf21wieLGYABEaWby97JP5s=")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Скопировать: ID стола" })).toBeInTheDocument();
  });

  it("никогда не показывает пароль канала", () => {
    const { container } = render(<RemoteDesktopPage />);

    expect(container.textContent!.toLowerCase()).not.toContain("password");
    expect(screen.queryByText(/пароль[^.]*:/i)).not.toBeInTheDocument();
  });

  it("честно называет состояние «стол ещё не опубликовал ID»", () => {
    useDesktopNativeChannel.mockReturnValue(
      channel({ available: false, device_id: null }),
    );

    render(<RemoteDesktopPage />);

    expect(screen.getByText(/ещё не опубликовал ID/)).toBeInTheDocument();
    // Адрес и ключ уже известны — их можно настроить заранее.
    expect(screen.getByText("100.73.162.127")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Открыть в приложении/ })).not.toBeInTheDocument();
  });

  it("при ошибке предлагает повторить, не теряя шапку экрана", () => {
    const refetch = vi.fn();
    useDesktopNativeChannel.mockReturnValue({
      data: undefined,
      isPending: false,
      isError: true,
      refetch,
    });

    render(<RemoteDesktopPage />);

    expect(screen.getByRole("alert")).toHaveTextContent("Данные канала недоступны");
    screen.getByRole("button", { name: "Повторить" }).click();
    expect(refetch).toHaveBeenCalledOnce();
    expect(screen.getByRole("heading", { name: "Рабочий стол" })).toBeInTheDocument();
  });

  it("называет удалённую машину один раз, а не трижды", () => {
    render(<RemoteDesktopPage />);

    expect(screen.queryAllByText("Vision Desktop")).toHaveLength(0);
    expect(
      screen.getByRole("heading", { name: "Подключение к рабочему столу" }),
    ).toBeInTheDocument();
  });

  it("не требует от оператора VPN — брокер доступен напрямую", () => {
    render(<RemoteDesktopPage />);

    expect(screen.queryByText(/Tailscale/)).not.toBeInTheDocument();
    expect(screen.queryByText(/приватной сети/)).not.toBeInTheDocument();
  });

  it("ставит настройку клиента перед кнопкой запуска", () => {
    // Ссылка запуска несёт ID и пароль, но адрес брокера и ключ схема
    // rustdesk:// передать не может. Без переключения клиента кнопка отвечает
    // «устройство не найдено» — оператор так и попался: самый заметный элемент
    // экрана вёл в тупик, а условие висело мелким текстом ПОД ним.
    const { container } = render(<RemoteDesktopPage />);

    const text = container.textContent!;
    expect(text.indexOf("переключите клиент")).toBeGreaterThanOrEqual(0);
    expect(text.indexOf("переключите клиент")).toBeLessThan(text.indexOf("Открыть в приложении"));
    expect(text.indexOf("100.73.162.127")).toBeLessThan(text.indexOf("Открыть в приложении"));
    // Ограничение названо прямо, а не оставлено на догадку.
    expect(screen.getByText(/до шага 1 приложение ответит/)).toBeInTheDocument();
  });

  it("не даёт длинному ключу разъехать строку канала", () => {
    render(<RemoteDesktopPage />);

    // Строка канала — grid-элемент, а у него min-width по умолчанию auto:
    // без min-w-0 он не сожмётся, и длинный ключ уедет под кнопку копирования.
    const key = screen.getByText("QJztruGKKjvEcX9XBLMixf21wieLGYABEaWby97JP5s=");
    const row = key.closest("div")!.parentElement!;

    expect(row.className).toContain("min-w-0");
    expect(key.className).toContain("truncate");
  });
});
