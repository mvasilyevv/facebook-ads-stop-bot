// Тест: Shell и Sidebar рендерятся без краша в роутер-обёртке.

import { beforeEach, describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Shell } from "@/components/layout/Shell";
import { Sidebar } from "@/components/layout/Sidebar";

const operatorSnapshotMock = vi.hoisted(() => ({
  data: undefined as unknown,
}));
const operatorRealtimeStatusMock = vi.hoisted(() => ({
  value: "connected" as "connecting" | "connected" | "reconnecting",
}));

vi.mock("@fb/operator-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@fb/operator-api")>()),
  useOperatorRealtimeStatus: () => operatorRealtimeStatusMock.value,
}));

vi.mock("@/lib/api/operator", () => ({
  fetchOperatorActionProjectionsForRealtime: vi.fn(),
  fetchOperatorSnapshotForRealtime: vi.fn(),
  useOperatorSnapshot: () => ({
    data: operatorSnapshotMock.data,
    isLoading: false,
    isError: false,
  }),
  // TopBar (#345 QW12) читает кэш этого запроса на /cabinets/:id — в smoke-тестах
  // Shell путь всегда "/", поэтому хук отключён (enabled: Boolean(cabinetId));
  // мок нужен только чтобы модуль резолвился.
  useOperatorCabinetSnapshot: () => ({ data: undefined }),
}));

beforeEach(() => {
  operatorSnapshotMock.data = undefined;
  operatorRealtimeStatusMock.value = "connected";
});

// Моки status-хуков — не нужен реальный fetch в smoke.
vi.mock("@/lib/api/settings", () => ({
  useObserverSettings: vi.fn(() => ({
    data: { is_scanning_enabled: true },
    isLoading: false,
    isError: false,
  })),
  useToggleScanning: () => ({ mutate: vi.fn(), isPending: false }),
}));

