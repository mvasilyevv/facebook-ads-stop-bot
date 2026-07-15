// Тест: Shell и Sidebar рендерятся без краша в роутер-обёртке.

import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
import { useObserverSettings } from "@/lib/api/settings";

// Моки status-хуков — не нужен реальный fetch в smoke.
vi.mock("@/lib/api/settings", () => ({
  useHealthDetails: () => ({
    data: {
      workers: [{ name: "observer", status: "ONLINE" }],
      observer_runtime: { status: "running" },
      meta_api_channel: { status: "ONLINE" },
      overall: "HEALTHY",
    },
    isLoading: false,
    isError: false,
  }),
  useObserverSettings: vi.fn(() => ({
    data: { is_scanning_enabled: true },
    isLoading: false,
    isError: false,
  })),
  useObserverStatus: () => ({
    data: { status: "running", last_scan_at: null },
    isLoading: false,
    isError: false,
  }),
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
    expect(screen.getByRole("dialog", { name: "Навигация" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Закрыть меню" })).toBeInTheDocument();
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

  it("показывает единый CTA в global status bar при паузе", async () => {
    vi.mocked(useObserverSettings).mockReturnValue({
      data: { is_scanning_enabled: false },
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useObserverSettings>);
    const router = makeAppRouter(() => <div />);
    render(
      <QueryClientProvider client={makeQueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("Мониторинг на паузе")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Включить мониторинг" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Диагностика" })).toBeInTheDocument();
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
