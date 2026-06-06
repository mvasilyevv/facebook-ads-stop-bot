/**
 * Тесты AdDetailDrawer (/ads/$fbAdId).
 *
 * Проверяем:
 *   - Drawer открывается (виден header с eyebrow)
 *   - Esc вызывает navigate назад
 *   - Кнопка close вызывает navigate назад
 *   - KVGrid рендерится с метриками при наличии данных
 *   - Skeleton при загрузке
 *   - ConfirmDialog отключения открывается
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ─── Моки ─────────────────────────────────────────────────────────────────────

const mockNavigate = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: (_path: string) => (opts: { component: unknown }) => opts,
  useRouter: () => ({ navigate: mockNavigate }),
  useParams: () => ({ fbAdId: "120211984573_8761" }),
}));

vi.mock("@/lib/api/ads", () => ({
  useAdTimeline: vi.fn(),
  useSnoozeAd: vi.fn(() => ({ mutateAsync: vi.fn().mockResolvedValue({}), isPending: false })),
  useBulkDisable: vi.fn(() => ({ mutateAsync: vi.fn().mockResolvedValue({}), isPending: false })),
  useAds: vi.fn(() => ({ data: { data: [], total: 0 }, isLoading: false, isError: false, refetch: vi.fn() })),
  useDisableTasks: vi.fn(() => ({ data: [], isLoading: false, isError: false, refetch: vi.fn() })),
  useEnableTasks: vi.fn(() => ({ data: [], isLoading: false, isError: false, refetch: vi.fn() })),
  useBulkSnooze: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
}));

vi.mock("@/lib/api/dashboard", () => ({
  useSpendHistory: vi.fn(() => ({ data: [] })),
  useDashboardBatch: vi.fn(() => ({ data: null, isLoading: false, isError: false, refetch: vi.fn() })),
  useChartData: vi.fn(() => ({ data: [], isLoading: false, isError: false, refetch: vi.fn() })),
}));

vi.mock("@/lib/websocket/useRealtimeInvalidation", () => ({
  useRealtimeInvalidation: vi.fn(() => ({
    status: "connected",
    pollingFallback: false,
    reconnectAttempt: 0,
    forceReconnect: vi.fn(),
  })),
}));

// ─── Импорты ─────────────────────────────────────────────────────────────────

import { useAdTimeline } from "@/lib/api/ads";
import type { AdTimeline } from "@fb/shared";
import type { components } from "@fb/shared/api/generated";

// ─── Фабрика мок-timeline ────────────────────────────────────────────────────

function makeTimeline(overrides: Partial<AdTimeline> = {}): AdTimeline {
  return {
    fb_ad_id: "120211984573_8761",
    internal_id: "a1b2c3d4-0000-0000-0000-000000000001",
    ad_name: "UA17 | SP | MV | Krov | 24.03",
    offer_code: "CR2",
    from_iso: new Date(Date.now() - 86400000).toISOString(),
    to_iso: new Date().toISOString(),
    metrics: [
      {
        cycle_ts: new Date().toISOString(),
        spend: "891.23",
        cost_per_lead: "42.10",
        leads: 21,
        deposits: 3,
        ctr: "1.25",
        frequency: "4.8",
      } as components["schemas"]["MetricRow"],
    ],
    alerts: [
      {
        id: "alert-1",
        stage: "stop",
        matched_rule_codes: ["cpl_stop"],
        triggered_by_rule_codes: ["cpl_stop"],
        created_at: new Date().toISOString(),
      },
    ],
    tasks: [],
    ...overrides,
  };
}

// ─── Хелпер рендера ──────────────────────────────────────────────────────────

async function renderDrawer() {
  const { AdDetailDrawer } = await import("../../routes/ads/$fbAdId").then((m) => {
    const route = m.Route as unknown as { component: React.FC };
    return { AdDetailDrawer: route.component };
  });

  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AdDetailDrawer />
    </QueryClientProvider>,
  );
}

// ─── Тесты ────────────────────────────────────────────────────────────────────

describe("AdDetailDrawer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockNavigate.mockClear();
  });

  // Drawer открыт — eyebrow видно
  it("отрисовывает eyebrow '06 · AD DETAIL'", async () => {
    vi.mocked(useAdTimeline).mockReturnValue({
      data: makeTimeline(),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useAdTimeline>);

    await renderDrawer();
    expect(screen.getByText("06 · AD DETAIL")).toBeInTheDocument();
  });

  // Skeleton при загрузке
  it("рендерит skeleton при isLoading=true", async () => {
    vi.mocked(useAdTimeline).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useAdTimeline>);

    await renderDrawer();
    // DrawerSkeleton рендерит role=status — Radix Drawer может использовать portal
    // поэтому ищем через screen (document-level)
    const loadingEl = screen.queryByLabelText("Загрузка данных объявления");
    expect(loadingEl).toBeInTheDocument();
  });

  // Кнопка close → navigate назад
  it("кнопка Закрыть вызывает navigate к /ads/", async () => {
    const user = userEvent.setup();
    vi.mocked(useAdTimeline).mockReturnValue({
      data: makeTimeline(),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useAdTimeline>);

    await renderDrawer();
    await user.click(screen.getByRole("button", { name: "Закрыть" }));
    expect(mockNavigate).toHaveBeenCalledWith({ to: "/ads" });
  });

  // Esc → navigate назад
  it("Esc вызывает navigate к /ads/", async () => {
    const user = userEvent.setup();
    vi.mocked(useAdTimeline).mockReturnValue({
      data: makeTimeline(),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useAdTimeline>);

    await renderDrawer();
    await user.keyboard("{Escape}");
    expect(mockNavigate).toHaveBeenCalledWith({ to: "/ads" });
  });

  // KVGrid видна с данными метрик
  it("отрисовывает KVGrid с метриками при наличии данных", async () => {
    vi.mocked(useAdTimeline).mockReturnValue({
      data: makeTimeline(),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useAdTimeline>);

    await renderDrawer();

    // KVGrid отображает Spend / CPL / Leads
    expect(screen.getByText("Spend")).toBeInTheDocument();
    expect(screen.getByText("CPL")).toBeInTheDocument();
    expect(screen.getByText("Leads")).toBeInTheDocument();
    // Значение из мок-данных: spend 891.23
    expect(screen.getByText("$891.23")).toBeInTheDocument();
  });

  // Кнопка Отключить → ConfirmDialog
  it("кнопка Отключить открывает ConfirmDialog", async () => {
    const user = userEvent.setup();
    vi.mocked(useAdTimeline).mockReturnValue({
      data: makeTimeline(),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useAdTimeline>);

    await renderDrawer();

    // Кнопка Отключить в footer
    const disableBtn = screen.getByRole("button", { name: /Отключить объявление вручную/i });
    await user.click(disableBtn);

    // ConfirmDialog появился
    expect(screen.getByText(/Отключить объявление\?/i)).toBeInTheDocument();
  });

  // Кнопка Снуз 1ч → useSnoozeAd
  it("Снуз 1ч вызывает useSnoozeAd с minutes=60", async () => {
    const mockSnooze = vi.fn().mockResolvedValue({});
    const { useSnoozeAd } = await import("@/lib/api/ads");
    vi.mocked(useSnoozeAd).mockReturnValue({
      mutateAsync: mockSnooze,
      isPending: false,
    } as unknown as ReturnType<typeof useSnoozeAd>);

    vi.mocked(useAdTimeline).mockReturnValue({
      data: makeTimeline(),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useAdTimeline>);

    const user = userEvent.setup();
    await renderDrawer();

    await user.click(screen.getByRole("button", { name: /Снуз на 1 час/i }));
    expect(mockSnooze).toHaveBeenCalledWith({ minutes: 60 });
  });
});
