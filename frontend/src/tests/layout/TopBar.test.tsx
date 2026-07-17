import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TopBar } from "@/components/layout/TopBar";

const routerState = vi.hoisted(() => ({ pathname: "/analytics" }));

vi.mock("@tanstack/react-router", () => ({
  useRouterState: () => ({ location: { pathname: routerState.pathname } }),
}));

vi.mock("@/components/layout/WorkerPulse", () => ({
  WorkerPulse: () => null,
}));

vi.mock("@/stores/commandPalette", () => ({
  useCommandPalette: (selector: (state: { toggle: () => void }) => unknown) =>
    selector({ toggle: vi.fn() }),
}));

describe("TopBar", () => {
  beforeEach(() => {
    routerState.pathname = "/analytics";
  });

  it("shows the analytics breadcrumb on the unified analytics route", () => {
    render(<TopBar />);

    expect(screen.getByLabelText("Текущий раздел")).toHaveTextContent("Аналитика");
  });

  it("keeps the analytics breadcrumb for route search parameters", () => {
    routerState.pathname = "/analytics/";

    render(<TopBar />);

    expect(screen.getByLabelText("Текущий раздел")).toHaveTextContent("Аналитика");
  });
});
