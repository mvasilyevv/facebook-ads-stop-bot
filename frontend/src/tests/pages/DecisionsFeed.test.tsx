import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  OperatorAdRow,
  OperatorAttentionItem,
  OperatorSnapshot,
} from "@fb/shared/operator/contracts";
import { makeOperatorSnapshot } from "@fb/shared/operator/testFixture";
import { OperatorRealtimeStatusProvider, type OperatorRealtimeStatus } from "@fb/operator-api";

const navigate = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
  Link: ({ to, children }: { to: string; children: ReactNode }) => <a href={to}>{children}</a>,
}));

const fetchOperatorAdForCommand = vi.fn();
const pauseMutate = vi.fn();
const acknowledgeMutate = vi.fn();
let pausePending = false;
let acknowledgePending = false;

vi.mock("@/lib/api/operator", () => ({
  fetchOperatorAdForCommand: (...args: unknown[]) => fetchOperatorAdForCommand(...args),
  usePauseOperatorAd: () => ({ mutateAsync: pauseMutate, isPending: pausePending }),
  useAcknowledgeOperatorIncident: () => ({ mutateAsync: acknowledgeMutate, isPending: acknowledgePending }),
  operatorProblemMessage: (error: unknown) =>
    error instanceof Error ? error.message : "Неизвестная ошибка",
}));

const commandToast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
}));

vi.mock("@/components/ui/Toast", () => ({
  toast: commandToast,
  ToastViewport: () => null,
}));

import { DecisionsFeed } from "@/features/decisions/DecisionsFeed";

function attentionItem(overrides: Partial<OperatorAttentionItem>): OperatorAttentionItem {
  return {
    id: "row-1",
    kind: "incident",
    severity: "warning",
    title: "Сигнал требует проверки",
    summary: "Сводка",
    reason: null,
    occurred_at: "2026-07-18T08:00:00Z",
    target: { kind: "ad", id: null, label: "Объект" },
    action: null,
    recovery_action: null,
    status: null,
    requires_usd_evidence: false,
    ...overrides,
  };
}

// D: критичный источник (money-rank «система», без действия).
const sourceRow = attentionItem({
  id: "source:meta_stale",
  kind: "source",
  severity: "critical",
  title: "Источник Meta не отвечает",
  summary: "Данные устарели.",
  occurred_at: "2026-07-18T07:00:00Z",
  target: { kind: "system", id: null, label: "Meta" },
  action: { label: "Диагностика", href: "/system/sources" },
});

// C: неподтверждённая команда (unknown ранжируется выше warning).
const actionRow = attentionItem({
  id: "task:9001",
  kind: "action",
  severity: "unknown",
  title: "Команда требует сверки",
  summary: "#9001 · unknown",
  occurred_at: "2026-07-18T07:30:00Z",
  target: { kind: "ad", id: null, label: "GH_VIP" },
  action: { label: "Открыть", href: "/actions/9001" },
});

// A: инцидент на объявлении с числовым id — получает «Отключить».
const pausableIncidentRow = attentionItem({
  id: "incident-a",
  kind: "incident",
  severity: "warning",
  title: "CPL выше базы",
  summary: "CPL $9.56 при базе $3.00.",
  reason: "Расход растёт без FTD",
  occurred_at: "2026-07-18T08:00:00Z",
  target: { kind: "ad", id: "111", label: "GH_CR2" },
  action: { label: "Открыть объявление", href: "/ads/111" },
  status: "open",
  requires_usd_evidence: true,
});

// B: инцидент на кабинете (не numeric ad) — получает «Подтвердить».
const acknowledgeableIncidentRow = attentionItem({
  id: "incident-b",
  kind: "incident",
  severity: "warning",
  title: "Кабинет требует проверки",
  summary: "Странная активность",
  reason: "Подозрение на разлогин",
  occurred_at: "2026-07-18T09:00:00Z",
  target: { kind: "account", id: null, label: "GH_ACCOUNT" },
  action: { label: "Открыть кабинет", href: "/cabinets/123" },
  status: "open",
});