function makeAppRouter(PageContent: () => React.ReactElement) {
  const rootRoute = createRootRoute({
    component: () => (
      <Shell>
        <PageContent />
      </Shell>
    ),
  });
  const indexRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    component: () => null,
  });
  return createRouter({
    routeTree: rootRoute.addChildren([indexRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
}

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

describe("Shell — smoke-рендер", () => {
  // Тест: Shell рендерится без ошибок, дочерний контент виден.
  it("рендерится и показывает children", async () => {
    function Page() {
      return <div data-testid="page-content">Контент страницы</div>;
    }
    const router = makeAppRouter(Page);
    render(
      <QueryClientProvider client={makeQueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    // TanStack Router рендерится асинхронно — ждём.
    await waitFor(() => {
      expect(screen.getByTestId("page-content")).toBeInTheDocument();
    });
    expect(screen.getByRole("link", { name: "Перейти к содержимому" })).toHaveAttribute(
      "href",
      "#main-content",
    );
    expect(screen.getByRole("main")).toHaveAttribute("id", "main-content");
  });

  it("открывает доступную мобильную навигацию", async () => {
    const router = makeAppRouter(() => <div />);
    render(
      <QueryClientProvider client={makeQueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    const openButton = await screen.findByRole("button", { name: "Открыть навигацию" });
    fireEvent.click(openButton);
    expect(await screen.findByRole("dialog", { name: "Навигация" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Закрыть меню" })).toBeInTheDocument();
  });

  it("возвращает keyboard focus на кнопку мобильной навигации после Escape", async () => {
    const user = userEvent.setup();
    const router = makeAppRouter(() => <div />);
    render(
      <QueryClientProvider client={makeQueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    const openButton = await screen.findByRole("button", {
      name: "Открыть навигацию",
    });
    await user.click(openButton);
    expect(await screen.findByRole("dialog", { name: "Навигация" })).toBeInTheDocument();

    await user.keyboard("{Escape}");

    await waitFor(() => expect(openButton).toHaveFocus());
  });

  // Тест: brand-mark присутствует в DOM.
  it("brand-mark 'FB' присутствует", async () => {
    const router = makeAppRouter(() => <div />);
    render(
      <QueryClientProvider client={makeQueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    await waitFor(() => {
      expect(screen.getByText("FB")).toBeInTheDocument();
    });
  });

  it("показывает action-first мобильную навигацию", async () => {
    const router = makeAppRouter(() => <div />);
    render(
      <QueryClientProvider client={makeQueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    const navigation = await screen.findByRole("navigation", {
      name: "Основная мобильная навигация",
    });
    expect(navigation).toHaveTextContent("Сейчас");
    expect(navigation).toHaveTextContent("Действия");
    expect(navigation).toHaveTextContent("Реклама");
    expect(navigation).toHaveTextContent("Ещё");
  });
});

describe("Sidebar — smoke-рендер с роутером", () => {
  function makeSidebarRouter() {
    const rootRoute = createRootRoute({ component: () => <Sidebar /> });
    const indexRoute = createRoute({
      getParentRoute: () => rootRoute,
      path: "/",
      component: () => null,
    });
    return createRouter({
      routeTree: rootRoute.addChildren([indexRoute]),
      history: createMemoryHistory({ initialEntries: ["/"] }),
    });
  }

  // Тест: Sidebar рендерится с nav-ссылками.
  it("рендерится без краша", async () => {
    const router = makeSidebarRouter();
    render(
      <QueryClientProvider client={makeQueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    await waitFor(() => {
      const links = screen.getAllByRole("link");
      expect(links.length).toBeGreaterThan(0);
    });
  });

  it("соответствует утверждённой иерархии и 44px touch targets", async () => {
    const router = makeSidebarRouter();
    render(
      <QueryClientProvider client={makeQueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );

    const navigation = await screen.findByRole("navigation", { name: "Основная навигация" });
    const links = within(navigation).getAllByRole("link");
    expect(links.map((link) => link.getAttribute("aria-label"))).toEqual([
      "Сейчас",
      "Решения",
      "Действия",
      "Инциденты",
      "Объявления",
      "Кампании",
      "Создание",
      "Офферы",
      "Аналитика",
      "Источники и воркеры",
      "Рабочий стол",
      "Настройки",
    ]);
    expect(within(navigation).getByRole("link", { name: "Источники и воркеры" })).toHaveAttribute(
      "href",
      "/system/sources",
    );
    for (const link of links) expect(link).toHaveClass("min-h-11");
    expect(screen.getByRole("button", { name: "Свернуть меню" })).toHaveClass("size-11");
  });

  it("includes unknown action outcomes in the attention badge", async () => {
    operatorSnapshotMock.data = {
      actions: {
        state: "ready",
        data: {
          items: [{ state: "unknown" }],
        },
      },
    };
    const router = makeSidebarRouter();
    render(
      <QueryClientProvider client={makeQueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );

    const actions = await screen.findByRole("link", { name: "Действия" });
    expect(actions).toHaveTextContent("Действия1");
  });

  it("считает открытые инциденты дешёвым бейджем из attention-снапшота", async () => {
    operatorSnapshotMock.data = {
      actions: { state: "ready", data: { items: [] } },
      attention: {
        state: "ready",
        data: {
          items: [
            { kind: "incident" },
            { kind: "incident" },
            { kind: "action" },
            { kind: "source" },
          ],
        },
      },
    };
    const router = makeSidebarRouter();
    render(
      <QueryClientProvider client={makeQueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );

    const incidents = await screen.findByRole("link", { name: "Инциденты" });
    expect(incidents).toHaveTextContent("Инциденты2");
    expect(incidents).toHaveAttribute("href", "/incidents");
  });

  it("не показывает устаревший action badge при переподключении", async () => {
    operatorRealtimeStatusMock.value = "reconnecting";
    operatorSnapshotMock.data = {
      actions: {
        state: "ready",
        data: { items: [{ state: "failed" }] },
      },
    };
    const router = makeSidebarRouter();
    render(
      <QueryClientProvider client={makeQueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );

    const actions = await screen.findByRole("link", { name: "Действия" });
    expect(actions).toHaveTextContent("Действия—");
    expect(actions).not.toHaveTextContent("Действия1");
    expect(within(actions).getByLabelText("Количество действий не подтверждено")).toHaveAttribute(
      "data-state",
      "unknown",
    );
  });

  // Тест: активная ссылка "/" имеет aria-current="page".
  it("активная ссылка '/' получает aria-current=page", async () => {
    const router = makeSidebarRouter();
    render(
      <QueryClientProvider client={makeQueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    await waitFor(() => {
      const activeLinks = screen.getAllByRole("link", { current: "page" });
      expect(activeLinks.length).toBe(1);
      expect(activeLinks[0]).toHaveAttribute("href", "/");
    });
  });

  // Тест: на /campaigns/create горит только подпункт «Создание», не «Кампании».
  it("на /campaigns/create активен только «Создание»", async () => {
    const rootRoute = createRootRoute({ component: () => <Sidebar /> });
    const routes = ["/", "/campaigns", "/campaigns/create"].map((path) =>
      createRoute({ getParentRoute: () => rootRoute, path, component: () => null }),
    );
    const router = createRouter({
      routeTree: rootRoute.addChildren(routes),
      history: createMemoryHistory({ initialEntries: ["/campaigns/create"] }),
    });
    render(
      <QueryClientProvider client={makeQueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    await waitFor(() => {
      const activeLinks = screen.getAllByRole("link", { current: "page" });
      expect(activeLinks.length).toBe(1);
      expect(activeLinks[0]).toHaveAttribute("href", "/campaigns/create");
    });
    // «Кампании» виден, но не aria-current (приглушённый родитель).
    const campaigns = screen.getByRole("link", { name: "Кампании" });
    expect(campaigns).not.toHaveAttribute("aria-current");
  });
});
