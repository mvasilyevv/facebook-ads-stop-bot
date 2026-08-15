import type { ComponentType } from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { makeOperatorScopeEvidence } from "@fb/shared/operator/testFixture";

const useOperatorIncident = vi.fn();
const navigate = vi.fn();
const acknowledgeMutate = vi.fn();

vi.mock("@fb/operator-api", () => ({
  useOperatorRealtimeStatus: () => "connected",
}));

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => ({
    ...options,
    useParams: () => ({ incidentId: "incident-51" }),
  }),
  useNavigate: () => navigate,
}));

vi.mock("@/lib/tg", () => ({
  haptic: { selection: vi.fn(), impact: vi.fn(), notify: vi.fn() },
  tgAlert: vi.fn(),
}));

vi.mock("@/lib/operatorApi", () => ({
  useOperatorIncident: (...args: unknown[]) => useOperatorIncident(...args),
  useAcknowledgeOperatorIncident: () => ({
    mutateAsync: acknowledgeMutate,
    isPending: false,
  }),
  operatorIncidentProblemMessage: () => "Не удалось подтвердить получение",
  operatorProblemMessage: () => "Инцидент недоступен",
}));

import { Route } from "@/routes/incidents/$incidentId";
import {
  clearResolvedNavigation,
  readResolvedNavigation,
} from "@/lib/transientNavigation";

const IncidentDetail = (Route as unknown as { component: ComponentType })
  .component;

describe("TMA typed incident detail", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    clearResolvedNavigation();
    acknowledgeMutate.mockResolvedValue({});
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
          status: "failed",
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

  it("loads the exact incident instead of searching the ranked snapshot", () => {
    render(<IncidentDetail />);

    expect(useOperatorIncident).toHaveBeenCalledWith("incident-51");
    expect(
      screen.getByRole("heading", {
        name: "Инцидент за пределами top-50",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("Завершён с ошибкой")).toBeInTheDocument();
  });

  it("opens the server-provided target without exposing its id in the TMA URL", () => {
    const current = useOperatorIncident();
    useOperatorIncident.mockReturnValue({
      ...current,
      data: {
        ...current.data,
        incident: {
          ...current.data.incident,
          target: { kind: "ad", id: "ad-51", label: "Объявление CR2" },
          action: { label: "Открыть объявление", href: "/ads/ad-51" },
        },
      },
    });

    render(<IncidentDetail />);
    fireEvent.click(screen.getByRole("button", { name: "Открыть объявление" }));

    expect(readResolvedNavigation()).toEqual({
      target_kind: "ad",
      target_id: "ad-51",
    });
    expect(navigate).toHaveBeenCalledWith({ to: "/open" });
    expect(window.location.href).not.toContain("ad-51");
  });

  it("shows partial evidence and its concrete issue", () => {
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
    expect(
      screen.getByText(/часовой пояс кабинета не подтверждён/i),
    ).toBeInTheDocument();
  });

  it("suppresses monetary detail copy without confirmed USD", () => {
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
          title: "Spend $18.40 выше stop",
          summary: "CPL $9.56",
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

  it("keeps a failed acknowledgement visible instead of navigating away", async () => {
    const current = useOperatorIncident();
    useOperatorIncident.mockReturnValue({
      ...current,
      data: {
        ...current.data,
        incident: { ...current.data.incident, status: "open" },
      },
    });
    acknowledgeMutate.mockRejectedValue(new Error("network"));

    render(<IncidentDetail />);
    fireEvent.click(
      screen.getByRole("button", { name: "Подтвердить получение" }),
    );

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Не удалось подтвердить получение",
      );
    });
    expect(navigate).not.toHaveBeenCalled();
  });
});
