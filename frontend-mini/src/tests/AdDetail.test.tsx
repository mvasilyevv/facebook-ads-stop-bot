/**
 * Тесты AdDetail: рендер STOP_SENT, скрытие Claim для normal, Disable confirm-flow.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { normalizeAlertState } from "@fb/shared";
import { AlertStateBadge, Button } from "@/components/ui";

// ─── Моки роутера ────────────────────────────────────────────────────────────

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => ({ component: (c: unknown) => c }),
  useNavigate: () => vi.fn(),
  useRouter: () => ({ navigate: vi.fn(), history: { back: vi.fn() } }),
  useLocation: () => ({ pathname: "/ads/123" }),
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

// ─── Моки API ────────────────────────────────────────────────────────────────

const disableMutate = vi.fn().mockResolvedValue({ ok: true });
const snoozeMutate = vi.fn().mockResolvedValue({
  ok: true,
  snoozed_until: new Date(Date.now() + 30 * 60_000).toISOString(),
});
const claimMutate = vi.fn().mockResolvedValue({ ok: true });

let mockAdData: MockAdData | null = null;
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

// ─── Компонент под тест ───────────────────────────────────────────────────────

import { useTmaAd, useTmaDisable, useTmaSnooze, useTmaClaim } from "@/lib/api";
import { tgConfirm, openLink } from "@/lib/tg";

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

  const { ad_name, state, can_open_in_ads_manager, recent_alerts = [] } = data as MockAdData;
  const normalized = normalizeAlertState(state);
  const hasIncident = ["warning_sent", "stop_sent", "claimed"].includes(normalized);

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
      <h1>{ad_name ?? fbAdId}</h1>
      <AlertStateBadge state={state} />
      <Button
        variant="danger"
        disabled={busy}
        onClick={() => void handleDisable()}
        data-testid="btn-disable"
      >
        Отключить объявление
      </Button>
      {hasIncident && (
        <Button
          variant="secondary"
          disabled={busy}
          onClick={() => void handleClaim()}
          data-testid="btn-claim"
        >
          Снять алерт (Claim)
        </Button>
      )}
      <Button onClick={() => setSnoozeOpen(true)} data-testid="btn-snooze-open">
        Снуз...
      </Button>
      {can_open_in_ads_manager && (
        <Button onClick={() => openLink("test")} data-testid="btn-ads-manager">
          Открыть в Ads Manager
        </Button>
      )}
      {snoozeOpen && (
        <div data-testid="snooze-sheet">
          <Button
            onClick={() => void snooze.mutateAsync({ fbAdId, minutes: 30 })}
            data-testid="btn-snooze-30"
          >
            30 минут
          </Button>
        </div>
      )}
      {(recent_alerts as MockAlert[]).map((al, i) => (
        <div key={i} data-testid={`alert-${i}`}>
          {al.stage} — {al.reason_title}
        </div>
      ))}
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
  });

  // Рендер с состоянием STOP_SENT: бейдж, имя, кнопки
  it("рендерит объявление со статусом STOP_SENT", () => {
    render(<Wrapper />);
    expect(screen.getByText("CR2 | GH | Stop Test")).toBeInTheDocument();
    // AlertStateBadge: normalizeAlertState("STOP_SENT") → "stop_sent" → label "Стоп"
    expect(screen.getByText("Стоп")).toBeInTheDocument();
    expect(screen.getByTestId("btn-disable")).toBeInTheDocument();
  });

  // Claim ПОКАЗЫВАЕТСЯ для stop_sent (hasIncident)
  it("показывает кнопку Claim для stop_sent", () => {
    render(<Wrapper />);
    expect(screen.getByTestId("btn-claim")).toBeInTheDocument();
  });

  // Claim СКРЫТ для normal (нет hasIncident)
  it("скрывает Claim для normal состояния", () => {
    mockAdData = NORMAL_AD;
    render(<Wrapper />);
    expect(screen.queryByTestId("btn-claim")).not.toBeInTheDocument();
  });

  // Disable confirm-flow: confirm → mutateAsync вызван
  it("Disable: показывает confirm, после OK вызывает mutateAsync", async () => {
    render(<Wrapper />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("btn-disable"));
    // tgConfirm был вызван
    expect(mockTgConfirm).toHaveBeenCalled();
    await waitFor(() => {
      expect(disableMutate).toHaveBeenCalledWith({ fbAdId: "ad_stop_001" });
    });
  });

  // Disable confirm-flow: отмена → mutateAsync НЕ вызван
  it("Disable: отмена confirm не вызывает mutateAsync", async () => {
    mockTgConfirm.mockResolvedValue(false);
    render(<Wrapper />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("btn-disable"));
    await waitFor(() => {
      expect(disableMutate).not.toHaveBeenCalled();
    });
  });

  // Лента алертов рендерится
  it("показывает ленту алертов", () => {
    render(<Wrapper />);
    expect(screen.getByTestId("alert-0")).toBeInTheDocument();
    expect(screen.getByText(/Дорогой лид/)).toBeInTheDocument();
  });

  // Кнопка "Открыть в Ads Manager" — только когда can_open_in_ads_manager=true
  it("кнопка Ads Manager видна при can_open_in_ads_manager=true", () => {
    render(<Wrapper />);
    expect(screen.getByTestId("btn-ads-manager")).toBeInTheDocument();
  });

  // Загрузка → скелетон
  it("рендерит загрузку при isLoading", () => {
    mockIsLoading = true;
    mockAdData = null;
    render(<Wrapper />);
    expect(screen.getByTestId("loading")).toBeInTheDocument();
  });

  // Ошибка → error state
  it("рендерит ошибку при isError", () => {
    mockIsError = true;
    mockAdData = null;
    render(<Wrapper />);
    expect(screen.getByTestId("error")).toBeInTheDocument();
  });
});
