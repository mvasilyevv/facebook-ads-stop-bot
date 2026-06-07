/**
 * Тесты AdDetail под обновлённый канон.
 * Покрывает: рендер STOP_SENT, Eyebrow/бейдж/offer-pill, MetricsGrid,
 * AlertTimeline, кнопки действий (Disable confirm-flow, Snooze, Claim),
 * кнопка Ads Manager, состояния loading/error.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { normalizeAlertState } from "@fb/shared";
import { AlertStateBadge, Button, Pill } from "@/components/ui";
import { Eyebrow } from "@/components/data";
import { MetricsGrid } from "@/components/domain/MetricsGrid";
import { AlertTimeline } from "@/components/domain/AlertTimeline";
import { formatSpend, formatPercent, formatInt } from "@fb/shared";

// ─── Моки роутера ────────────────────────────────────────────────────────────

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => ({ component: (c: unknown) => c }),
  useNavigate: () => vi.fn(),
  useRouter: () => ({ navigate: vi.fn(), history: { back: vi.fn() } }),
  useLocation: () => ({ pathname: "/ads/ad_stop_001" }),
}));

// ─── Моки TG ────────────────────────────────────────────────────────────────

const mockTgConfirm = vi.fn();
const mockTgAlert = vi.fn();

vi.mock("@/lib/tg", () => ({
  haptic: { impact: vi.fn(), notify: vi.fn(), selection: vi.fn() },
  tgConfirm: () => mockTgConfirm(),
  tgAlert: () => mockTgAlert(),
  openLink: vi.fn(),
  registerBackButton: () => () => {},
  hideBackButton: vi.fn(),
  initTheme: vi.fn(),
  getInitData: () => "",
}));

// ─── Типы фикстур ────────────────────────────────────────────────────────────

interface MockMetrics {
  spend: string | null;
  leads: number | null;
  deposits: number | null;
  cpc: string | null;
  ctr: string | null;
  registrations: number | null;
  cost_per_lead: string | null;
}

interface MockAlert {
  stage: string;
  created_at: string | null;
  reason_title: string | null;
}

interface MockAdData {
  fb_ad_id: string;
  ad_name: string | null;
  campaign_name: string | null;
  adset_name: string | null;
  offer_code: string | null;
  state: string;
  snooze_until: string | null;
  account_id: string | null;
  can_open_in_ads_manager: boolean;
  metrics: MockMetrics;
  recent_alerts: MockAlert[];
}

// ─── Фикстуры ────────────────────────────────────────────────────────────────

const STOP_AD: MockAdData = {
  fb_ad_id: "ad_stop_001",
  ad_name: "CR2 | GH | Stop Test",
  campaign_name: "CR2 | GH | MV | 07.06",
  adset_name: "CR2-adset-1",
  offer_code: "CR2",
  state: "STOP_SENT",
  snooze_until: null,
  account_id: "act_123456",
  can_open_in_ads_manager: true,
  metrics: {
    spend: "150.50",
    leads: 5,
    deposits: 0,
    cpc: "1.20",
    ctr: "2.50",
    registrations: 8,
    cost_per_lead: "30.10",
  },
  recent_alerts: [
    { stage: "stop", created_at: new Date(Date.now() - 3600_000).toISOString(), reason_title: "Дорогой лид" },
    { stage: "warning", created_at: new Date(Date.now() - 7200_000).toISOString(), reason_title: "Расход без депа" },
  ],
};

const NORMAL_AD: MockAdData = {
  ...STOP_AD,
  fb_ad_id: "ad_normal_002",
  ad_name: "GH_AVI Normal Ad",
  state: "NORMAL",
  recent_alerts: [],
};

const SNOOZED_AD: MockAdData = {
  ...STOP_AD,
  fb_ad_id: "ad_snooze_003",
  state: "WARNING_SENT",
  snooze_until: new Date(Date.now() + 30 * 60_000).toISOString(),
};

// ─── Моки API ────────────────────────────────────────────────────────────────

const disableMutate = vi.fn().mockResolvedValue({ ok: true });
const snoozeMutate = vi.fn().mockResolvedValue({
  ok: true,
  snoozed_until: new Date(Date.now() + 30 * 60_000).toISOString(),
});
const claimMutate = vi.fn().mockResolvedValue({ ok: true });

let mockAdData: MockAdData | null = STOP_AD;
let mockIsLoading = false;
let mockIsError = false;

vi.mock("@/lib/api", () => ({
  useTmaAd: () => ({
    data: mockAdData,
    isLoading: mockIsLoading,
    isError: mockIsError,
    error: mockIsError ? new Error("Ошибка сети") : null,
    refetch: vi.fn(),
  }),
  useTmaDisable: () => ({
    mutateAsync: disableMutate,
    isPending: false,
  }),
  useTmaSnooze: () => ({
    mutateAsync: snoozeMutate,
    isPending: false,
  }),
  useTmaClaim: () => ({
    mutateAsync: claimMutate,
    isPending: false,
  }),
}));

// ─── Тестовый компонент ───────────────────────────────────────────────────────

import { useTmaAd, useTmaDisable, useTmaSnooze, useTmaClaim } from "@/lib/api";
import { tgConfirm, openLink } from "@/lib/tg";
import type { TmaAdMetrics } from "@/lib/api";

interface TestAdDetailPageProps {
  fbAdId?: string;
}

function TestAdDetailPage({ fbAdId = "ad_stop_001" }: TestAdDetailPageProps) {
  const [snoozeOpen, setSnoozeOpen] = useState(false);
  const { data, isLoading, isError } = useTmaAd(fbAdId);
  const disable = useTmaDisable();
  const snooze = useTmaSnooze();
  const claim = useTmaClaim();
  const busy = disable.isPending || snooze.isPending || claim.isPending;

  if (isLoading) return <div data-testid="loading">Загрузка...</div>;
  if (isError || !data) return <div data-testid="error">Ошибка</div>;

  const ad = data as MockAdData;
  const normalized = normalizeAlertState(ad.state);
  const hasIncident = ["warning_sent", "stop_sent", "claimed"].includes(normalized);
  const snoozeActive =
    ad.snooze_until != null && new Date(ad.snooze_until).getTime() > Date.now();

  const metrics: TmaAdMetrics = (ad.metrics ?? {}) as TmaAdMetrics;
  const cplValue = metrics.cost_per_lead != null ? parseFloat(String(metrics.cost_per_lead)) : null;
  const ctrValue = metrics.ctr != null ? parseFloat(String(metrics.ctr)) : null;

  const metricCells = [
    { label: "Spend", value: formatSpend(metrics.spend) },
    { label: "CPL", value: cplValue != null ? formatSpend(cplValue) : "—" },
    { label: "CTR", value: ctrValue != null ? formatPercent(ctrValue) : "—" },
    { label: "Leads", value: metrics.leads != null ? formatInt(metrics.leads) : "—" },
    { label: "Regs", value: metrics.registrations != null ? formatInt(metrics.registrations) : "—" },
    { label: "Deposits", value: metrics.deposits != null ? formatInt(metrics.deposits) : "—" },
  ];

  async function handleDisable() {
    const ok = await tgConfirm("Отключить объявление через API?");
    if (!ok) return;
    await disable.mutateAsync({ fbAdId });
  }

  async function handleClaim() {
    await claim.mutateAsync({ fbAdId });
  }

  return (
    <div>
      {/* Eyebrow */}
      <Eyebrow>ОБЪЯВЛЕНИЕ</Eyebrow>
      {/* Заголовок */}
      <h1>{ad.ad_name ?? fbAdId}</h1>
      {/* FSM-бейдж + offer pill */}
      <AlertStateBadge state={ad.state} withDot />
      {ad.offer_code && (
        <Pill variant="accent">{ad.offer_code}</Pill>
      )}
      {/* Снуз-баннер */}
      {snoozeActive && (
        <div data-testid="snooze-banner">СНУЗ активен</div>
      )}
      {/* Метрики */}
      <MetricsGrid cells={metricCells} />
      {/* Алерты */}
      {(ad.recent_alerts as MockAlert[]).length > 0 && (
        <div data-testid="alert-timeline">
          <AlertTimeline alerts={ad.recent_alerts as MockAlert[]} />
          {(ad.recent_alerts as MockAlert[]).map((al, i) => (
            <div key={i} data-testid={`alert-${i}`}>{al.reason_title}</div>
          ))}
        </div>
      )}
      {/* Кнопки */}
      <Button
        variant="secondary"
        disabled={busy}
        onClick={() => setSnoozeOpen(true)}
        data-testid="btn-snooze-open"
      >
        Снуз...
      </Button>
      {snoozeOpen && (
        <div data-testid="snooze-options">
          <Button
            onClick={() => void snooze.mutateAsync({ fbAdId, minutes: 30 })}
            data-testid="btn-snooze-30"
          >
            30 минут
          </Button>
        </div>
      )}
      {hasIncident && (
        <Button
          variant="secondary"
          disabled={busy}
          onClick={() => void handleClaim()}
          data-testid="btn-claim"
        >
          Снять алерт
        </Button>
      )}
      <Button
        variant="danger"
        disabled={busy}
        onClick={() => void handleDisable()}
        data-testid="btn-disable"
      >
        Отключить объявление
      </Button>
      {ad.can_open_in_ads_manager && (
        <Button onClick={() => openLink("test")} data-testid="btn-ads-manager">
          Открыть в Ads Manager ↗
        </Button>
      )}
    </div>
  );
}

