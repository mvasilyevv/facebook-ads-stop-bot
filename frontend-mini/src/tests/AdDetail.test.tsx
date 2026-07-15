/**
 * Тесты AdDetail: рендер РЕАЛЬНОГО компонента AdDetailPage (routes/ads/$fbAdId.tsx)
 * поверх мокнутого @tanstack/react-router и @/lib/api — паттерн StatsPage
 * (именованный экспорт компонента, без дублирования логики в test.helper.tsx).
 *
 * Покрывает: рендер STOP_SENT, Eyebrow/бейдж/offer-pill, MetricsGrid, AlertTimeline,
 * кнопки действий (Disable confirm-flow, Claim), кнопка Ads Manager, loading/error.
 *
 * MID-23 аудита 02.07: добавлены сценарии — отклонённая disable-мутация показывает
 * ошибку и возвращает кнопку в активное состояние (а не тихий catch), и явный
 * regression-тест на анти-даблклик (кнопка disabled во время isPending).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import type { ComponentType } from "react";
import userEvent from "@testing-library/user-event";

// ─── Моки роутера ────────────────────────────────────────────────────────────
// Route.useParams() читается напрямую в компоненте — мок createFileRoute должен
// вернуть объект с методом useParams (не просто { component }).

let mockFbAdId = "ad_stop_001";

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (opts: { component: unknown }) => ({
    ...opts,
    useParams: () => ({ fbAdId: mockFbAdId }),
  }),
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
  tgAlert: (...args: unknown[]) => mockTgAlert(...args),
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
  account_id: string | null;
  can_open_in_ads_manager: boolean;
  creative_thumb_url?: string | null;
  creative_image_url?: string | null;
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

const disableMutate = vi.fn();
const claimMutate = vi.fn();

let mockAdData: MockAdData | null = STOP_AD;
let mockIsLoading = false;
let mockIsError = false;
let mockDisablePending = false;
let mockClaimPending = false;

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
    isPending: mockDisablePending,
  }),
  useTmaClaim: () => ({
    mutateAsync: claimMutate,
    isPending: mockClaimPending,
  }),
}));

// ─── Компонент под тестом (реальный) ──────────────────────────────────────────

import { Route } from "@/routes/ads/$fbAdId";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const AdDetailPage = (Route as unknown as { component: ComponentType }).component;

const makeQC = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });

function Wrapper() {
  return (
    <QueryClientProvider client={makeQC()}>
      <AdDetailPage />
    </QueryClientProvider>
  );
}

// ─── Тесты ───────────────────────────────────────────────────────────────────

describe("AdDetail", () => {
  beforeEach(() => {
    mockFbAdId = "ad_stop_001";
    mockAdData = STOP_AD;
    mockIsLoading = false;
    mockIsError = false;
    mockDisablePending = false;
    mockClaimPending = false;
    mockTgConfirm.mockReset().mockResolvedValue(true);
    mockTgAlert.mockReset().mockResolvedValue(undefined);
    disableMutate.mockReset().mockResolvedValue({ ok: true });
    claimMutate.mockReset().mockResolvedValue({ ok: true });
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

  // Pill оффера виден
  it("показывает pill с кодом оффера CR2", () => {
    render(<Wrapper />);
    const pills = screen.getAllByText("CR2");
    expect(pills.length).toBeGreaterThan(0);
  });

  // MetricsGrid рендерится и содержит ячейки
  it("рендерит MetricsGrid с метриками", () => {
    render(<Wrapper />);
    expect(screen.getByText("$150.50")).toBeInTheDocument();
  });

  // Метрика Leads
  it("показывает количество leads", () => {
    render(<Wrapper />);
    expect(screen.getAllByText("5").length).toBeGreaterThan(0);
  });

  // AlertTimeline рендерится
  it("показывает ленту алертов", () => {
    render(<Wrapper />);
    expect(screen.getByText("Дорогой лид")).toBeInTheDocument();
    expect(screen.getByText("Расход без депа")).toBeInTheDocument();
  });

  // Claim виден для stop_sent
  it("показывает Claim для stop_sent", () => {
    render(<Wrapper />);
    expect(screen.getByRole("button", { name: "Снять алерт" })).toBeInTheDocument();
  });

  // Claim скрыт для normal
  it("скрывает Claim для normal состояния", () => {
    mockAdData = NORMAL_AD;
    mockFbAdId = "ad_normal_002";
    render(<Wrapper />);
    expect(screen.queryByRole("button", { name: "Снять алерт" })).not.toBeInTheDocument();
  });

  // Disable confirm-flow: OK → mutateAsync вызван
  it("Disable: confirm → mutateAsync вызван", async () => {
    render(<Wrapper />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Отключить объявление" }));
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
    await user.click(screen.getByRole("button", { name: "Отключить объявление" }));
    await waitFor(() => {
      expect(disableMutate).not.toHaveBeenCalled();
    });
  });

  // MID-23: отклонённая disable-мутация (ошибка сети/сервера) → UI показывает
  // ошибку через tgAlert, кнопка возвращается в активное (не залипшее) состояние.
  it("Disable: отклонённая мутация → показывает ошибку, кнопка снова активна", async () => {
    disableMutate.mockReset().mockRejectedValue(new Error("Сервер недоступен"));
    render(<Wrapper />);
    const user = userEvent.setup();
    const btn = screen.getByRole("button", { name: "Отключить объявление" });
    await user.click(btn);

    await waitFor(() => {
      expect(disableMutate).toHaveBeenCalledWith({ fbAdId: "ad_stop_001" });
    });
    await waitFor(() => {
      expect(mockTgAlert).toHaveBeenCalledWith("Сервер недоступен");
    });
    // isPending снова false после отклонённого промиса — кнопка не задизейблена busy-состоянием.
    expect(screen.getByRole("button", { name: "Отключить объявление" })).not.toBeDisabled();
  });

  // MID-23: анти-даблклик — во время isPending (disable ИЛИ claim) обе money-кнопки
  // задизейблены, защита от дубля задачи в outbox при повторном тапе.
  it("Disable и Claim задизейблены во время isPending (анти-даблклик)", () => {
    mockDisablePending = true;
    render(<Wrapper />);
    expect(screen.getByRole("button", { name: "Отключить объявление" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Снять алерт" })).toBeDisabled();
  });

  it("Claim.isPending тоже блокирует кнопку Disable (общий busy)", () => {
    mockClaimPending = true;
    render(<Wrapper />);
    expect(screen.getByRole("button", { name: "Отключить объявление" })).toBeDisabled();
  });

  // Кнопка Ads Manager видна при can_open_in_ads_manager=true
  it("кнопка Ads Manager видна при can_open_in_ads_manager=true", () => {
    render(<Wrapper />);
    expect(screen.getByRole("button", { name: /Открыть в Ads Manager/ })).toBeInTheDocument();
  });

  // Кнопка Ads Manager скрыта при can_open_in_ads_manager=false
  it("кнопка Ads Manager скрыта при can_open_in_ads_manager=false", () => {
    mockAdData = { ...STOP_AD, can_open_in_ads_manager: false };
    render(<Wrapper />);
    expect(screen.queryByRole("button", { name: /Открыть в Ads Manager/ })).not.toBeInTheDocument();
  });

  // Состояние загрузки
  it("рендерит загрузку при isLoading", () => {
    mockIsLoading = true;
    mockAdData = null;
    render(<Wrapper />);
    // Skeleton-заглушки рендерятся вместо контента объявления
    expect(screen.queryByText("CR2 | GH | Stop Test")).not.toBeInTheDocument();
  });

  // Состояние ошибки
  it("рендерит ошибку при isError", () => {
    mockIsError = true;
    mockAdData = null;
    render(<Wrapper />);
    expect(screen.getByText("Ошибка сети")).toBeInTheDocument();
  });

  // Алерты не рендерятся для normal без алертов
  it("показывает пустой стейт для normal без алертов", () => {
    mockAdData = NORMAL_AD;
    mockFbAdId = "ad_normal_002";
    render(<Wrapper />);
    expect(screen.getByText("Нет активных алертов")).toBeInTheDocument();
  });
});
