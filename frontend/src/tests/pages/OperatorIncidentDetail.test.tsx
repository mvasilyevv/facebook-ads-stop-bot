import type { ComponentType, ReactNode } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { makeOperatorScopeEvidence } from "@fb/shared/operator/testFixture";

const useOperatorIncident = vi.fn();
const acknowledge = vi.fn();
const navigate = vi.fn();

vi.mock("@fb/operator-api", () => ({
  useOperatorRealtimeStatus: () => "connected",
}));

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => ({
    ...options,
    useParams: () => ({ incidentId: "incident-51" }),
  }),
  useNavigate: () => navigate,
  Link: ({ children }: { children: ReactNode }) => <a href="/">{children}</a>,
}));

vi.mock("@/lib/api/operator", () => ({
  useOperatorIncident: (...args: unknown[]) => useOperatorIncident(...args),
  useAcknowledgeOperatorIncident: () => ({
    mutateAsync: acknowledge,
    isPending: false,
  }),
  operatorIncidentProblemMessage: () => "Не удалось подтвердить получение",
  operatorProblemMessage: () => "Инцидент недоступен",
}));

import { Route } from "@/routes/incidents/$incidentId";

const IncidentDetail = (Route as unknown as { component: ComponentType }).component;

describe("typed operator incident detail", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    acknowledge.mockResolvedValue({});
    useOperatorIncident.mockReturnValue({
      data: {
        state: "ready",
        as_of: "2026-07-27T10:00:00Z",
        freshness_seconds: 0,
        sources: ["incidents"],
        issues: [],
        timezone: "UTC",
        timezone_known: true,
        scope: makeOperatorScopeEvidence(),
        incident: {
          id: "incident:incident-51",
          severity: "critical",
          status: "resolved",
          title: "Инцидент за пределами top-50",
          summary: "Детальная проекция загружена напрямую.",
          reason: "threshold",
          occurred_at: "2026-07-27T09:00:00Z",
          target: { kind: "system", id: null, label: "Система" },
          account_id: null,
          action: { label: "Открыть", href: "/incidents/incident-51" },
          requires_usd_evidence: false,
        },
      },
      isPending: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
  });

  it("loads the exact incident endpoint and keeps terminal incidents addressable", () => {
    render(<IncidentDetail />);

    expect(useOperatorIncident).toHaveBeenCalledWith("incident-51");
    expect(
      screen.getByRole("heading", {
        name: "Инцидент за пределами top-50",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("Разрешён")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Подтвердить получение" })).not.toBeInTheDocument();
  });

  it("shows partial evidence, freshness context and an unknown timezone", () => {
    const current = useOperatorIncident();
    useOperatorIncident.mockReturnValue({
      ...current,
      data: {
        ...current.data,
        state: "partial",
        timezone_known: false,
        issues: [
          {
            code: "source_lag",
            title: "Источник запаздывает",
            detail: "Meta snapshot старше допустимого окна",
            severity: "warning",
            correlation_id: null,
          },
        ],
      },
    });

    render(<IncidentDetail />);

    expect(screen.getByText("Источник запаздывает")).toBeInTheDocument();
    expect(screen.getByText("Данные на")).toBeInTheDocument();
    expect(screen.getByText("инциденты")).toBeInTheDocument();
    expect(screen.getByText(/часовой пояс кабинета не подтверждён/i)).toBeInTheDocument();
  });

  it("suppresses monetary copy when detail USD evidence is missing", () => {
    const current = useOperatorIncident();
    useOperatorIncident.mockReturnValue({
      ...current,
      data: {
        ...current.data,
        state: "partial",
        scope: {
          ...current.data.scope,
          currency: null,
          currency_state: "unknown",
          currency_observed_at: null,
          missing_currency_account_ids: ["123"],
        },
        incident: {
          ...current.data.incident,
          title: "CPL $9.56 > $3.00",
          summary: "Spend $18.40",
          requires_usd_evidence: true,
        },
      },
    });

    render(<IncidentDetail />);

    expect(
      screen.getByRole("heading", { name: "Денежный сигнал требует проверки" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/\$18\.40/)).not.toBeInTheDocument();
  });

  it("keeps a failed acknowledgement visible and stays on the incident", async () => {
    const current = useOperatorIncident();
    useOperatorIncident.mockReturnValue({
      ...current,
      data: {
        ...current.data,
        incident: { ...current.data.incident, status: "open" },
      },
    });
    acknowledge.mockRejectedValue(new Error("network"));

    render(<IncidentDetail />);
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить получение" }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Не удалось подтвердить получение");
    });
    expect(navigate).not.toHaveBeenCalled();
  });
});
