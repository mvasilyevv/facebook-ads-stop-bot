import type { ComponentType } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { OperatorIncidentsResponse } from "@fb/shared/operator/contracts";
import {
  makeOperatorScopeEvidence,
  makeOperatorSnapshot,
} from "@fb/shared/operator/testFixture";

const navigate = vi.fn();
const acknowledge = vi.fn();
const refetch = vi.fn();
const useOperatorIncidents = vi.fn();
const storeResolvedNavigation = vi.fn();
const useOperatorRealtimeStatus = vi.fn(() => "connected");

vi.mock("@fb/operator-api", () => ({
  useOperatorRealtimeStatus: () => useOperatorRealtimeStatus(),
}));

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => ({
    ...options,
    useSearch: () => ({ account_id: "123", status: "open" }),
  }),
  useNavigate: () => navigate,
}));

vi.mock("@/lib/operatorApi", () => ({
  useOperatorSnapshot: () => ({ data: makeOperatorSnapshot() }),
  useOperatorIncidents: (...args: unknown[]) => useOperatorIncidents(...args),
  useAcknowledgeOperatorIncident: () => ({ mutateAsync: acknowledge }),
  operatorIncidentProblemMessage: () => "Журнал временно недоступен",
}));

vi.mock("@/lib/transientNavigation", () => ({
  storeResolvedNavigation: (...args: unknown[]) =>
    storeResolvedNavigation(...args),
}));

vi.mock("@/lib/tg", () => ({
  haptic: {
    selection: vi.fn(),
    impact: vi.fn(),
    notify: vi.fn(),
  },
}));

import { Route } from "@/routes/incidents/index";

const IncidentsPage = (Route as unknown as { component: ComponentType })
  .component;

function payload(): OperatorIncidentsResponse {
  return {
    state: "partial",
    as_of: "2026-08-08T12:00:00Z",
    freshness_seconds: 0,
    sources: ["incidents", "meta_account_snapshot"],
    issues: [
      {
        code: "currency_unknown",
        title: "Валюта кабинета не подтверждена",
        detail: "Денежные значения скрыты.",
        severity: "unknown",
        correlation_id: null,
      },
    ],
    scope: {
      ...makeOperatorScopeEvidence(),
      currency: null,
      currency_state: "unknown",
      currency_observed_at: null,
      missing_currency_account_ids: ["123"],
    },
    items: [
      {
        id: "00000000-0000-0000-0000-000000000051",
        severity: "critical",
        status: "open",
        // Заголовок инцидента — название правила (core/rules/labels.py::rule_label),
        // сумм в нём не бывает: деньги живут в summary и reason.
        title: "Расход без депозита",
        summary: "Валюта кабинета не подтверждена. Обновите снимок — денежные детали скрыты.",
        reason: "расход без первого депозита",
        occurred_at: "2026-08-08T12:00:00Z",
        account_id: "123",
        target: { kind: "ad", id: "120001", label: "GH_CR2" },
        action: {
          label: "Открыть",
          href: "/incidents/00000000-0000-0000-0000-000000000051",
        },
        requires_usd_evidence: true,
      },
    ],
    page: 1,
    page_size: 30,
    total: 1,
    pages: 1,
  };
}

function setIncidents(
  pages: ReturnType<typeof payload>[] | ReturnType<typeof payload>,
  options: {
    isError?: boolean;
    error?: Error;
    hasNextPage?: boolean;
    isFetchingNextPage?: boolean;
    fetchNextPage?: () => void;
  } = {},
) {
  const pageArray = Array.isArray(pages) ? pages : [pages];
  useOperatorIncidents.mockReturnValue({
    data: { pages: pageArray },
    isError: options.error !== undefined || (options.isError ?? false),
    error: options.error ?? null,
    isPending: false,
    isFetching: false,
    hasNextPage: options.hasNextPage ?? false,
    isFetchingNextPage: options.isFetchingNextPage ?? false,
    fetchNextPage: options.fetchNextPage ?? vi.fn(),
    refetch,
  });
}

