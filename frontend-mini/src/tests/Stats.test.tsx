/**
 * Smoke-тест StatsPage (routes/stats/index.tsx): реальный компонент экрана
 * (именованный экспорт StatsPage) поверх мокнутого @tanstack/react-router и
 * мокнутых хуков @/lib/api — без дублирования логики в helper-файле.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { StatsToday, StatsPeriod } from "@fb/shared";

// Мок роутера. createFileRoute(path) должен вернуть ФУНКЦИЮ (не объект) —
// в реальном routes/stats/index.tsx она вызывается как createFileRoute(p)({...}).
vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (opts: { component: unknown }) => ({ options: opts, component: opts.component }),
  useNavigate: () => vi.fn(),
}));

// Мок TG
vi.mock("@/lib/tg", () => ({
  haptic: { impact: vi.fn(), notify: vi.fn(), selection: vi.fn() },
  tgConfirm: vi.fn().mockResolvedValue(true),
  tgAlert: vi.fn().mockResolvedValue(undefined),
  registerBackButton: () => () => {},
  hideBackButton: vi.fn(),
  initTheme: vi.fn(),
  getInitData: () => "",
}));

const STATS_TODAY: StatsToday = {
  cabinet_day_start: "2026-07-02T00:00:00Z",
  generated_at: "2026-07-02T14:00:00Z",
  meta: {
    totals: {
      spend: "300.00",
      impressions: 20000,
      clicks: 800,
      leads: 80,
      registrations: 40,
      deposits: 10,
    },
    derived: {
      cpc: "0.38",
      cpl: "3.75",
      cpr: "7.50",
      cpa: "30.00",
      ctr_pct: "4.0",
      cr_click_lead_pct: "10.0",
      cr_lead_reg_pct: "50.0",
      cr_reg_dep_pct: "25.0",
    },
    series_hourly: [
      { ts: "2026-07-02T10:00:00Z", spend: "50.00", impressions: 3000, clicks: 100, leads: 10, registrations: 5, deposits: 1, active_ads: 4 },
      { ts: "2026-07-02T11:00:00Z", spend: "80.00", impressions: 4000, clicks: 150, leads: 15, registrations: 8, deposits: 2, active_ads: 4 },
    ],
  },
  tracker: {
    available: true,
    day_utc: "2026-07-02",
    attribution_note: "Attribution gap — нормально.",
    totals: { installs: 50, registrations: 40, deposits: 10, revenue: "800.00", roi_pct: "166.7" },
    series_daily: [],
  },
  breakdown: null,
};

const STATS_PERIOD: StatsPeriod = {
  from_iso: "2026-06-25T00:00:00Z",
  to_iso: "2026-07-02T14:00:00Z",
  meta: {
    totals: {
      spend: "2100.00",
      impressions: 140000,
      clicks: 5600,
      leads: 560,
      registrations: 280,
      deposits: 70,
    },
    derived: {
      cpc: "0.38",
      cpl: "3.75",
      cpr: "7.50",
      cpa: "30.00",
      ctr_pct: "4.0",
      cr_click_lead_pct: "10.0",
      cr_lead_reg_pct: "50.0",
      cr_reg_dep_pct: "25.0",
    },
    series_daily: [
      { day: "2026-06-30", spend: "300.00", impressions: 20000, clicks: 800, leads: 80, registrations: 40, deposits: 10, active_ads: 4 },
      { day: "2026-07-01", spend: "310.00", impressions: 21000, clicks: 820, leads: 82, registrations: 41, deposits: 11, active_ads: 4 },
    ],
  },
  tracker: {
    available: false,
    day_utc: null,
    attribution_note: "",
    totals: { installs: 0, registrations: 0, deposits: 0, revenue: null, roi_pct: null },
    series_daily: [],
  },
};

const mockUseStatsToday = vi.fn();
const mockUseStatsPeriod = vi.fn();

vi.mock("@/lib/api", () => ({
  useStatsToday: () => mockUseStatsToday(),
  useStatsPeriod: (days: number) => mockUseStatsPeriod(days),
}));

import { StatsPage } from "@/routes/stats/index";

function renderPage() {
  return render(<StatsPage />);
}

describe("StatsPage", () => {
  beforeEach(() => {
    mockUseStatsToday.mockReturnValue({
      data: STATS_TODAY,
      isLoading: false,
      isError: false,
      error: null,
    });
    mockUseStatsPeriod.mockReturnValue({
      data: STATS_PERIOD,
      isLoading: false,
      isError: false,
      error: null,
    });
  });

  // Шапка «Статистика» с eyebrow
  it("показывает шапку «Статистика»", () => {
    renderPage();
    expect(screen.getByText("Статистика")).toBeInTheDocument();
  });

  // По умолчанию период «Сегодня»: воронка из meta.totals за сегодня
  it("по умолчанию показывает данные за «Сегодня»", () => {
    renderPage();
    expect(screen.getByRole("button", { name: "Сегодня" })).toHaveClass("bg-accent");
    expect(screen.getByText("$300.00")).toBeInTheDocument();
  });

  // Переключение на «7д» — рендерит данные периода
  it("переключение на «7д» показывает данные периода", () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "7д" }));
    expect(screen.getByText("$2,100.00")).toBeInTheDocument();
  });

  // Трекер available=true за сегодня — метрики отрисованы
  it("блок трекера показывает метрики за «Сегодня» (available=true)", () => {
    renderPage();
    expect(screen.getByText("$800.00")).toBeInTheDocument();
  });

  // Трекер available=false за период — пустое состояние
  it("блок трекера показывает «Нет данных трекера» для периода «7д»", () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "7д" }));
    expect(screen.getByText("Нет данных трекера")).toBeInTheDocument();
  });

  // isError=true — сообщение об ошибке вместо контента
  it("показывает ошибку загрузки при isError=true", () => {
    mockUseStatsToday.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Сеть недоступна"),
    });
    renderPage();
    expect(screen.getByText("Ошибка загрузки")).toBeInTheDocument();
    expect(screen.getByText("Сеть недоступна")).toBeInTheDocument();
  });

  // isLoading=true — скелетоны вместо чисел (без краша)
  it("рендерится без ошибок при isLoading=true", () => {
    mockUseStatsToday.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
    });
    renderPage();
    expect(screen.getByText("Статистика")).toBeInTheDocument();
    expect(screen.queryByText("$300.00")).not.toBeInTheDocument();
  });
});