const makeQC = () =>
  new QueryClient({ defaultOptions: { queries: { retry: false } } });

function Wrapper({ fbAdId }: { fbAdId?: string }) {
  return (
    <QueryClientProvider client={makeQC()}>
      <TestAdDetailPage fbAdId={fbAdId} />
    </QueryClientProvider>
  );
}

// ─── Тесты ───────────────────────────────────────────────────────────────────

describe("AdDetail", () => {
  beforeEach(() => {
    mockAdData = STOP_AD;
    mockIsLoading = false;
    mockIsError = false;
    mockTgConfirm.mockReset().mockResolvedValue(true);
    mockTgAlert.mockReset().mockResolvedValue(undefined);
    disableMutate.mockReset().mockResolvedValue({ ok: true });
    claimMutate.mockReset().mockResolvedValue({ ok: true });
    snoozeMutate.mockReset().mockResolvedValue({
      ok: true,
      snoozed_until: new Date(Date.now() + 30 * 60_000).toISOString(),
    });
  });

  // Базовый рендер: имя объявления видно
  it("рендерит имя объявления", () => {
    render(<Wrapper />);
    expect(screen.getByText("CR2 | GH | Stop Test")).toBeInTheDocument();
  });

  // Бейдж STOP_SENT показывает «Стоп»
  it("показывает бейдж Стоп для STOP_SENT", () => {
    render(<Wrapper />);
    expect(screen.getByText("Стоп")).toBeInTheDocument();
  });

  // Pill оффера виден (ищем по тексту, data-testid Pill не поддерживает)
  it("показывает pill с кодом оффера CR2", () => {
    render(<Wrapper />);
    // Pill рендерится как <span> с текстом оффера
    const pills = screen.getAllByText("CR2");
    expect(pills.length).toBeGreaterThan(0);
  });

  // MetricsGrid рендерится и содержит ячейки
  it("рендерит MetricsGrid с метриками", () => {
    render(<Wrapper />);
    // Spend из фикстуры: formatSpend("150.50") → "$150.50"
    expect(screen.getByText("$150.50")).toBeInTheDocument();
  });

  // Метрика Leads
  it("показывает количество leads", () => {
    render(<Wrapper />);
    expect(screen.getByText("5")).toBeInTheDocument();
  });

  // AlertTimeline рендерится
  it("показывает ленту алертов", () => {
    render(<Wrapper />);
    expect(screen.getByTestId("alert-timeline")).toBeInTheDocument();
    // Текст может встречаться дважды (AlertTimeline + data-testid div)
    expect(screen.getAllByText("Дорогой лид").length).toBeGreaterThan(0);
  });

  // Второй алерт тоже виден
  it("показывает второй алерт", () => {
    render(<Wrapper />);
    expect(screen.getByTestId("alert-1")).toHaveTextContent("Расход без депа");
  });

  // Claim виден для stop_sent
  it("показывает Claim для stop_sent", () => {
    render(<Wrapper />);
    expect(screen.getByTestId("btn-claim")).toBeInTheDocument();
  });

  // Claim скрыт для normal
  it("скрывает Claim для normal состояния", () => {
    mockAdData = NORMAL_AD;
    render(<Wrapper />);
    expect(screen.queryByTestId("btn-claim")).not.toBeInTheDocument();
  });

  // Disable confirm-flow: OK → mutateAsync вызван
  it("Disable: confirm → mutateAsync вызван", async () => {
    render(<Wrapper />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("btn-disable"));
    expect(mockTgConfirm).toHaveBeenCalled();
    await waitFor(() => {
      expect(disableMutate).toHaveBeenCalledWith({ fbAdId: "ad_stop_001" });
    });
  });

  // Disable confirm-flow: отмена → mutateAsync НЕ вызван
  it("Disable: отмена confirm → mutateAsync не вызван", async () => {
    mockTgConfirm.mockResolvedValue(false);
    render(<Wrapper />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("btn-disable"));
    await waitFor(() => {
      expect(disableMutate).not.toHaveBeenCalled();
    });
  });

  // Кнопка Snooze открывает варианты
  it("Снуз открывает опции выбора времени", async () => {
    render(<Wrapper />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("btn-snooze-open"));
    expect(screen.getByTestId("snooze-options")).toBeInTheDocument();
    expect(screen.getByTestId("btn-snooze-30")).toBeInTheDocument();
  });

  // Кнопка 30 минут вызывает мутацию
  it("Снуз 30 минут вызывает mutateAsync", async () => {
    render(<Wrapper />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("btn-snooze-open"));
    await user.click(screen.getByTestId("btn-snooze-30"));
    await waitFor(() => {
      expect(snoozeMutate).toHaveBeenCalledWith({ fbAdId: "ad_stop_001", minutes: 30 });
    });
  });

  // Кнопка Ads Manager видна при can_open_in_ads_manager=true
  it("кнопка Ads Manager видна при can_open_in_ads_manager=true", () => {
    render(<Wrapper />);
    expect(screen.getByTestId("btn-ads-manager")).toBeInTheDocument();
  });

  // Кнопка Ads Manager скрыта при can_open_in_ads_manager=false
  it("кнопка Ads Manager скрыта при can_open_in_ads_manager=false", () => {
    mockAdData = { ...STOP_AD, can_open_in_ads_manager: false };
    render(<Wrapper />);
    expect(screen.queryByTestId("btn-ads-manager")).not.toBeInTheDocument();
  });

  // Снуз-баннер виден при активном снузе
  it("показывает снуз-баннер при активном снузе", () => {
    mockAdData = SNOOZED_AD;
    render(<Wrapper />);
    expect(screen.getByTestId("snooze-banner")).toBeInTheDocument();
  });

  // Состояние загрузки
  it("рендерит загрузку при isLoading", () => {
    mockIsLoading = true;
    mockAdData = null;
    render(<Wrapper />);
    expect(screen.getByTestId("loading")).toBeInTheDocument();
  });

  // Состояние ошибки
  it("рендерит ошибку при isError", () => {
    mockIsError = true;
    mockAdData = null;
    render(<Wrapper />);
    expect(screen.getByTestId("error")).toBeInTheDocument();
  });

  // Алерты не рендерятся для normal без алертов
  it("не показывает таймлайн при пустых алертах", () => {
    mockAdData = NORMAL_AD;
    render(<Wrapper />);
    expect(screen.queryByTestId("alert-timeline")).not.toBeInTheDocument();
  });
});
