/**
 * Тесты deep-link drawer /ads/$fbAdId (канон ads-web.jsx AdDrawer).
 *
 * Route грузит snapshot (находит ad в /dashboard/ads по id) + timeline-fallback
 * и рендерит общий AdDrawer. Проверяем:
 *   - header: eyebrow «… / ОБЪЯВЛЕНИЕ», ad_name, offer-chip;
 *   - metrics-snapshot grid (spend/CPL/leads из snapshot.metrics);
 *   - triggered-rule banner;
 *   - Esc / close → navigate назад к /ads;
 *   - footer Disable → ConfirmDialog (confirm-with-typing, placeholder DISABLE);
 *   - skeleton при загрузке (ad=null).
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { AdSnapshot } from "@fb/shared";

// ─── Моки ─────────────────────────────────────────────────────────────────────

const mockNavigate = vi.fn();
const FB_AD_ID = "120211984573_8761";

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: (_path: string) => (opts: { component: unknown }) => opts,
  useRouter: () => ({ navigate: mockNavigate }),
  useParams: () => ({ fbAdId: FB_AD_ID }),
}));

vi.mock("@/lib/api/ads", () => ({
  useAds: vi.fn(),
  useAdTimeline: vi.fn(() => ({ data: undefined, isLoading: false, isError: false })),
  useBulkDisable: vi.fn(() => ({ mutateAsync: vi.fn().mockResolvedValue({}), isPending: false })),
}));

vi.mock("@/lib/websocket/useRealtimeInvalidation", () => ({
  useRealtimeInvalidation: vi.fn(() => ({ status: "connected" })),
}));

// ─── Импорты после моков ──────────────────────────────────────────────────────

import { useAds, useAdTimeline } from "@/lib/api/ads";

// ─── Фабрика snapshot ──────────────────────────────────────────────────────────

function makeSnapshot(overrides: Partial<AdSnapshot> = {}): AdSnapshot {
  return {
    fb_ad_id: FB_AD_ID,
    internal_id: "a1b2c3d4-0000-0000-0000-000000000001",
    ad_name: "UA17 | SP | MV | Krov | 24.03",
    campaign_name: "GH_CR | 18.06",
    adset_name: "adset-android",
    offer_code: "CR2",
    alert_state: "stop_sent",
    is_active: true,
    last_seen_at: new Date().toISOString(),
    stop_rule_codes: ["cpl_stop"],
    warning_rule_codes: [],
    metrics: {
      cycle_ts: new Date().toISOString(),
      spend: "891.23",
      cost_per_lead: "42.10",
      cpm: "12.4",
      ctr: "1.25",
      frequency: "4.8",
      leads: 21,
      deposits: 3,
    },
    ...overrides,
  } as AdSnapshot;
}

// ─── Хелпер рендера ──────────────────────────────────────────────────────────

async function renderDrawer() {
  const { Route } = await import("../../routes/ads/$fbAdId");
  const Comp = (Route as unknown as { component: React.FC }).component;
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <Comp />
    </QueryClientProvider>,
  );
}

/** Мок useAds, возвращающий один snapshot (found-snapshot путь). */
function mockAdsWith(ad: AdSnapshot | null, isLoading = false) {
  vi.mocked(useAds).mockReturnValue({
    data: { data: ad ? [ad] : [], total: ad ? 1 : 0 },
    isLoading,
    isError: false,
    error: null,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useAds>);
}

// ─── Тесты ────────────────────────────────────────────────────────────────────

describe("AdDrawer (deep-link /ads/$fbAdId)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockNavigate.mockClear();
    mockAdsWith(makeSnapshot());
    vi.mocked(useAdTimeline).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useAdTimeline>);
  });

  // Header: eyebrow ОБЪЯВЛЕНИЕ + ad_name + offer-chip + ad_id.
  it("отрисовывает header (eyebrow ОБЪЯВЛЕНИЕ, ad_name, offer)", async () => {
    await renderDrawer();
    expect(screen.getByText("ОБЪЯВЛЕНИЕ")).toBeInTheDocument();
    expect(screen.getByText("UA17 | SP | MV | Krov | 24.03")).toBeInTheDocument();
    expect(screen.getAllByText("CR2").length).toBeGreaterThan(0);
    expect(screen.getByText(FB_AD_ID)).toBeInTheDocument();
  });

  // Иерархия: Кампания/Адсет («отец») — различает дубли по адсету.
  it("показывает иерархию (Кампания/Адсет)", async () => {
    await renderDrawer();
    expect(screen.getByText("ИЕРАРХИЯ")).toBeInTheDocument();
    expect(screen.getByText("GH_CR | 18.06")).toBeInTheDocument();
    expect(screen.getByText("adset-android")).toBeInTheDocument();
  });

  // Метрики-снимок: spend (money1 — один знак) + лейблы.
  it("отрисовывает metrics-snapshot grid с данными", async () => {
    await renderDrawer();
    expect(screen.getByText("spend")).toBeInTheDocument();
    expect(screen.getByText("CPL")).toBeInTheDocument();
    expect(screen.getByText("leads")).toBeInTheDocument();
    expect(screen.getByText("$891.2")).toBeInTheDocument();
  });

  // Triggered-rule banner.
  it("показывает triggered-rule banner", async () => {
    await renderDrawer();
    expect(screen.getByText(/сработали:/i)).toBeInTheDocument();
  });

  // Skeleton при ad=null + загрузке.
  it("рендерит skeleton при загрузке (ad ещё не готов)", async () => {
    mockAdsWith(null, true);
    await renderDrawer();
    expect(screen.getByLabelText("Загрузка данных объявления")).toBeInTheDocument();
  });

  // Close → navigate назад.
  it("кнопка Закрыть вызывает navigate к /ads", async () => {
    const user = userEvent.setup();
    await renderDrawer();
    await user.click(screen.getByRole("button", { name: "Закрыть" }));
    expect(mockNavigate).toHaveBeenCalledWith({ to: "/ads" });
  });

  // Esc → navigate назад.
  it("Esc вызывает navigate к /ads", async () => {
    const user = userEvent.setup();
    await renderDrawer();
    await user.keyboard("{Escape}");
    expect(mockNavigate).toHaveBeenCalledWith({ to: "/ads" });
  });

  // MONEY: footer Disable → ConfirmDialog (confirm-with-typing).
  it("кнопка Disable открывает ConfirmDialog (confirm-with-typing)", async () => {
    const user = userEvent.setup();
    await renderDrawer();

    await user.click(screen.getByRole("button", { name: /Отключить объявление/i }));
    expect(screen.getByText(/Отключить объявление\?/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText("DISABLE")).toBeInTheDocument();
  });

  // У уже отключённого (alert_state='disabled') нет кнопки «Отключить» — показываем статус.
  it("для disabled не показывает кнопку Отключить, показывает статус", async () => {
    mockAdsWith(makeSnapshot({ alert_state: "disabled" }));
    await renderDrawer();
    expect(
      screen.queryByRole("button", { name: /Отключить объявление/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/Объявление отключено/i)).toBeInTheDocument();
  });

});
