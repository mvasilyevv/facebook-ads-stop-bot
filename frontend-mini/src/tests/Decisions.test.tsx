/**
 * Тесты ленты «Решения» (issue #338, PR4/mini): порядок строк совпадает с
 * compareDecisionRows, возраст виден, money-действие только там, где
 * decisionPrimaryAction его вернул, ack идемпотентен, пустая лента,
 * усечение видно, unavailable не выглядит зелёным.
 */
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  OperatorAdRow,
  OperatorAdsResponse,
  OperatorSnapshot,
} from "@fb/shared/operator/contracts";
import { makeOperatorSnapshot } from "@fb/shared/operator/testFixture";

const navigate = vi.fn();
const storeResolvedNavigation = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
  Link: ({ children }: { children: ReactNode }) => <span>{children}</span>,
}));

vi.mock("@/lib/transientNavigation", () => ({
  storeResolvedNavigation: (...args: unknown[]) =>
    storeResolvedNavigation(...args),
  parseTmaAttentionHref: (href: string) => {
    const match = /^\/(ads|actions|incidents)\/([^/?#]+)$/.exec(href);
    if (!match) return null;
    const targetKind =
      match[1] === "ads" ? "ad" : match[1] === "actions" ? "action" : "incident";
    return { kind: "target", target: { target_kind: targetKind, target_id: match[2] } };
  },
}));

vi.mock("@fb/operator-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@fb/operator-api")>()),
  useOperatorRealtimeStatus: () => "connected",
}));

const tgAlert = vi.fn();
vi.mock("@/lib/tg", () => ({
  haptic: { impact: vi.fn(), notify: vi.fn(), selection: vi.fn() },
  tgConfirm: vi.fn(async () => true),
  tgAlert: (...args: unknown[]) => tgAlert(...args),
  // PullToRefresh (обёртка ленты) вызывает эти два при монтировании/размонтировании.
  disableVerticalSwipes: vi.fn(),
  enableVerticalSwipes: vi.fn(),
}));

let snapshotState: { data: OperatorSnapshot | undefined; isError: boolean; error: unknown };
const snapshotRefetch = vi.fn(async () => ({}));
const acknowledgeMutate = vi.fn(async () => ({}));
const pauseMutate = vi.fn(async () => ({
  task_id: 501,
  public_id: "#501",
  manual_review_available: false,
  created: true,
}));
const activateMutate = vi.fn(async () => ({}));
const fetchOperatorAdForCommand = vi.fn();
let adsResponses: Record<string, OperatorAdsResponse> = {};

vi.mock("@/lib/operatorApi", () => ({
  useOperatorSnapshot: () => snapshotState,
  useOperatorAds: (query: { search?: string }) => {
    const response = query.search ? adsResponses[query.search] : undefined;
    return {
      data: response,
      isPending: response === undefined,
      isError: false,
      error: null,
    };
  },
  useAcknowledgeOperatorIncident: () => ({ mutateAsync: acknowledgeMutate }),
  usePauseOperatorAd: () => ({ mutateAsync: pauseMutate, isPending: false }),
  useActivateOperatorAd: () => ({ mutateAsync: activateMutate, isPending: false }),
  fetchOperatorAdForCommand: (...args: unknown[]) =>
    fetchOperatorAdForCommand(...args),
  operatorProblemMessage: () => "Сервер не подтвердил данные",
  operatorIncidentProblemMessage: () => "Журнал временно недоступен",
}));

import { MiniDecisionsPage } from "@/features/decisions/DecisionsFeed";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MiniDecisionsPage />
    </QueryClientProvider>,
  );
}

function baseAttentionItem(
  overrides: Partial<OperatorSnapshot["attention"]["data"] extends null ? never : NonNullable<OperatorSnapshot["attention"]["data"]>["items"][number]>,
) {
  return {
    id: "incident-1",
    kind: "incident" as const,
    severity: "warning" as const,
    title: "Сигнал требует проверки",
    summary: "Расход растёт без FTD",
    reason: null,
    occurred_at: "2026-07-18T10:00:00Z",
    target: { kind: "account" as const, id: null, label: "Кабинет GH" },
    action: { label: "Открыть", href: "/incidents/incident-1" },
    recovery_action: null,
    status: "open" as const,
    requires_usd_evidence: false,
    ...overrides,
  };
}

