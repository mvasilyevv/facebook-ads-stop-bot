import type { ComponentType } from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const useOperatorIncident = vi.fn();
const navigate = vi.fn();
const acknowledgeMutate = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => ({
    ...options,
    useParams: () => ({ incidentId: "incident-51" }),
  }),
  useNavigate: () => navigate,
}));

vi.mock("@/lib/operatorApi", () => ({
  useOperatorIncident: (...args: unknown[]) => useOperatorIncident(...args),
  useAcknowledgeOperatorIncident: () => ({
    mutateAsync: acknowledgeMutate,
    isPending: false,
  }),
  operatorProblemMessage: () => "Инцидент недоступен",
}));

import { Route } from "@/routes/incidents/$incidentId";

const IncidentDetail = (Route as unknown as { component: ComponentType })
  .component;

describe("TMA typed incident detail", () => {
  beforeEach(() => {
    vi.clearAllMocks();
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
        status: "failed",
        incident: {
          id: "incident:incident-51",
          kind: "incident",
          severity: "critical",
          title: "Инцидент за пределами top-50",
          summary: "Детальная проекция загружена напрямую.",
          reason: "threshold",
          occurred_at: "2026-07-27T09:00:00Z",
          target: { kind: "system", id: null, label: "Система" },
          action: null,
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

  it("keeps a failed acknowledgement visible instead of navigating away", async () => {
    const current = useOperatorIncident();
    useOperatorIncident.mockReturnValue({
      ...current,
      data: { ...current.data, status: "open" },
    });
    acknowledgeMutate.mockRejectedValue(new Error("network"));

    render(<IncidentDetail />);
    fireEvent.click(
      screen.getByRole("button", { name: "Подтвердить получение" }),
    );

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Инцидент недоступен",
      );
    });
    expect(navigate).not.toHaveBeenCalled();
  });
});
