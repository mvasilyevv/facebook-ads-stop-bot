import type { ComponentType, ReactNode } from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OperatorRealtimeStatusProvider, type OperatorRealtimeStatus } from "@fb/operator-api";
import type { OperatorSnapshot } from "@fb/shared/operator/contracts";
import { makeOperatorSnapshot } from "@fb/shared/operator/testFixture";

const mockUseOperatorSnapshot = vi.fn();
const mockUseOperatorCabinetSnapshot = vi.fn();
const mockScan = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => options,
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
    <a
      href={Object.entries(params ?? {}).reduce(
        (href, [key, value]) => href.replace(`$${key}`, value),
        to,
      )}
      {...props}
    >
      {children}
    </a>
  ),
}));

vi.mock("@/lib/api/operator", () => ({
  useOperatorSnapshot: (...args: unknown[]) => mockUseOperatorSnapshot(...args),
  useOperatorCabinetSnapshot: (...args: unknown[]) => mockUseOperatorCabinetSnapshot(...args),
  useOperatorScanNow: () => ({ mutate: mockScan, isPending: false }),
  operatorProblemMessage: (error: unknown) => (error instanceof Error ? error.message : "Ошибка"),
}));

const mockToast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
}));

vi.mock("@/components/ui/toastStore", () => ({
  toast: mockToast,
}));

import { Route } from "@/routes/index";
import { OperatorCabinetDashboard } from "@/features/operator/OperatorDashboard";

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
    campaign_name: "GH_CR | 18.06",
    adset_id: "adset-1",
    adset_name: "adset-android",
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

function renderDashboard(status: OperatorRealtimeStatus = "connected") {
  return render(
    <OperatorRealtimeStatusProvider status={status}>
      <Dashboard />
    </OperatorRealtimeStatusProvider>,
  );
}