function makeAdRow(overrides: Partial<OperatorAdRow> = {}): OperatorAdRow {
  return {
    id: "row-301",
    fb_ad_id: "301",
    name: "GH | Stop Test",
    campaign_id: "campaign-1",
    campaign_name: "GH | MV | 07.06",
    adset_id: "adset-1",
    adset_name: "adset-1",
    account_id: "act_123",
    delivery_status: "ACTIVE",
    data_state: "ready",
    severity: "warning",
    as_of: "2026-07-18T10:00:00Z",
    metrics: {
      spend: "10.00",
      impressions: 100,
      clicks: 10,
      registrations: 1,
      ftd: 0,
      confirmed_deposits: 0,
      cpc: "1.00",
      cost_per_registration: "10.00",
      frequency: "1.00",
      cost_per_ftd: null,
    },
    rule_context: {
      offer_code: null,
      rule_code: null,
      rule_title: null,
      value: null,
      threshold: null,
      percent_to_stop: null,
      stage: "none",
    },
    active_action: null,
    ...overrides,
  } as OperatorAdRow;
}

function adsResponseFor(row: OperatorAdRow): OperatorAdsResponse {
  return {
    state: "ready",
    as_of: row.as_of,
    freshness_seconds: 10,
    sources: ["meta"],
    issues: [],
    scope: {
      account_ids: ["act_123"],
      display_timezone: "Europe/Kaliningrad",
      cabinet_timezone: "Europe/Kaliningrad",
      cabinet_timezone_state: "single",
      missing_timezone_account_ids: [],
      currency: "USD",
      currency_state: "single",
      missing_currency_account_ids: [],
      currency_observed_at: row.as_of,
    },
    rows: [row],
    page: 1,
    page_size: 10,
    total: 1,
    pages: 1,
  } as OperatorAdsResponse;
}