describe("TMA incident journal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useOperatorRealtimeStatus.mockReturnValue("connected");
    acknowledge.mockResolvedValue({});
    refetch.mockResolvedValue({});
    navigate.mockResolvedValue(undefined);
    setIncidents(payload());
  });

  it("uses the same URL/query evidence and keeps opaque IDs out of navigation URLs", async () => {
    render(<IncidentsPage />);

    expect(useOperatorIncidents).toHaveBeenCalledWith({
      account_id: "123",
      severity: [],
      status: ["open"],
      page_size: 30,
    });
    // Issue 354: прячется сумма, а не природа сигнала — заголовок правила
    // остаётся видимым, иначе оператор не может рассортировать инциденты.
    expect(screen.getByRole("heading", { name: "Расход без депозита" })).toBeInTheDocument();
    expect(screen.getAllByText(/Валюта кабинета не подтверждена/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/\$/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Открыть/ }));
    await waitFor(() =>
      expect(storeResolvedNavigation).toHaveBeenCalledWith({
        target_kind: "incident",
        target_id: "00000000-0000-0000-0000-000000000051",
      }),
    );
    expect(navigate).toHaveBeenCalledWith({ to: "/open" });
    expect(screen.getByText("1 запись")).toBeInTheDocument();
  });

  it("does not show ready or confirmed empty while realtime reconnects", () => {
    useOperatorRealtimeStatus.mockReturnValue("reconnecting");
    setIncidents({
      ...payload(),
      state: "ready",
      issues: [],
      scope: makeOperatorScopeEvidence(),
    });

    render(<IncidentsPage />);

    expect(screen.getAllByText("Данные устарели")).not.toHaveLength(0);
    expect(screen.queryByText("Данные актуальны")).not.toBeInTheDocument();
  });

  it("acknowledges an open incident in one tap", async () => {
    render(<IncidentsPage />);

    fireEvent.click(screen.getByRole("button", { name: /Принять/ }));

    await waitFor(() => expect(acknowledge).toHaveBeenCalledTimes(1));
    expect(acknowledge).toHaveBeenCalledWith({
      params: {
        path: { incident_id: "00000000-0000-0000-0000-000000000051" },
        header: { "X-Operator-Principal": "operator:tma" },
      },
    });
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("does not show 'Загрузка…' together with the error banner (QW8)", () => {
    useOperatorIncidents.mockReturnValue({
      data: undefined,
      isError: true,
      isPending: false,
      isFetching: false,
      hasNextPage: false,
      isFetchingNextPage: false,
      fetchNextPage: vi.fn(),
      error: new Error("boom"),
      refetch,
    });

    render(<IncidentsPage />);

    expect(screen.queryByText("Загрузка…")).not.toBeInTheDocument();
    expect(screen.getByText("Не удалось загрузить")).toBeInTheDocument();
    expect(screen.getByText("Журнал временно недоступен")).toBeInTheDocument();
  });

  it("offers one-step recovery from an empty filtered journal", () => {
    setIncidents({
      ...payload(),
      state: "empty",
      issues: [],
      scope: makeOperatorScopeEvidence(),
      items: [],
      total: 0,
      pages: 0,
    });

    render(<IncidentsPage />);
    fireEvent.click(screen.getByRole("button", { name: "Сбросить фильтры" }));

    expect(navigate).toHaveBeenCalledWith({ search: {}, replace: true });
  });

  it("accumulates a second page below the first, in server order", () => {
    const secondItem = {
      ...payload().items[0]!,
      id: "00000000-0000-0000-0000-000000000052",
      target: { kind: "ad" as const, id: "120002", label: "PL_VIP" },
    };
    setIncidents([payload(), { ...payload(), page: 2, items: [secondItem] }]);

    render(<IncidentsPage />);

    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("GH_CR2");
    expect(rows[1]).toHaveTextContent("PL_VIP");
  });

  it("offers a show-more control that fetches the next page", async () => {
    const fetchNextPage = vi.fn();
    setIncidents(payload(), { hasNextPage: true, fetchNextPage });

    render(<IncidentsPage />);
    fireEvent.click(screen.getByRole("button", { name: "Показать ещё" }));

    await waitFor(() => expect(fetchNextPage).toHaveBeenCalledOnce());
  });
});
