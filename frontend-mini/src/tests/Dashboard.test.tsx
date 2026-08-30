import type { ComponentType, ReactNode } from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { OperatorSnapshot } from "@fb/shared/operator/contracts";
import { makeOperatorSnapshot } from "@fb/shared/operator/testFixture";

const mockUseOperatorSnapshot = vi.fn();
const mockUseOperatorCabinetSnapshot = vi.fn();
const mockScan = vi.fn();
const mockNavigate = vi.fn();
const mockUseOperatorRealtimeStatus = vi.fn(() => "connected");
const { mockHapticNotify } = vi.hoisted(() => ({
  mockHapticNotify: vi.fn(),
}));

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => options,
  useNavigate: () => mockNavigate,
  Link: ({
    children,
    to,
    params,
    ...props
  }: {
    children: ReactNode;
    to: string;
    params?: Record<string, string>;
  }) => (
    <a href={to.replace("$actionId", params?.actionId ?? "")} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("@/lib/operatorApi", () => ({
  useOperatorSnapshot: (...args: unknown[]) => mockUseOperatorSnapshot(...args),
  useOperatorCabinetSnapshot: (...args: unknown[]) =>
    mockUseOperatorCabinetSnapshot(...args),
  useOperatorRetryScan: () => ({ mutateAsync: mockScan, isPending: false }),
  operatorProblemMessage: (error: unknown) =>
    error instanceof Error ? error.message : "Ошибка",
}));

vi.mock("@fb/operator-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@fb/operator-api")>()),
  useOperatorRealtimeStatus: () => mockUseOperatorRealtimeStatus(),
}));

vi.mock("@/lib/tg", () => ({
  haptic: { impact: vi.fn(), notify: mockHapticNotify, selection: vi.fn() },
  tgAlert: vi.fn(),
  // PullToRefresh (обёртка роута «/») — вне жеста эти вызовы no-op.
  disableVerticalSwipes: vi.fn(),
  enableVerticalSwipes: vi.fn(),
}));

import { Route } from "@/routes/index";
import { readResolvedNavigation } from "@/lib/transientNavigation";

const Dashboard = (Route as unknown as { component: ComponentType }).component;

function approachingRow(
  fbAdId: string,
  percentToStop: string,
): NonNullable<OperatorSnapshot["approaching_stop"]["data"]>["items"][number] {
  return {
    id: `row-${fbAdId}`,
    fb_ad_id: fbAdId,
    name: `Объявление ${fbAdId}`,
    campaign_id: "campaign-1",
    campaign_name: "GH · CR2",
    adset_id: "adset-1",
    adset_name: "Broad",
    account_id: "act_123",
    delivery_status: "ACTIVE",
    data_state: "ready",
    severity: "warning",
    as_of: "2026-07-18T10:14:45Z",
    metrics: {
      spend: "12.50",
      impressions: 1000,
      clicks: 30,
      registrations: 3,
      ftd: 0,
      confirmed_deposits: 0,
      cpc: "0.41",
      cost_per_registration: "4.16",
      frequency: "1.84",
      cost_per_ftd: null,
    },
    rule_context: {
      offer_code: "GH_CR2",
      rule_code: "cpr_stop",
      rule_title: "Дорогая рега",
      value: "0.41",
      threshold: "0.48",
      percent_to_stop: percentToStop,
      stage: "warning",
    },
    active_action: null,
  };
}

