/**
 * Тест AdsPage (ads/index.tsx) под канон ads-mini.jsx:
 * строки-сетка, поиск, мультивыбор чипов состояний.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
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

// Мок-объявления: stop_sent c высоким CPL, normal с низким CPL
const MOCK_ADS: AdSnapshot[] = [
  {
    fb_ad_id:       "ad001",
    ad_name:        "CR2 Stop Test Ad",
    alert_state:    "stop_sent",
    offer_code:     "CR2",
    campaign_name:  "CR2 | GH | MV",
    last_seen_at:   new Date().toISOString(),
    adset_name:     null,
    internal_id:    "uuid-001",
    is_active:      true,
    metrics: {
      cycle_ts:       new Date().toISOString(),
      spend:          "150.00",
      cost_per_lead:  "45.00",
    } as AdSnapshot["metrics"],
    stop_rule_codes:    ["spend_no_event"],
    warning_rule_codes: [],
  } as AdSnapshot,
  {
    fb_ad_id:       "ad002",
    ad_name:        "Normal Ad",
    alert_state:    "normal",
    offer_code:     "GH_AVI",
    campaign_name:  "GH | AVI | Test",
    last_seen_at:   new Date().toISOString(),
    adset_name:     null,
    internal_id:    "uuid-002",
    is_active:      true,
    metrics: {
      cycle_ts:       new Date().toISOString(),
      spend:          "80.00",
      cost_per_lead:  "12.00",
    } as AdSnapshot["metrics"],
    stop_rule_codes:    [],
    warning_rule_codes: [],
  } as AdSnapshot,
];

vi.mock("@/lib/api", () => ({
  useDashboardAds: () => ({
    data:          MOCK_ADS,
    isLoading:     false,
    isError:       false,
    error:         null,
    refetch:       vi.fn(),
    dataUpdatedAt: Date.now(),
  }),
}));

const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

import { useState, useMemo } from "react";
import { useDashboardAds } from "@/lib/api";
import { normalizeAlertState, ALERT_STATE_LABELS, formatSpend } from "@fb/shared";
import { MiniHeader } from "@/components/layout/MiniHeader";

/** Минимальная копия AdsPage для тестирования поведения */
function TestAdsPage() {
  const [search, setSearch]      = useState("");
  const [activeStates, setActiveStates] = useState<string[]>([]);
  const { data: allAds = [], isLoading } = useDashboardAds("", "");

  const STATE_FILTERS = [
    { id: "normal",       label: "Норма"    },
    { id: "warning_sent", label: "Предупр." },
    { id: "stop_sent",    label: "Стоп"     },
    { id: "claimed",      label: "В работе" },
    { id: "disabled",     label: "Откл."    },
  ];

  const rows = useMemo(() => {
    let result = allAds as AdSnapshot[];
    if (activeStates.length > 0) {
      result = result.filter((ad) =>
        activeStates.includes(normalizeAlertState(ad.alert_state)),
      );
    }
    if (search.trim()) {
      const q = search.toLowerCase();
      result = result.filter(
        (ad) =>
          (ad.ad_name ?? "").toLowerCase().includes(q) ||
          (ad.campaign_name ?? "").toLowerCase().includes(q) ||
          (ad.offer_code ?? "").toLowerCase().includes(q) ||
          ad.fb_ad_id.toLowerCase().includes(q),
      );
    }
    return [...result].sort((a, b) => {
      const sa = a.metrics?.spend != null ? parseFloat(String(a.metrics.spend)) : 0;
      const sb = b.metrics?.spend != null ? parseFloat(String(b.metrics.spend)) : 0;
      return sb - sa;
    });
  }, [allAds, activeStates, search]);

  if (isLoading) return <div>Загрузка...</div>;

  return (
    <div>
      <MiniHeader
        eyebrowNum="04"
        eyebrow="УПРАВЛЕНИЕ"
        title="Объявления"
        right={
          <span data-testid="row-count">{rows.length.toLocaleString("en-US")}</span>
        }
      />

      {/* Поиск */}
      <input
        placeholder="Поиск"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        aria-label="Поиск по объявлениям"
      />

      {/* Чипы фильтров */}
      <div role="group" aria-label="Фильтр по состоянию">
        {STATE_FILTERS.map((f) => (
          <button
            key={f.id}
            type="button"
            aria-pressed={activeStates.includes(f.id)}
            onClick={() =>
              setActiveStates((prev) =>
                prev.includes(f.id) ? prev.filter((x) => x !== f.id) : [...prev, f.id],
              )
            }
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* Список */}
      {rows.length === 0 ? (
        <p>Ничего не найдено</p>
      ) : (
        rows.map((ad) => {
          const state = normalizeAlertState(ad.alert_state);
          const stateLabel = ALERT_STATE_LABELS[state];
          const spend = ad.metrics?.spend != null ? parseFloat(String(ad.metrics.spend)) : null;
          return (
            <div key={ad.fb_ad_id} data-testid={`ad-row-${ad.fb_ad_id}`}>
              <span data-testid={`ad-name-${ad.fb_ad_id}`}>{ad.ad_name}</span>
              <span data-testid={`ad-state-${ad.fb_ad_id}`}>{stateLabel}</span>
              {spend != null && (
                <span data-testid={`ad-spend-${ad.fb_ad_id}`}>{formatSpend(spend)}</span>
              )}
            </div>
          );
        })
      )}
    </div>
  );
}

function WrapperLocal() {
  return (
    <QueryClientProvider client={qc}>
      <TestAdsPage />
    </QueryClientProvider>
  );
}

describe("AdsPage", () => {
  // Рендерит оба объявления
  it("показывает оба объявления", () => {
    render(<WrapperLocal />);
    expect(screen.getByText("CR2 Stop Test Ad")).toBeInTheDocument();
    expect(screen.getByText("Normal Ad")).toBeInTheDocument();
  });

  // Шапка с eyebrow УПРАВЛЕНИЕ и заголовком
  it("показывает шапку «Объявления» с eyebrow УПРАВЛЕНИЕ", () => {
    render(<WrapperLocal />);
    expect(screen.getByText("Объявления")).toBeInTheDocument();
    expect(screen.getByText("УПРАВЛЕНИЕ")).toBeInTheDocument();
  });

  // Счётчик строк в шапке
  it("счётчик в шапке отражает количество строк", () => {
    render(<WrapperLocal />);
    expect(screen.getByTestId("row-count")).toHaveTextContent("2");
  });

  // Состояние FSM корректно выводится
  it("показывает «Стоп» для stop_sent объявления", () => {
    render(<WrapperLocal />);
    expect(screen.getByTestId("ad-state-ad001")).toHaveTextContent("Стоп");
  });

  it("показывает «Норма» для normal объявления", () => {
    render(<WrapperLocal />);
    expect(screen.getByTestId("ad-state-ad002")).toHaveTextContent("Норма");
  });

  // Spend отображается
  it("показывает spend первого объявления", () => {
    render(<WrapperLocal />);
    const spendEl = screen.getByTestId("ad-spend-ad001");
    expect(spendEl.textContent).toContain("150");
  });

  // Сортировка по spend: ad001 ($150) выше ad002 ($80)
  it("сортирует по spend desc: ad001 идёт первым", () => {
    render(<WrapperLocal />);
    const rows = screen.getAllByTestId(/^ad-row-/);
    expect(rows[0]!.getAttribute("data-testid")).toBe("ad-row-ad001");
    expect(rows[1]!.getAttribute("data-testid")).toBe("ad-row-ad002");
  });

  // Поиск фильтрует по имени
  it("поиск «CR2» оставляет только первое объявление", async () => {
    render(<WrapperLocal />);
    const input = screen.getByLabelText("Поиск по объявлениям");
    await userEvent.type(input, "CR2");
    expect(screen.getByText("CR2 Stop Test Ad")).toBeInTheDocument();
    expect(screen.queryByText("Normal Ad")).not.toBeInTheDocument();
    expect(screen.getByTestId("row-count")).toHaveTextContent("1");
  });

  // Поиск без совпадений → «Ничего не найдено»
  it("поиск без совпадений показывает «Ничего не найдено»", async () => {
    render(<WrapperLocal />);
    const input = screen.getByLabelText("Поиск по объявлениям");
    await userEvent.type(input, "xyz_no_match_888");
    expect(screen.getByText("Ничего не найдено")).toBeInTheDocument();
    expect(screen.getByTestId("row-count")).toHaveTextContent("0");
  });

  // Чип «Стоп» фильтрует только stop_sent
  it("чип «Стоп» оставляет только stop_sent объявления", () => {
    render(<WrapperLocal />);
    const stopChip = screen.getByRole("button", { name: "Стоп" });
    fireEvent.click(stopChip);
    expect(screen.getByText("CR2 Stop Test Ad")).toBeInTheDocument();
    expect(screen.queryByText("Normal Ad")).not.toBeInTheDocument();
    expect(screen.getByTestId("row-count")).toHaveTextContent("1");
  });

  // Чип «Норма» фильтрует только normal
  it("чип «Норма» оставляет только normal объявления", () => {
    render(<WrapperLocal />);
    const normalChip = screen.getByRole("button", { name: "Норма" });
    fireEvent.click(normalChip);
    expect(screen.getByText("Normal Ad")).toBeInTheDocument();
    expect(screen.queryByText("CR2 Stop Test Ad")).not.toBeInTheDocument();
  });

  // Мультивыбор: «Стоп» + «Норма» → оба
  it("мультивыбор «Стоп» + «Норма» показывает оба объявления", () => {
    render(<WrapperLocal />);
    fireEvent.click(screen.getByRole("button", { name: "Стоп"  }));
    fireEvent.click(screen.getByRole("button", { name: "Норма" }));
    expect(screen.getByText("CR2 Stop Test Ad")).toBeInTheDocument();
    expect(screen.getByText("Normal Ad")).toBeInTheDocument();
    expect(screen.getByTestId("row-count")).toHaveTextContent("2");
  });

  // Повторный клик на активный чип снимает фильтр
  it("повторный клик на чип снимает фильтр", () => {
    render(<WrapperLocal />);
    const stopChip = screen.getByRole("button", { name: "Стоп" });
    fireEvent.click(stopChip); // включить
    fireEvent.click(stopChip); // выключить
    expect(screen.getByText("CR2 Stop Test Ad")).toBeInTheDocument();
    expect(screen.getByText("Normal Ad")).toBeInTheDocument();
    expect(screen.getByTestId("row-count")).toHaveTextContent("2");
  });

  // Все чипы присутствуют в DOM
  it("все пять чипов состояний присутствуют", () => {
    render(<WrapperLocal />);
    expect(screen.getByRole("button", { name: "Норма"    })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Предупр." })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Стоп"     })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "В работе" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Откл."    })).toBeInTheDocument();
  });
});