describe("operator dashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
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

  it("shows the action-first overview and confirmed money", () => {
    renderDashboard();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Сейчас");
    expect(screen.getByText("Есть отклонения, требующие решения")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Портфель" })).toBeInTheDocument();
    expect(screen.getByText("CPL выше базы")).toBeInTheDocument();
    expect(screen.getAllByText(/\$18\.40/).length).toBeGreaterThan(0);
    expect(screen.getAllByText("$0").length).toBeGreaterThan(0);
    expect(screen.getByText("$20")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Сканировать" })).toBeInTheDocument();
  });

  it("uses typed cabinet navigation without exposing correlation UUIDs", () => {
    const snapshot = makeOperatorSnapshot();
    snapshot.actions.data!.items[0]!.correlation_id = "8b8d0c93-15dc-46b4-8fe0-8da6bec3667f";
    snapshot.actions.data!.items[0]!.reason = "Traceback: secret-host token=unsafe";
    mockUseOperatorSnapshot.mockReturnValue({
      data: snapshot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderDashboard();

    expect(screen.getByRole("link", { name: "Открыть кабинет: GH_CR2" })).toHaveAttribute(
      "href",
      "/cabinets/123",
    );
    expect(screen.getByRole("link", { name: "Открыть действие" })).toHaveAttribute(
      "href",
      "/actions/1842",
    );
    expect(screen.getByText("Задача #1842")).toBeInTheDocument();
    expect(screen.getByText("Команда выполняется; итог ещё не подтверждён.")).toBeInTheDocument();
    expect(screen.queryByText("8b8d0c93")).not.toBeInTheDocument();
    expect(screen.queryByText(/Traceback|secret-host|token=unsafe/)).not.toBeInTheDocument();
  });

  it("uses the selected cabinet timezone on the cabinet route", () => {
    render(
      <OperatorRealtimeStatusProvider status="connected">
        <OperatorCabinetDashboard cabinetId="123" />
      </OperatorRealtimeStatusProvider>,
    );

    expect(screen.getByText(/Africa\/Accra · контроль кабинета/)).toBeInTheDocument();
    expect(screen.getAllByText(/18\.07\.2026, 10:1[45]/).length).toBeGreaterThan(0);
    expect(mockUseOperatorCabinetSnapshot).toHaveBeenCalledWith("123", {
      window: "today",
    });
  });

  it("keeps cabinet timestamps unconfirmed when timezone evidence is missing", () => {
    const snapshot = makeOperatorSnapshot();
    snapshot.portfolio.data!.currency_groups[0]!.cabinets[0]!.timezone = null;
    snapshot.meta.cabinet_timezone = null;
    snapshot.meta.cabinet_timezone_known = false;
    snapshot.meta.cabinet_timezone_state = "unknown";
    mockUseOperatorCabinetSnapshot.mockReturnValue({
      data: snapshot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(
      <OperatorRealtimeStatusProvider status="connected">
        <OperatorCabinetDashboard cabinetId="123" />
      </OperatorRealtimeStatusProvider>,
    );

    expect(screen.getByRole("heading", { level: 1 }).parentElement).toHaveTextContent(
      "часовой пояс не подтверждён",
    );
    expect(screen.getAllByText("не подтверждено").length).toBeGreaterThan(0);
    expect(screen.queryByText(/18\.07\.2026, 12:1[45]/)).not.toBeInTheDocument();
  });

  it("reads a confirmed empty approaching-stop section as calm, not alarming", () => {
    renderDashboard();

    const section = screen.getByRole("region", { name: "Подходят к стопу" });
    expect(within(section).getByText("никто не подходит")).toBeInTheDocument();
    expect(
      within(section).getByText("Ни одно объявление не подходит к стопу."),
    ).toBeInTheDocument();
    expect(section).not.toHaveTextContent("Источник недоступен");
    expect(section).not.toHaveTextContent("Нет данных");
  });

  it("ranks approaching ads with their rule, threshold and share of the way", () => {
    const snapshot = makeOperatorSnapshot();
    snapshot.approaching_stop = {
      ...snapshot.approaching_stop,
      state: "ready",
      data: { items: [approachingRow("ad-9", "93.40"), approachingRow("ad-8", "71.05")] },
    };
    mockUseOperatorSnapshot.mockReturnValue({
      data: snapshot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderDashboard();

    const section = screen.getByRole("region", { name: "Подходят к стопу" });
    expect(within(section).getByText("93.4%")).toBeInTheDocument();
    expect(within(section).getAllByText("Дорогая рега · $0.41 из $0.48").length).toBe(2);
    expect(within(section).getAllByText("Подходит к стопу").length).toBe(2);
    expect(
      within(section).getByRole("link", { name: "Открыть объявление: Объявление ad-9" }),
    ).toHaveAttribute("href", "/ads/ad-9");
  });

  it("shows an unavailable approaching-stop section without inventing a share", () => {
    const snapshot = makeOperatorSnapshot();
    snapshot.approaching_stop = {
      ...snapshot.approaching_stop,
      state: "unavailable",
      data: null,
    };
    mockUseOperatorSnapshot.mockReturnValue({
      data: snapshot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderDashboard();

    const section = screen.getByRole("region", { name: "Подходят к стопу" });
    expect(within(section).getByText("Источник недоступен")).toBeInTheDocument();
    expect(section).not.toHaveTextContent("никто не подходит");
    expect(section).not.toHaveTextContent("0%");
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

    renderDashboard();

    expect(screen.getByText("CPL $9.56 при базе $3.00.")).toBeInTheDocument();
    expect(screen.getByText("Причина: Расход растёт без FTD")).toBeInTheDocument();
    expect(screen.queryByText("raw_source.connection_refused")).not.toBeInTheDocument();
    expect(screen.queryByText("cabinet_actor_error")).not.toBeInTheDocument();
    expect(screen.queryByText("#42 · failed")).not.toBeInTheDocument();
    expect(screen.queryByText("raw worker exception")).not.toBeInTheDocument();
    expect(screen.queryByText("private backend identifier")).not.toBeInTheDocument();
    expect(screen.getByText(/Источник данных/)).toBeInTheDocument();
    expect(
      screen.getAllByRole("link").some((link) => link.getAttribute("href") === "/system/sources"),
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

    renderDashboard();

    expect(screen.getByText("Сигнал требует проверки")).toBeInTheDocument();
    expect(screen.getByText("Объект не указан")).toBeInTheDocument();
    expect(screen.queryByText("CPL выше базы")).not.toBeInTheDocument();
    expect(screen.queryByText("CPL $9.56 при базе $3.00.")).not.toBeInTheDocument();
    expect(screen.queryByText("Причина: Расход растёт без FTD")).not.toBeInTheDocument();
  });

  it("never turns unavailable data into a green or zero state", () => {
    const snapshot = makeOperatorSnapshot();
    snapshot.system = {
      ...snapshot.system,
      state: "unavailable",
      data: null,
      issues: [
        {
          code: "HEARTBEATS_MISSING",
          title: "Воркеры не отвечают",
          detail: "Нет подтверждённых heartbeat.",
          severity: "critical",
          correlation_id: "corr-down",
        },
      ],
    };
    snapshot.economy = { ...snapshot.economy, state: "unavailable", data: null };
    mockUseOperatorSnapshot.mockReturnValue({
      data: snapshot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderDashboard();
    expect(screen.getAllByText("Источник недоступен").length).toBeGreaterThan(0);
    expect(screen.getByText("Состояние ещё не подтверждено")).toBeInTheDocument();
    expect(screen.queryByText("Активных рисков нет")).not.toBeInTheDocument();
    expect(screen.queryByText("$0.00")).not.toBeInTheDocument();
  });

  it("surfaces partial sections while retaining explicitly marked data", () => {
    const snapshot = makeOperatorSnapshot();
    snapshot.funnel = {
      ...snapshot.funnel,
      state: "partial",
      issues: [
        {
          code: "TRACKER_DELAYED",
          title: "Tracker задерживается",
          detail: "Показана только подтверждённая часть воронки.",
          severity: "warning",
          correlation_id: "corr-tracker",
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

    renderDashboard();
    expect(screen.getByText("Tracker задерживается")).toBeInTheDocument();
    expect(screen.getByText("Клики")).toBeInTheDocument();
  });

  it("never paints degraded money context as a healthy overview", () => {
    const snapshot = makeOperatorSnapshot();
    snapshot.system.data!.severity = "ok";
    snapshot.attention.data!.items = [];
    snapshot.economy = { ...snapshot.economy, state: "partial" };
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

    renderDashboard();

    expect(screen.getByText("Денежный контекст требует проверки")).toBeInTheDocument();
    expect(screen.getAllByText("Данные неполные").length).toBeGreaterThan(0);
    expect(screen.getByText("Валюта не подтверждена")).toBeInTheDocument();
    expect(screen.queryByText("$47.80")).not.toBeInTheDocument();
    expect(screen.queryByText("$0")).not.toBeInTheDocument();
    expect(screen.queryByText("$1")).not.toBeInTheDocument();
    expect(screen.queryByText("Активных рисков нет")).not.toBeInTheDocument();
  });

  it("does not render a server-provided action outside the internal allowlist", () => {
    const snapshot = makeOperatorSnapshot();
    snapshot.attention.data!.items[0]!.action = {
      label: "Открыть",
      href: "https://example.invalid/operator",
    };
    mockUseOperatorSnapshot.mockReturnValue({
      data: snapshot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderDashboard();

    expect(screen.queryByRole("link", { name: "Открыть" })).not.toBeInTheDocument();
  });

  it("renders an actionable unavailable state for request failures", () => {
    mockUseOperatorSnapshot.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Network down"),
      refetch: vi.fn(),
    });
    renderDashboard();
    expect(screen.getByRole("alert")).toHaveTextContent("Операторский снимок недоступен");
    expect(screen.getByRole("button", { name: "Повторить" })).toBeInTheDocument();
  });

  it("never presents a cached snapshot as current while realtime reconnects", () => {
    renderDashboard("reconnecting");

    expect(screen.getByText("Состояние ещё не подтверждено")).toBeInTheDocument();
    expect(screen.getAllByText("Данные устарели").length).toBeGreaterThan(0);
    expect(screen.queryByText("Данные актуальны")).not.toBeInTheDocument();
    expect(screen.queryByText("Активных рисков нет")).not.toBeInTheDocument();
    expect(screen.queryByText("В работе")).not.toBeInTheDocument();
    expect(screen.getByText("Выполняется")).toBeInTheDocument();
    expect(screen.queryByText("Подтверждено")).not.toBeInTheDocument();
  });

  it("counts every attention reason, not only the five rendered cards", () => {
    const snapshot = makeOperatorSnapshot();
    const incident = snapshot.attention.data!.items[0]!;
    snapshot.attention.data!.items = Array.from({ length: 7 }, (_, index) => ({
      ...incident,
      id: `incident-${index}`,
    }));
    mockUseOperatorSnapshot.mockReturnValue({
      data: snapshot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderDashboard();

    expect(screen.getByText("7 причин")).toBeInTheDocument();
    expect(screen.queryByText("5 причин")).not.toBeInTheDocument();
  });

  it("prints — instead of 0 when attention and action evidence is missing", () => {
    const snapshot = makeOperatorSnapshot();
    snapshot.attention.state = "unavailable";
    snapshot.attention.data = null;
    snapshot.actions.state = "stale";
    snapshot.actions.data = null;
    mockUseOperatorSnapshot.mockReturnValue({
      data: snapshot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderDashboard();

    // Нет подтверждённого списка — нет и числа. Ноль означал бы «сигналов нет».
    expect(screen.queryByText("0 причин")).not.toBeInTheDocument();
    expect(screen.queryByText("0 выполняется")).not.toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(2);
  });

  it("reports a queued scan as queued, never as a green success", async () => {
    mockScan.mockImplementation(
      (_vars: unknown, options?: { onSuccess?: () => void }) => options?.onSuccess?.(),
    );

    renderDashboard();
    await userEvent.click(screen.getByRole("button", { name: "Сканировать" }));

    // HTTP 202 = queued. Зелёный toast.success означал бы выполненное сканирование.
    expect(mockToast.success).not.toHaveBeenCalled();
    expect(mockToast.info).toHaveBeenCalledWith(
      "Сканирование поставлено в очередь",
      expect.stringContaining("не подтверждено"),
    );
  });
});
