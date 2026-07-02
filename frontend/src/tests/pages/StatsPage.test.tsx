/**
 * Smoke-тест Stats-страницы (паттерн tests/pages/Dashboard.test.tsx).
 * Моки: useStatsToday, useStatsPeriod, TanStack Router.
 */

import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ─── Моки модулей ─────────────────────────────────────────────────────────────

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: (_path: string) => (opts: { component: unknown }) => opts,
  useRouter: () => ({ navigate: vi.fn() }),
  useParams: () => ({}),
  Outlet: () => null,
}));

const mockStatsToday = {
  cabinet_day_start: "2026-07-02T00:00:00Z",
  generated_at: "2026-07-02T12:00:00Z",
  meta: {
    totals: {
      spend: "500.00",
      impressions: 20000,
      clicks: 1000,
      leads: 80,
      registrations: 40,
      deposits: 10,
    },
    derived: {
      cpc: "0.5",
      cpl: "6.25",
      cpr: "12.5",
      cpa: "50.0",
      ctr_pct: "5.0",
      cr_click_lead_pct: "8.0",
      cr_lead_reg_pct: "50.0",
      cr_reg_dep_pct: "25.0",
    },
    series_hourly: [
      { ts: "2026-07-02T10:00:00Z", spend: "100.00", impressions: 4000, clicks: 200, leads: 16, registrations: 8, deposits: 2, active_ads: 5 },
      { ts: "2026-07-02T11:00:00Z", spend: "150.00", impressions: 6000, clicks: 300, leads: 24, registrations: 12, deposits: 3, active_ads: 4 },
    ],
  },
  tracker: {
    available: true,
    day_utc: "2026-07-02",
    attribution_note: "Атрибуция по click_id",
    totals: { installs: 500, registrations: 40, deposits: 10, revenue: "1000.00", roi_pct: "10.0" },
  },
  breakdown: [
    { key: "off_1", label: "GH_CR2", spend: "500.00", clicks: 1000, leads: 80, registrations: 40, deposits: 10, cpl: "6.25" },
  ],
};

vi.mock("@/lib/api/stats", () => ({
  useStatsToday: vi.fn(() => ({
    data: mockStatsToday,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  })),
  useStatsPeriod: vi.fn(() => ({
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  })),
}));

// ─── Импорты после моков ──────────────────────────────────────────────────────

import { useStatsToday } from "@/lib/api/stats";

// ─── Хелперы ─────────────────────────────────────────────────────────────────

function createQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

async function renderStatsPage() {
  const { StatsPage } = await import("../../routes/stats/index").then((m) => {
    const route = m.Route as unknown as { component: React.FC };
    return { StatsPage: route.component };
  });

  const qc = createQueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <StatsPage />
    </QueryClientProvider>,
  );
}

// ─── Тесты ────────────────────────────────────────────────────────────────────

describe("StatsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useStatsToday).mockReturnValue({
      data: mockStatsToday,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useStatsToday>);
  });

  // Заголовок страницы — «Статистика».
  it("рендерит h1 «Статистика»", async () => {
    await renderStatsPage();
    const h1 = screen.getByRole("heading", { level: 1 });
    expect(h1).toHaveTextContent("Статистика");
  });

  // По умолчанию режим «Сегодня» — воронка/derived/график/breakdown видны.
  it("режим «Сегодня»: рендерит воронку и производные метрики", async () => {
    await renderStatsPage();

    expect(screen.getByRole("list", { name: "Воронка залива" })).toBeInTheDocument();
    // $500.00 встречается и в KPI-строке, и в строке breakdown-таблицы (тот же тотал).
    expect(screen.getAllByText("$500.00").length).toBeGreaterThan(0);
    // Breakdown-таблица с офферным разрезом.
    expect(screen.getByText("GH_CR2")).toBeInTheDocument();
  });

  // Тумблер периодов присутствует — «Сегодня», 7д/30д/90д, «Период…».
  it("рендерит переключатель периода", async () => {
    await renderStatsPage();
    expect(screen.getByRole("group", { name: "Период статистики" })).toBeInTheDocument();
    expect(screen.getByText("Сегодня")).toBeInTheDocument();
    expect(screen.getByText("7д")).toBeInTheDocument();
  });

  // Ошибка загрузки без данных → ErrorState.
  it("рендерит ErrorState при isError=true и отсутствии данных", async () => {
    vi.mocked(useStatsToday).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Network error"),
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useStatsToday>);

    await renderStatsPage();
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });
});
