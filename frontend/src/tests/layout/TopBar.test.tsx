import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TopBar } from "@/components/layout/TopBar";

const routerState = vi.hoisted(() => ({ pathname: "/analytics" }));
const cabinetSnapshotMock = vi.hoisted(() => ({ data: undefined as unknown }));

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

vi.mock("@/lib/api/operator", () => ({
  useOperatorCabinetSnapshot: () => cabinetSnapshotMock,
}));

describe("TopBar", () => {
  beforeEach(() => {
    routerState.pathname = "/analytics";
    cabinetSnapshotMock.data = undefined;
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

  it.each([
    ["/system/sources", "Источники и воркеры"],
    ["/incidents/incident-42", "Инцидент"],
  ])("shows a concrete breadcrumb for %s", (pathname, label) => {
    routerState.pathname = pathname;

    render(<TopBar />);

    expect(screen.getByLabelText("Текущий раздел")).toHaveTextContent(label);
    expect(screen.getByLabelText("Текущий раздел")).not.toHaveTextContent("—");
  });

  // #345 QW12 — хлебная крошка на /cabinets/:id раньше всегда показывала
  // статичное "Кабинет" из ROUTE_CRUMB, даже когда имя кабинета уже лежало в
  // загруженном снапшоте (тот же запрос держит в кэше страница кабинета).
  it("подставляет имя кабинета из уже загруженного снапшота", () => {
    routerState.pathname = "/cabinets/act_123456789";
    cabinetSnapshotMock.data = { meta: { account: { id: "act_123456789", name: "PL_VIP" } } };

    render(<TopBar />);

    expect(screen.getByLabelText("Текущий раздел")).toHaveTextContent("PL_VIP");
  });

  it("оставляет обобщённую крошку, пока снапшот кабинета не загружен", () => {
    routerState.pathname = "/cabinets/act_123456789";
    cabinetSnapshotMock.data = undefined;

    render(<TopBar />);

    expect(screen.getByLabelText("Текущий раздел")).toHaveTextContent("Кабинет");
  });
});
