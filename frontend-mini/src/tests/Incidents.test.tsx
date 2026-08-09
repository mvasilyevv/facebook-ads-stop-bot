import type { ComponentType } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
    useSearch: () => ({ account_id: "123", status: "open", page: 1 }),
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

function payload() {
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
        title: "Spend $18.40 выше stop",
        summary: "CPL $9.56",
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

describe("TMA incident journal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useOperatorRealtimeStatus.mockReturnValue("connected");
    acknowledge.mockResolvedValue({});
    refetch.mockResolvedValue({});
    navigate.mockResolvedValue(undefined);
    useOperatorIncidents.mockReturnValue({
      data: payload(),
      isError: false,
      isPending: false,
      isFetching: false,
      refetch,
    });
  });

  it("uses the same URL/query evidence and keeps opaque IDs out of navigation URLs", async () => {
    render(<IncidentsPage />);

    expect(useOperatorIncidents).toHaveBeenCalledWith({
      account_id: "123",
      severity: [],
      status: ["open"],
      page: 1,
      page_size: 30,
    });
    expect(
      screen.getByRole("heading", { name: "Денежный сигнал требует проверки" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/\$18\.40/)).not.toBeInTheDocument();

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
    useOperatorIncidents.mockReturnValue({
      data: {
        ...payload(),
        state: "ready",
        issues: [],
        scope: makeOperatorScopeEvidence(),
      },
      isError: false,
      isPending: false,
      isFetching: false,
      refetch,
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
});
