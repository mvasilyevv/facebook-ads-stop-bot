import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { makeOperatorSnapshot } from "@fb/shared/operator/testFixture";

const mockUseOperatorSnapshot = vi.fn();
const mockRealtimeStatus = vi.fn(() => "connected");

vi.mock("@/lib/api/operator", () => ({
  useOperatorSnapshot: (...args: unknown[]) => mockUseOperatorSnapshot(...args),
}));

vi.mock("@fb/operator-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@fb/operator-api")>()),
  useOperatorRealtimeStatus: () => mockRealtimeStatus(),
}));

import { WorkerPulse } from "@/components/layout/WorkerPulse";

describe("WorkerPulse", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockRealtimeStatus.mockReturnValue("connected");
    mockUseOperatorSnapshot.mockReturnValue({
      data: makeOperatorSnapshot(),
      isError: false,
    });
  });

  it("shows the confirmed worker count", () => {
    render(<WorkerPulse />);
    expect(screen.getByText("1/2 воркеров")).toBeInTheDocument();
  });

  it("neutralizes cached worker health while realtime reconnects", async () => {
    mockRealtimeStatus.mockReturnValue("reconnecting");
    render(<WorkerPulse />);

    expect(screen.getByText("—/2 воркеров")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Воркеры: статус не подтверждён" }),
    ).toBeInTheDocument();

    screen.getByRole("button", { name: "Воркеры: статус не подтверждён" }).focus();
    await userEvent.keyboard("{Enter}");
    expect(screen.getAllByText("не подтверждено").length).toBeGreaterThan(0);
    expect(screen.queryByText("online")).not.toBeInTheDocument();
  });
});