function makeReloginSnapshot(
  scanState?: "queued" | "running" | "failed" | "cancelled",
) {
  const snapshot = makeOperatorSnapshot();
  snapshot.attention.data!.items[0] = {
    ...snapshot.attention.data!.items[0]!,
    id: "login-incident-1",
    severity: "critical",
    title: "В Facebook нужно войти снова",
    summary: "Кабинет: 123",
    occurred_at: "2026-07-18T10:14:00Z",
    recovery_action: "retry_scan",
  };
  if (scanState) {
    snapshot.actions.data!.items.push({
      id: "1843",
      public_id: "#1843",
      kind: "scan",
      state: scanState,
      title: "Сканирование",
      target_id: null,
      target_label: null,
      requested_at: "2026-07-18T10:14:10Z",
      updated_at: "2026-07-18T10:14:20Z",
      requested_by: "operator:tma",
      reason: null,
      correlation_id: "corr-scan",
      account_id: null,
      currency: null,
      cabinet_timezone: null,
      account_context_observed_at: null,
      account_context_issues: [],
    });
  }
  return snapshot;
}

describe("TMA operator dashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    window.sessionStorage.clear();
    mockUseOperatorRealtimeStatus.mockReturnValue("connected");
    mockUseOperatorSnapshot.mockReturnValue({
      data: makeOperatorSnapshot(),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    mockUseOperatorCabinetSnapshot.mockReturnValue({
      data: makeOperatorSnapshot(),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
  });

  it("opens attention targets through opaque TMA navigation", async () => {
    render(<Dashboard />);

    await userEvent.click(
      screen.getByRole("button", { name: "Открыть объявление" }),
    );

    expect(readResolvedNavigation()).toEqual({
      target_kind: "ad",
      target_id: "ad-1",
    });
    expect(mockNavigate).toHaveBeenCalledWith({ to: "/open" });
    expect(window.location.href).not.toContain("ad-1");
  });

  it("reads a confirmed empty approaching-stop section as calm, not alarming", () => {
    render(<Dashboard />);

    const section = screen.getByRole("region", { name: "Подходят к стопу" });
    expect(within(section).getByText("никто не подходит")).toBeInTheDocument();
    expect(
      within(section).getByText("Ни одно объявление не подходит к стопу."),
    ).toBeInTheDocument();
    expect(section).not.toHaveTextContent("Источник недоступен");
  });

  it("ranks approaching ads with their rule, threshold and share of the way", () => {
    const snapshot = makeOperatorSnapshot();
    snapshot.approaching_stop = {
      ...snapshot.approaching_stop,
      state: "ready",
      data: { items: [approachingRow("ad-9", "93.40")] },
    };
    mockUseOperatorSnapshot.mockReturnValue({
      data: snapshot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<Dashboard />);

    const section = screen.getByRole("region", { name: "Подходят к стопу" });
    expect(within(section).getByText("93.4%")).toBeInTheDocument();
    expect(within(section).getByText("Подходит к стопу")).toBeInTheDocument();
    expect(
      within(section).getByText("Дорогая рега · $0.41 из $0.48"),
    ).toBeInTheDocument();
  });

  it("keeps the full approaching-stop count visible when the ledger is capped", () => {
    const snapshot = makeOperatorSnapshot();
    snapshot.approaching_stop = {
      ...snapshot.approaching_stop,
      state: "ready",
      data: {
        items: Array.from({ length: 7 }, (_, index) =>
          approachingRow(`ad-${index + 1}`, String(99 - index)),
        ),
      },
    };
    mockUseOperatorSnapshot.mockReturnValue({
      data: snapshot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<Dashboard />);

    const section = screen.getByRole("region", { name: "Подходят к стопу" });
    expect(within(section).getByText("7 объявлений")).toBeInTheDocument();
    expect(
      within(section).getAllByRole("link", { name: /Открыть объявление:/ }),
    ).toHaveLength(5);
    expect(
      within(section).getByRole("link", {
        name: "Все объявления по близости к стопу",
      }),
    ).toHaveAttribute("href", "/ads");
  });

  it("keeps the action-first ledger order on the compact shell", () => {
    render(<Dashboard />);
    const headings = screen
      .getAllByRole("heading", { level: 2 })
      .map((heading) => heading.textContent);
    // Ранний контур встал сразу после сигналов, но инвариант action-first —
    // внимание → портфель → действия → воронка — не изменился.
    expect(headings).toEqual([
      "Требует внимания",
      "Подходят к стопу",
      "Портфель",
      "Действия",
      "Воронка",
    ]);
    expect(screen.queryByText("Накопительный расход")).not.toBeInTheDocument();
  });

  it("renders action-first sections without a permanent scan control", () => {
    render(<Dashboard />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Сейчас",
    );
    expect(screen.getByText("FB Agent · оператор")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Требует внимания", level: 2 }),
    ).toBeInTheDocument();
    expect(screen.getByText("CPL выше базы")).toBeInTheDocument();
    expect(screen.getAllByText("Требует внимания").length).toBeGreaterThan(1);
    expect(
      screen.queryByRole("button", { name: /скан/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("$47.80")).toBeInTheDocument();
    expect(screen.getByText("$39.00")).toBeInTheDocument();
    expect(screen.getByText("$45.00")).toBeInTheDocument();
    expect(screen.getAllByText("$0").length).toBeGreaterThan(0);
    expect(screen.getByText("$20")).toBeInTheDocument();
  });

  it("does not expose action correlation UUIDs", () => {
    const snapshot = makeOperatorSnapshot();
    snapshot.actions.data!.items[0]!.correlation_id =
      "8b8d0c93-15dc-46b4-8fe0-8da6bec3667f";
    snapshot.actions.data!.items[0]!.reason =
      "Traceback: secret-host token=unsafe";
    mockUseOperatorSnapshot.mockReturnValue({
      data: snapshot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<Dashboard />);

    expect(screen.getByText("Задача #1842")).toBeInTheDocument();
    expect(
      screen.getByText("Команда выполняется; итог ещё не подтверждён."),
    ).toBeInTheDocument();
    expect(screen.queryByText("8b8d0c93")).not.toBeInTheDocument();
    expect(
      screen.queryByText(/Traceback|secret-host|token=unsafe/),
    ).not.toBeInTheDocument();
  });

  it("collapses consecutive repeats in the compact action journal", () => {
    const snapshot = makeOperatorSnapshot();
    const base = snapshot.actions.data!.items[0]!;
    // Именно на телефоне владелец увидел двадцать одинаковых строк подряд.
    snapshot.actions.data!.items = [
      {
        ...base,
        id: "1844",
        public_id: "#1844",
        updated_at: "2026-07-18T10:15:00Z",
      },
      {
        ...base,
        id: "1843",
        public_id: "#1843",
        updated_at: "2026-07-18T10:14:00Z",
      },
      {
        ...base,
        id: "1842",
        public_id: "#1842",
        updated_at: "2026-07-18T10:13:00Z",
      },
    ];
    mockUseOperatorSnapshot.mockReturnValue({
      data: snapshot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<Dashboard />);

    expect(screen.getByText("×3")).toBeInTheDocument();
    // Строка ведёт к самому свежему повтору, а не к первому попавшемуся.
    expect(screen.getByText("Задача #1844")).toBeInTheDocument();
    expect(screen.queryByText("Задача #1843")).not.toBeInTheDocument();
    expect(screen.queryByText("Задача #1842")).not.toBeInTheDocument();
  });

  it("uses typed source links and hides source/action attention internals", () => {
    const snapshot = makeOperatorSnapshot();
    const incident = snapshot.attention.data!.items[0]!;
    snapshot.attention.data!.items = [
      incident,
      {
        ...incident,
        id: "source:unsafe",
        kind: "source",
        title: "Источник требует проверки",
        summary: "raw_source.connection_refused",
        reason: "cabinet_actor_error",
        action: { label: "Диагностика", href: "/system/sources" },
      },
      {
        ...incident,
        id: "task:unsafe",
        kind: "action",
        title: "Команда требует сверки",
        summary: "#42 · failed",
        reason: "raw worker exception",
        action: { label: "Открыть", href: "/actions/42" },
      },
    ];
    snapshot.attention.sources.push("private_backend_identifier");
    mockUseOperatorSnapshot.mockReturnValue({
      data: snapshot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<Dashboard />);

    expect(screen.getByText("CPL $9.56 при базе $3.00.")).toBeInTheDocument();
    expect(
      screen.getByText("Причина: Расход растёт без FTD"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("raw_source.connection_refused"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("cabinet_actor_error")).not.toBeInTheDocument();
    expect(screen.queryByText("#42 · failed")).not.toBeInTheDocument();
    expect(screen.queryByText("raw worker exception")).not.toBeInTheDocument();
    expect(
      screen.queryByText("private backend identifier"),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/Источник данных/)).toBeInTheDocument();
    expect(
      screen
        .getAllByRole("link")
        .some((link) => link.getAttribute("href") === "/system/sources"),
    ).toBe(true);
  });

  it("hides incident money copy until the snapshot confirms USD", () => {
    const snapshot = makeOperatorSnapshot();
    snapshot.meta = {
      ...snapshot.meta,
      currency: null,
      currency_state: "mixed",
    };
    snapshot.attention.data!.items[0]!.target.label = null;
    mockUseOperatorSnapshot.mockReturnValue({
      data: snapshot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<Dashboard />);

    expect(screen.getByText("Сигнал требует проверки")).toBeInTheDocument();
    expect(screen.getByText("Объект не указан")).toBeInTheDocument();
    expect(screen.queryByText("CPL выше базы")).not.toBeInTheDocument();
    expect(
      screen.queryByText("CPL $9.56 при базе $3.00."),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("Причина: Расход растёт без FTD"),
    ).not.toBeInTheDocument();
  });

  it("opens a cabinet through the typed TMA route", async () => {
    render(<Dashboard />);

    await userEvent.click(screen.getByRole("button", { name: /GH_CR2/ }));

    expect(mockNavigate).toHaveBeenCalledWith({
      to: "/cabinets/$cabinetId",
      params: { cabinetId: "123" },
    });
  });

  it("keeps the 202 scan receipt and links to the queued action lifecycle", async () => {
    mockUseOperatorSnapshot.mockReturnValue({
      data: makeReloginSnapshot(),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    mockScan.mockResolvedValue({
      state: "queued",
      task_id: 1842,
      public_id: "#1842",
      created: true,
      correlation_id: "8b8d0c93-15dc-46b4-8fe0-8da6bec3667f",
    });
    render(<Dashboard />);

    await userEvent.click(
      screen.getByRole("button", { name: "Повторить скан" }),
    );

    const queued = (
      await screen.findByText("Сканирование поставлено в очередь")
    ).closest('[role="status"]');
    if (!queued) throw new Error("Queued scan status is missing");
    expect(queued).toHaveTextContent("Сканирование поставлено в очередь");
    expect(queued).toHaveTextContent("Задача #1842");
    expect(
      screen.getByRole("link", { name: "Открыть выполнение" }),
    ).toHaveAttribute("href", "/actions/1842");
    expect(mockScan).toHaveBeenCalledWith({
      params: {
        header: {
          "Idempotency-Key": expect.stringMatching(/^[0-9a-f-]{36}$/i),
        },
      },
    });
    expect(mockHapticNotify).toHaveBeenCalledWith("warning");
    expect(mockHapticNotify).not.toHaveBeenCalledWith("success");
  });

  it("does not sell a system cancellation as a failure to fix", () => {
    // Тот же исход, что и в web-фронте: скан отменил observer, а не поломка.
    // Мини-апп делит хелпер, поэтому подпись и тон обязаны совпадать.
    mockUseOperatorSnapshot.mockReturnValue({
      data: makeReloginSnapshot("cancelled"),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<Dashboard />);

    const button = screen.getByRole("button", {
      name: "Скан отменён системой",
    });
    expect(button).toBeEnabled();
    expect(button).toHaveAttribute("data-state", "blocked");
    expect(
      screen.queryByRole("button", { name: "Ошибка — повторить" }),
    ).not.toBeInTheDocument();
  });

  it("shows an active retry as executing", () => {
    mockUseOperatorSnapshot.mockReturnValue({
      data: makeReloginSnapshot("running"),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<Dashboard />);

    expect(screen.getByRole("button", { name: "Выполняется" })).toBeDisabled();
  });

  it("shows a retryable error after command delivery fails", async () => {
    mockUseOperatorSnapshot.mockReturnValue({
      data: makeReloginSnapshot(),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    mockScan.mockRejectedValueOnce(new Error("Network down"));

    render(<Dashboard />);
    await userEvent.click(
      screen.getByRole("button", { name: "Повторить скан" }),
    );

    expect(
      screen.getByRole("button", { name: "Ошибка — повторить" }),
    ).toBeEnabled();
    expect(mockHapticNotify).toHaveBeenCalledWith("error");
  });

  it("labels stale data and does not present it as current", () => {
    const snapshot = makeOperatorSnapshot();
    snapshot.portfolio = {
      ...snapshot.portfolio,
      state: "stale",
      issues: [
        {
          code: "META_STALE",
          title: "Meta давно не обновлялась",
          detail: "Показан последний подтверждённый снимок.",
          severity: "warning",
          correlation_id: "corr-meta",
        },
      ],
    };
    mockUseOperatorSnapshot.mockReturnValue({
      data: snapshot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    render(<Dashboard />);
    expect(screen.getByText("Meta давно не обновлялась")).toBeInTheDocument();
    expect(screen.getAllByText(/снимок устарел/i).length).toBeGreaterThan(0);
  });

  it("never paints degraded money context as a healthy overview", () => {
    const snapshot = makeOperatorSnapshot();
    snapshot.system.data!.severity = "ok";
    snapshot.attention.data!.items = [];
    snapshot.funnel = { ...snapshot.funnel, state: "partial" };
    snapshot.meta = {
      ...snapshot.meta,
      currency: null,
      currency_state: "mixed",
    };
    mockUseOperatorSnapshot.mockReturnValue({
      data: snapshot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<Dashboard />);

    expect(
      screen.getByText("Денежный контекст требует проверки"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Валюта не подтверждена; суммы скрыты"),
    ).toBeInTheDocument();
    expect(screen.queryByText("$47.80")).not.toBeInTheDocument();
    expect(screen.queryByText("$0")).not.toBeInTheDocument();
    expect(screen.queryByText("$1")).not.toBeInTheDocument();
    expect(screen.queryByText("Активных рисков нет")).not.toBeInTheDocument();
  });

  it("hides a stale cabinet scale instead of presenting it as current", () => {
    const snapshot = makeOperatorSnapshot();
    snapshot.portfolio.data!.currency_groups[0]!.state = "stale";
    snapshot.portfolio.data!.currency_groups[0]!.cabinets[0]!.state = "stale";
    mockUseOperatorSnapshot.mockReturnValue({
      data: snapshot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<Dashboard />);

    const cabinet = screen.getByRole("button", {
      name: /GH_CR2/,
    }).parentElement;
    expect(cabinet).toHaveTextContent("снимок устарел");
  });

  it("downgrades the complete cached snapshot while realtime is reconnecting", () => {
    mockUseOperatorRealtimeStatus.mockReturnValue("reconnecting");

    render(<Dashboard />);

    expect(
      screen.getByText("Состояние ещё не подтверждено"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Активных рисков нет")).not.toBeInTheDocument();
    expect(screen.getAllByText(/снимок устарел/i).length).toBeGreaterThan(0);
  });

  it("renders a visible failure instead of legacy dashboard data", () => {
    mockUseOperatorSnapshot.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Snapshot failed"),
      refetch: vi.fn(),
    });
    render(<Dashboard />);
    expect(screen.getByRole("alert")).toHaveTextContent("Snapshot failed");
  });
});