function makeSnapshot(
  items: OperatorAttentionItem[],
  overrides: {
    attentionState?: OperatorSnapshot["attention"]["state"];
    total?: number;
    truncated?: boolean;
  } = {},
): OperatorSnapshot {
  const base = makeOperatorSnapshot();
  return {
    ...base,
    attention: {
      ...base.attention,
      state: overrides.attentionState ?? "ready",
      issues: [],
      data:
        overrides.attentionState === "unavailable"
          ? null
          : {
              items,
              total: overrides.total ?? items.length,
              truncated: overrides.truncated ?? false,
              decisions_count: items.length,
              decisions_critical: false,
            },
    },
    actions: {
      ...base.actions,
      state: "ready",
      data: {
        items: [
          {
            ...base.actions.data!.items[0]!,
            id: "9001",
            public_id: "#9001",
            state: "unknown",
          },
        ],
      },
    },
  };
}

function makeAd(overrides: Partial<OperatorAdRow> = {}): OperatorAdRow {
  return {
    id: "row-111",
    fb_ad_id: "111",
    name: "GH_CR2 объявление",
    campaign_id: "campaign-1",
    campaign_name: "Кампания",
    adset_id: "adset-1",
    adset_name: "Адсет",
    account_id: "act_1",
    delivery_status: "ACTIVE",
    data_state: "ready",
    severity: "warning",
    as_of: "2026-07-18T10:00:00Z",
    metrics: {
      spend: "12.50",
      impressions: 100,
      clicks: 0,
      registrations: null,
      ftd: 0,
      confirmed_deposits: 0,
      cpc: null,
      cost_per_registration: null,
      frequency: "1.84",
      cost_per_ftd: null,
    },
    rule_context: {
      offer_code: "GH_CR2",
      rule_code: "cpr_stop",
      rule_title: "Дорогая рега",
      value: "0.41",
      threshold: "0.48",
      percent_to_stop: "85.41",
      stage: "warning",
    },
    active_action: null,
    ...overrides,
  };
}

function renderFeed(
  snapshot: OperatorSnapshot,
  options: { status?: OperatorRealtimeStatus; realtimeConnected?: boolean; now?: Date } = {},
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const status = options.status ?? "connected";
  return render(
    <QueryClientProvider client={client}>
      <OperatorRealtimeStatusProvider status={status}>
        <DecisionsFeed
          snapshot={snapshot}
          realtimeConnected={options.realtimeConnected ?? status === "connected"}
          now={options.now ?? new Date("2026-07-18T11:00:00Z")}
        />
      </OperatorRealtimeStatusProvider>
    </QueryClientProvider>,
  );
}

