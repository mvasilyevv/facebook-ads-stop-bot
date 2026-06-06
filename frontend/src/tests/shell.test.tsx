// Тест: Shell и Sidebar рендерятся без краша в роутер-обёртке.

import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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

// Мок useHealthDetails — не нужен реальный fetch в smoke.
vi.mock("@/lib/api/settings", () => ({
  useHealthDetails: () => ({ data: null, isError: false }),
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
});
