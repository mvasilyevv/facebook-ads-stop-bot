import type { ComponentType, ReactNode } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { OperatorIncidentsResponse } from "@fb/shared/operator/contracts";
import { makeOperatorScopeEvidence, makeOperatorSnapshot } from "@fb/shared/operator/testFixture";

const navigate = vi.fn();
const acknowledge = vi.fn();
const refetch = vi.fn();
const useOperatorIncidents = vi.fn();
const useOperatorRealtimeStatus = vi.fn(() => "connected");

vi.mock("@fb/operator-api", () => ({
  useOperatorRealtimeStatus: () => useOperatorRealtimeStatus(),
}));

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => ({
    ...options,
    useSearch: () => ({ severity: "critical", status: "open" }),
  }),
  useNavigate: () => navigate,
  Link: ({ children, params }: { children: ReactNode; params?: { incidentId?: string } }) => (
    <a href={params?.incidentId ? `/incidents/${params.incidentId}` : "/incidents"}>{children}</a>
  ),
}));

vi.mock("@/lib/api/operator", () => ({
  useOperatorSnapshot: () => ({ data: makeOperatorSnapshot() }),
  useOperatorIncidents: (...args: unknown[]) => useOperatorIncidents(...args),
  useAcknowledgeOperatorIncident: () => ({ mutateAsync: acknowledge }),
  operatorIncidentProblemMessage: () => "Журнал временно недоступен",
}));

import { Route } from "@/routes/incidents/index";

const IncidentsPage = (Route as unknown as { component: ComponentType }).component;

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
        title: "CPL $9.56 > $3.00",
        summary: "Spend $18.40 · 0 FTD",
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
    page: 2,
    page_size: 30,
    total: 31,
    pages: 2,
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

describe("operator incident journal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useOperatorRealtimeStatus.mockReturnValue("connected");
    acknowledge.mockResolvedValue({});
    refetch.mockResolvedValue({});
    setIncidents(payload());
  });

  it("keeps URL filters in the typed API query and hides unproven money", () => {
    render(<IncidentsPage />);

    expect(useOperatorIncidents).toHaveBeenCalledWith({
      account_id: undefined,
      severity: ["critical"],
      status: ["open"],
      page_size: 30,
    });
    expect(
      screen.getByRole("heading", { name: "Денежный сигнал требует проверки" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/\$18\.40/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Открыть/ })).toHaveAttribute(
      "href",
      "/incidents/00000000-0000-0000-0000-000000000051",
    );
    // Честный счётчик «показано X из total»: на странице одна запись, а
    // total (31) сервер подтвердил заранее.
    expect(screen.getByText("1 из 31 запись")).toBeInTheDocument();
  });

  it("downgrades a ready HTTP journal while live reconciliation is pending", () => {
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

  it("keeps the journal heading visible when incidents are unavailable", () => {
    useOperatorIncidents.mockReturnValue({
      data: undefined,
      isError: true,
      isPending: false,
      isFetching: false,
      hasNextPage: false,
      isFetchingNextPage: false,
      fetchNextPage: vi.fn(),
      error: new Error("Журнал инцидентов недоступен"),
      refetch,
    });

    render(<IncidentsPage />);

    expect(screen.getByRole("heading", { name: "Инциденты" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("acknowledges an open incident in one tap and refreshes the journal", async () => {
    render(<IncidentsPage />);

    fireEvent.click(screen.getByRole("button", { name: "Принять" }));

    await waitFor(() => expect(acknowledge).toHaveBeenCalledTimes(1));
    expect(acknowledge).toHaveBeenCalledWith({
      params: {
        path: { incident_id: "00000000-0000-0000-0000-000000000051" },
        header: { "X-Operator-Principal": "operator:web" },
      },
    });
    expect(refetch).toHaveBeenCalledTimes(1);
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
    setIncidents([payload(), { ...payload(), page: 3, items: [secondItem] }]);

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

  it("shows only the current query's page after a filter change discards the old accumulation", () => {
    const secondItem = {
      ...payload().items[0]!,
      id: "00000000-0000-0000-0000-000000000052",
      target: { kind: "ad" as const, id: "120002", label: "PL_VIP" },
    };
    setIncidents([payload(), { ...payload(), page: 3, items: [secondItem] }]);
    const { rerender } = render(<IncidentsPage />);
    expect(screen.getAllByRole("listitem")).toHaveLength(2);

    // Смена фильтра — новый ключ запроса: react-query отдаёт свежую первую
    // страницу этой выборки, а не хвост от предыдущей. Компонент не должен
    // держать собственный накопительный стейт поверх этого.
    setIncidents({ ...payload(), items: [{ ...payload().items[0]!, id: "solo" }] });
    rerender(<IncidentsPage />);

    const remainingRows = screen.getAllByRole("listitem");
    expect(remainingRows).toHaveLength(1);
    expect(remainingRows[0]).not.toHaveTextContent("PL_VIP");
  });
});