describe("лента «Решения»", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    pausePending = false;
    acknowledgePending = false;
    pauseMutate.mockResolvedValue({
      task_id: 1842,
      public_id: "#1842",
      created: true,
      state: "queued",
    });
    acknowledgeMutate.mockResolvedValue({});
    fetchOperatorAdForCommand.mockResolvedValue(makeAd());
  });

  it("сортирует строки как compareDecisionRows: critical → unknown → старейшее warning", () => {
    // Порядок во входном снимке нарочно перемешан — сортирует компонент.
    const snapshot = makeSnapshot([
      acknowledgeableIncidentRow,
      pausableIncidentRow,
      sourceRow,
      actionRow,
    ]);
    renderFeed(snapshot);

    const rows = screen.getAllByRole("listitem");
    const labels = rows.map(
      (row) => within(row).getByText(/^(Meta|GH_VIP|GH_CR2|GH_ACCOUNT)$/).textContent,
    );
    expect(labels).toEqual(["Meta", "GH_VIP", "GH_CR2", "GH_ACCOUNT"]);
  });

  it("показывает возраст строки", () => {
    const snapshot = makeSnapshot([pausableIncidentRow]);
    renderFeed(snapshot, { now: new Date("2026-07-18T11:00:00Z") });

    expect(screen.getByText("Висит 3 ч")).toBeInTheDocument();
  });

  it("даёт «Отключить» только там, где decisionPrimaryAction его возвращает", () => {
    const snapshot = makeSnapshot([pausableIncidentRow, acknowledgeableIncidentRow]);
    renderFeed(snapshot);

    expect(screen.getByRole("button", { name: "Отключить" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Подтвердить" })).toBeInTheDocument();
    // На строке с «Отключить» нет второй кнопки «Подтвердить», и наоборот.
    expect(screen.getAllByRole("button", { name: "Отключить" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Подтвердить" })).toHaveLength(1);
  });

  it("не показывает действие для строки источника (kind=source)", () => {
    const snapshot = makeSnapshot([sourceRow]);
    renderFeed(snapshot);

    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Проверить в Meta/ })).not.toBeInTheDocument();
  });

  it("даёт ссылку «Проверить в Meta» для неподтверждённой команды и не выдаёт её за выполненное действие", () => {
    const snapshot = makeSnapshot([actionRow]);
    renderFeed(snapshot);

    const link = screen.getByRole("link", { name: /Проверить в Meta/ });
    expect(link).toHaveAttribute("href", "/actions/9001");
  });

  it("отправляет отключение тем же путём, что и /ads: Idempotency-Key, заголовок и expected_* из свежего чтения", async () => {
    const user = userEvent.setup();
    const snapshot = makeSnapshot([pausableIncidentRow]);
    renderFeed(snapshot);

    await user.click(screen.getByRole("button", { name: "Отключить" }));
    const dialog = await screen.findByRole("dialog", { name: "Отключить объявление?" });
    await user.click(within(dialog).getByRole("button", { name: "Отключить" }));

    await waitFor(() => expect(pauseMutate).toHaveBeenCalledOnce());
    const request = pauseMutate.mock.calls[0]?.[0] as {
      params: { path: { ad_id: string }; header: Record<string, string> };
      body: { expected_delivery_status: string; expected_as_of: string };
    };
    expect(request.params.path.ad_id).toBe("111");
    expect(request.params.header["Idempotency-Key"]).toMatch(/^[0-9a-f-]{36}$/i);
    expect(request.params.header["X-Operator-Principal"]).toBe("operator:web");
    expect(request.body).toEqual({
      expected_delivery_status: "ACTIVE",
      expected_as_of: "2026-07-18T10:00:00Z",
    });
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: "/actions/$actionId",
        params: { actionId: "1842" },
      }),
    );
  });

  it("никогда не красит поставленную в очередь (202) команду зелёным", async () => {
    const user = userEvent.setup();
    const snapshot = makeSnapshot([pausableIncidentRow]);
    renderFeed(snapshot);

    await user.click(screen.getByRole("button", { name: "Отключить" }));
    const dialog = await screen.findByRole("dialog", { name: "Отключить объявление?" });
    await user.click(within(dialog).getByRole("button", { name: "Отключить" }));

    await waitFor(() => expect(commandToast.info).toHaveBeenCalledOnce());
    expect(commandToast.success).not.toHaveBeenCalled();
  });

  it("ack идемпотентен на двойной клик: вторая команда не уходит, пока первая ещё не завершилась", async () => {
    let resolveAck: (() => void) | undefined;
    acknowledgeMutate.mockImplementation(
      () =>
        new Promise<Record<string, never>>((resolve) => {
          resolveAck = () => resolve({});
        }),
    );
    const user = userEvent.setup();
    const snapshot = makeSnapshot([acknowledgeableIncidentRow]);
    renderFeed(snapshot);

    const button = screen.getByRole("button", { name: "Подтвердить" });
    await user.click(button);
    await user.click(button);

    expect(acknowledgeMutate).toHaveBeenCalledOnce();
    resolveAck?.();
  });

  it("подтверждение инцидента идёт без диалога и с тем же заголовком принципала", async () => {
    const user = userEvent.setup();
    const snapshot = makeSnapshot([acknowledgeableIncidentRow]);
    renderFeed(snapshot);

    await user.click(screen.getByRole("button", { name: "Подтвердить" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(acknowledgeMutate).toHaveBeenCalledOnce());
    expect(acknowledgeMutate).toHaveBeenCalledWith({
      params: {
        path: { incident_id: "incident-b" },
        header: { "X-Operator-Principal": "operator:web" },
      },
    });
  });

  it("показывает «Решений нет» как хорошую новость на подтверждённой пустой ленте", () => {
    const snapshot = makeSnapshot([]);
    renderFeed(snapshot);

    expect(screen.getByText("Решений нет")).toBeInTheDocument();
    expect(screen.queryByText(/не подтверждена/)).not.toBeInTheDocument();
  });

  it("не путает неподтверждённую пустоту с подтверждённым нулём", () => {
    const snapshot = makeSnapshot([], { attentionState: "unavailable" });
    renderFeed(snapshot);

    expect(screen.queryByText("Решений нет")).not.toBeInTheDocument();
    expect(screen.getByText("Лента не подтверждена")).toBeInTheDocument();
  });

  it("показывает заметную строку об усечении лимитом сервера, а не сноску", () => {
    const snapshot = makeSnapshot([pausableIncidentRow], { total: 63, truncated: true });
    renderFeed(snapshot);

    // role="status" — заметный баннер, а не мелкая сноска в углу карточки.
    // (role=status не задаёт accessible name из содержимого, поэтому ищем без name-фильтра.)
    const notice = screen.getByRole("status");
    expect(notice).toHaveTextContent("Показано 1 из 63");
    expect(within(notice).getByRole("link", { name: "журнале инцидентов" })).toHaveAttribute(
      "href",
      "/incidents",
    );
    expect(within(notice).getByRole("link", { name: "логе действий" })).toHaveAttribute(
      "href",
      "/actions",
    );
  });

  it("не показывает строку усечения, когда лимит не достигнут", () => {
    const snapshot = makeSnapshot([pausableIncidentRow], { total: 1, truncated: false });
    renderFeed(snapshot);

    expect(screen.queryByText(/Показано/)).not.toBeInTheDocument();
  });

  it("недоступный источник (unavailable) не выглядит зелёным", () => {
    const snapshot = makeSnapshot([], { attentionState: "unavailable" });
    const { container } = renderFeed(snapshot);

    const badge = container.querySelector(".operator-state-badge");
    expect(badge).not.toBeNull();
    // "confirmed" — тон подтверждённого/готового состояния; unavailable им быть не может.
    expect(badge).not.toHaveAttribute("data-tone", "confirmed");
    expect(badge).toHaveAttribute("data-tone", "unavailable");
    expect(screen.getByText("Лента не подтверждена")).toBeInTheDocument();
  });

  it("частично подтверждённая (partial) лента показывает нотис и не красит бейдж зелёным", () => {
    const snapshot = makeSnapshot([pausableIncidentRow], { attentionState: "partial" });
    const { container } = renderFeed(snapshot);

    const badge = container.querySelector(".operator-state-badge");
    expect(badge).not.toHaveAttribute("data-tone", "confirmed");
    expect(badge).toHaveAttribute("data-tone", "degraded");
    expect(screen.getAllByText("Данные неполные").length).toBeGreaterThanOrEqual(1);
    // При partial money-действия выключены во всей ленте.
    expect(screen.queryByRole("button", { name: "Отключить" })).not.toBeInTheDocument();
    expect(screen.getByText("Действие недоступно до сверки live-снимка")).toBeInTheDocument();
  });

  it("выключает действия, пока realtime-канал не подключён (офлайн)", () => {
    const snapshot = makeSnapshot([pausableIncidentRow]);
    renderFeed(snapshot, { status: "reconnecting", realtimeConnected: false });

    expect(screen.queryByRole("button", { name: "Отключить" })).not.toBeInTheDocument();
    expect(screen.getByText("Действие недоступно до сверки live-снимка")).toBeInTheDocument();
  });

  it("гасит денежную копию строки по её собственному requires_usd_evidence, а не по глобальной валюте", () => {
    const base = makeOperatorSnapshot();
    const unconfirmedCurrencySnapshot: OperatorSnapshot = {
      ...makeSnapshot([pausableIncidentRow]),
      meta: { ...base.meta, currency_state: "unknown", currency: null },
    };
    renderFeed(unconfirmedCurrencySnapshot);

    expect(screen.getByText("Денежный сигнал требует проверки")).toBeInTheDocument();
    expect(screen.queryByText("CPL выше базы")).not.toBeInTheDocument();
  });
});