describe("TMA decisions feed", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    snapshotRefetch.mockClear();
    adsResponses = { "301": adsResponseFor(makeAdRow()) };
    snapshotState = {
      data: {
        ...makeOperatorSnapshot(),
        attention: {
          state: "ready",
          as_of: "2026-07-18T10:14:45Z",
          freshness_seconds: 15,
          sources: ["observer"],
          issues: [],
          data: { items: [], total: 0, truncated: false, decisions_count: 0, decisions_critical: false },
        },
      },
      isError: false,
      error: null,
    };
    // @ts-expect-error mock hook object augmented ad hoc in tests
    snapshotState.refetch = snapshotRefetch;
  });

  it("сортирует строки как compareDecisionRows: critical перед warning, старейшее первым", () => {
    snapshotState.data = {
      ...snapshotState.data!,
      attention: {
        ...snapshotState.data!.attention,
        data: {
          items: [
            baseAttentionItem({
              id: "warn-old",
              severity: "warning",
              target: { kind: "account", id: null, label: "Warn Old" },
              occurred_at: "2026-07-18T09:00:00Z",
            }),
            baseAttentionItem({
              id: "critical-new",
              severity: "critical",
              target: { kind: "account", id: null, label: "Critical New" },
              occurred_at: "2026-07-18T10:00:00Z",
            }),
            baseAttentionItem({
              id: "warn-new",
              severity: "warning",
              target: { kind: "account", id: null, label: "Warn New" },
              occurred_at: "2026-07-18T09:30:00Z",
            }),
          ],
          total: 3,
          truncated: false,
          decisions_count: 0,
          decisions_critical: false,
        },
      },
    };
    // @ts-expect-error same ad-hoc refetch augmentation
    snapshotState.refetch = snapshotRefetch;

    renderPage();

    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent("Critical New");
    expect(items[1]).toHaveTextContent("Warn Old");
    expect(items[2]).toHaveTextContent("Warn New");
  });

  it("показывает возраст строки", () => {
    snapshotState.data = {
      ...snapshotState.data!,
      meta: { ...snapshotState.data!.meta, generated_at: "2026-07-18T13:00:00Z" },
      attention: {
        ...snapshotState.data!.attention,
        data: {
          items: [
            baseAttentionItem({
              occurred_at: "2026-07-18T10:00:00Z",
            }),
          ],
          total: 1,
          truncated: false,
          decisions_count: 0,
          decisions_critical: false,
        },
      },
    };
    // @ts-expect-error same ad-hoc refetch augmentation
    snapshotState.refetch = snapshotRefetch;

    renderPage();

    expect(screen.getByText(/Висит/)).toBeInTheDocument();
  });

  it("money-действие «Отключить» только там, где decisionPrimaryAction вернул pause", () => {
    snapshotState.data = {
      ...snapshotState.data!,
      attention: {
        ...snapshotState.data!.attention,
        data: {
          items: [
            baseAttentionItem({
              id: "pause-row",
              target: { kind: "ad", id: "301", label: "Объявление 301" },
            }),
            baseAttentionItem({
              id: "ack-row",
              target: { kind: "account", id: null, label: "Кабинет без ad-цели" },
            }),
          ],
          total: 2,
          truncated: false,
          decisions_count: 0,
          decisions_critical: false,
        },
      },
    };
    // @ts-expect-error same ad-hoc refetch augmentation
    snapshotState.refetch = snapshotRefetch;

    renderPage();

    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    const pauseRow = rows.find((row) => row.textContent?.includes("Объявление 301"))!;
    const ackRow = rows.find((row) => row.textContent?.includes("Кабинет без ad-цели"))!;
    expect(pauseRow).toBeDefined();
    expect(ackRow).toBeDefined();
    // Только у строки с числовым ad-таргетом есть кнопка «Отключить».
    expect(within(pauseRow).getByRole("button", { name: /Отключить/ })).toBeInTheDocument();
    expect(within(pauseRow).queryByRole("button", { name: /Подтвердить/ })).not.toBeInTheDocument();
    expect(within(ackRow).getByRole("button", { name: /Подтвердить/ })).toBeInTheDocument();
    expect(within(ackRow).queryByRole("button", { name: /Отключить/ })).not.toBeInTheDocument();
  });

  it("ack идемпотентен: после подтверждения кнопка исчезает и повторный клик невозможен", async () => {
    const openItem = baseAttentionItem({ id: "incident-open", status: "open" });
    const closedItem = { ...openItem, status: "acknowledged" as const };
    snapshotState.data = {
      ...snapshotState.data!,
      attention: {
        ...snapshotState.data!.attention,
        data: { items: [openItem], total: 1, truncated: false, decisions_count: 0, decisions_critical: false },
      },
    };
    // @ts-expect-error same ad-hoc refetch augmentation
    snapshotState.refetch = snapshotRefetch.mockImplementation(async () => {
      snapshotState.data = {
        ...snapshotState.data!,
        attention: {
          ...snapshotState.data!.attention,
          data: { items: [closedItem], total: 1, truncated: false, decisions_count: 0, decisions_critical: false },
        },
      };
      return {};
    });

    renderPage();

    fireEvent.click(screen.getByRole("button", { name: /Подтвердить/ }));

    await waitFor(() => expect(acknowledgeMutate).toHaveBeenCalledTimes(1));
    expect(acknowledgeMutate).toHaveBeenCalledWith({
      params: {
        path: { incident_id: "incident-open" },
        header: { "X-Operator-Principal": "operator:tma" },
      },
    });
    expect(snapshotRefetch).toHaveBeenCalledTimes(1);
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: /Подтвердить/ }),
      ).not.toBeInTheDocument(),
    );
  });

  it("пустая лента — хорошая новость, не ошибка", () => {
    snapshotState.data = {
      ...snapshotState.data!,
      attention: {
        ...snapshotState.data!.attention,
        state: "empty",
        data: { items: [], total: 0, truncated: false, decisions_count: 0, decisions_critical: false },
      },
    };
    // @ts-expect-error same ad-hoc refetch augmentation
    snapshotState.refetch = snapshotRefetch;

    renderPage();

    expect(screen.getByText("Решений нет")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("показывает заметную строку усечения при truncated=true", () => {
    snapshotState.data = {
      ...snapshotState.data!,
      attention: {
        ...snapshotState.data!.attention,
        data: {
          items: [baseAttentionItem({})],
          total: 57,
          truncated: true,
          decisions_count: 0,
          decisions_critical: false,
        },
      },
    };
    // @ts-expect-error same ad-hoc refetch augmentation
    snapshotState.refetch = snapshotRefetch;

    renderPage();

    expect(screen.getByText(/Показано/)).toHaveTextContent("57");
  });

  it("unavailable никогда не выглядит зелёным (confirmed)", () => {
    snapshotState.data = {
      ...snapshotState.data!,
      attention: {
        state: "unavailable",
        as_of: null,
        freshness_seconds: null,
        sources: [],
        issues: [
          {
            code: "attention_unavailable",
            title: "Лента решений недоступна",
            detail: null,
            severity: "unknown",
            correlation_id: null,
          },
        ],
        data: null,
      },
    };
    // @ts-expect-error same ad-hoc refetch augmentation
    snapshotState.refetch = snapshotRefetch;

    renderPage();

    const badge = document.querySelector('[data-state="unavailable"]');
    expect(badge).not.toBeNull();
    expect(badge).not.toHaveAttribute("data-tone", "confirmed");
    expect(screen.getByText("Лента не подтверждена")).toBeInTheDocument();
  });
});
