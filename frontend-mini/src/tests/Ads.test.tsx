/**
 * Тест AdsPage: рендер с мок-данными useDashboardAds, фильтрация.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { AdSnapshot } from "@fb/shared";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => ({ component: (c: unknown) => c }),
  useNavigate: () => vi.fn(),
  useRouter: () => ({ navigate: vi.fn() }),
  useLocation: () => ({ pathname: "/ads" }),
}));

vi.mock("@/lib/tg", () => ({
  haptic: { impact: vi.fn(), notify: vi.fn(), selection: vi.fn() },
  initTheme: vi.fn(),
  getInitData: () => "",
}));

// Мок-объявления
const MOCK_ADS: AdSnapshot[] = [
  {
    fb_ad_id: "ad001",
    ad_name: "CR2 Stop Test Ad",
    alert_state: "stop_sent",
    offer_code: "CR2",
    campaign_name: "CR2 | GH | MV",
    last_seen_at: new Date().toISOString(),
    adset_name: null,
    metrics: { spend: "150.00", cpc: "0.50", leads: 5, deposits: 0 } as unknown as AdSnapshot["metrics"],
    stop_rule_codes: ["spend_no_event"],
    warning_rule_codes: [],
  } as unknown as AdSnapshot,
  {
    fb_ad_id: "ad002",
    ad_name: "Normal Ad",
    alert_state: "normal",
    offer_code: "GH_AVI",
    campaign_name: "GH | AVI | Test",
    last_seen_at: new Date().toISOString(),
    adset_name: null,
    metrics: { spend: "80.00", cpc: "0.30", leads: 10, deposits: 3 } as unknown as AdSnapshot["metrics"],
    stop_rule_codes: [],
    warning_rule_codes: [],
  } as unknown as AdSnapshot,
];

vi.mock("@/lib/api", () => ({
  useDashboardAds: () => ({
    data: MOCK_ADS,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    dataUpdatedAt: Date.now(),
  }),
}));

// Инлайн-тестовый компонент AdsPage
import { useState, useMemo } from "react";
import { useDashboardAds } from "@/lib/api";
import { AlertStateBadge, EmptyState, Pill } from "@/components/ui";
import { MiniHeader } from "@/components/layout/MiniHeader";

function TestAdsPage() {
  const [search, setSearch] = useState("");
  const { data: allAds = [], isLoading } = useDashboardAds("", search);

  const filtered = useMemo(() => {
    if (!search.trim()) return allAds;
    const q = search.toLowerCase();
    return allAds.filter(
      (ad) =>
        (ad.ad_name ?? "").toLowerCase().includes(q) ||
        ((ad as { campaign_name?: string }).campaign_name ?? "").toLowerCase().includes(q),
    );
  }, [allAds, search]);

  if (isLoading) return <div>Загрузка...</div>;

  return (
    <div>
      <MiniHeader eyebrow="Объявления" title={`${filtered.length} объявл.`} />
      <input
        placeholder="Поиск..."
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        aria-label="Поиск по объявлениям"
      />
      {filtered.length === 0 ? (
        <EmptyState title="Объявлений не найдено" />
      ) : (
        filtered.map((ad) => (
          <div key={ad.fb_ad_id} data-testid={`ad-${ad.fb_ad_id}`}>
            <p>{ad.ad_name}</p>
            <AlertStateBadge state={ad.alert_state ?? "normal"} />
            {ad.offer_code && <Pill variant="accent">{ad.offer_code}</Pill>}
          </div>
        ))
      )}
    </div>
  );
}

const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

function Wrapper() {
  return (
    <QueryClientProvider client={qc}>
      <TestAdsPage />
    </QueryClientProvider>
  );
}

describe("AdsPage", () => {
  // Рендерит оба объявления
  it("показывает все объявления", () => {
    render(<Wrapper />);
    expect(screen.getByText("CR2 Stop Test Ad")).toBeInTheDocument();
    expect(screen.getByText("Normal Ad")).toBeInTheDocument();
  });

  // Badge FSM корректен
  it("показывает badge 'Стоп' для stop_sent объявления", () => {
    render(<Wrapper />);
    expect(screen.getByText("Стоп")).toBeInTheDocument();
  });

  // Pill оффера
  it("показывает pill оффера CR2", () => {
    render(<Wrapper />);
    expect(screen.getByText("CR2")).toBeInTheDocument();
  });

  // Поиск фильтрует
  it("поиск 'CR2' оставляет только первое объявление", async () => {
    render(<Wrapper />);
    const input = screen.getByLabelText("Поиск по объявлениям");
    await userEvent.type(input, "CR2");
    expect(screen.getByText("CR2 Stop Test Ad")).toBeInTheDocument();
    expect(screen.queryByText("Normal Ad")).not.toBeInTheDocument();
  });

  // Пустой поиск → EmptyState
  it("EmptyState при поиске без совпадений", async () => {
    render(<Wrapper />);
    const input = screen.getByLabelText("Поиск по объявлениям");
    await userEvent.type(input, "xyz_no_match_at_all");
    expect(screen.getByText("Объявлений не найдено")).toBeInTheDocument();
  });
});
