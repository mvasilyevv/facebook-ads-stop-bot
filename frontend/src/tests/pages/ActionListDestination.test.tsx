import type { ComponentType, ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { OperatorActionItem } from "@fb/shared/operator/contracts";

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: ComponentType }) => options,
  Link: ({
    children,
    to,
    params,
    search,
    ...props
  }: {
    children: ReactNode;
    to: string;
    params?: Record<string, string>;
    search?: Record<string, string>;
  }) => {
    const path = Object.entries(params ?? {}).reduce(
      (href, [key, value]) => href.replace(`$${key}`, value),
      to,
    );
    const query = new URLSearchParams(search ?? {}).toString();
    return (
      <a href={query ? `${path}?${query}` : path} {...props}>
        {children}
      </a>
    );
  },
}));

const { ActionList } = await import("@/features/operator/OperatorDashboard");

const RUN_ID = "5f1b25c9-1593-4cd5-b39e-068e877d32fa";

function action(overrides: Partial<OperatorActionItem>): OperatorActionItem {
  return {
    id: "20",
    public_id: "#20",
    kind: "create",
    state: "unknown",
    title: "Создание кампании",
    target_id: null,
    target_label: null,
    run_id: null,
    requested_at: "2026-08-20T10:18:08.303920Z",
    updated_at: "2026-08-20T10:23:03.023131Z",
    requested_by: "api_launch",
    reason: null,
    correlation_id: "5334dcb6-b00d-4d26-b455-eb47a7f33ac5",
    account_id: "3570379159805007",
    currency: "USD",
    cabinet_timezone: "America/Dawson_Creek",
    account_context_observed_at: null,
    account_context_issues: [],
    ...overrides,
  } as OperatorActionItem;
}

describe("куда ведёт действие из очереди", () => {
  it("залив открывается на экране кампании, а не на карточке конвейера", () => {
    // Владелец 20.08.2026: «хотел бы попасть на экран, где создаётся кампания».
    // Карточка действия показывает обработку — очередь, выполнение, сверку, —
    // а состав залива, созданные объекты и управление живут в запуске.
    render(<ActionList items={[action({ run_id: RUN_ID })]} />);

    expect(screen.getByRole("link", { name: /Открыть залив/ })).toHaveAttribute(
      "href",
      `/campaigns?run=${RUN_ID}`,
    );
    expect(screen.queryByRole("link", { name: /Открыть действие/ })).not.toBeInTheDocument();
  });

  it("действие без запуска по-прежнему ведёт на свою карточку", () => {
    render(<ActionList items={[action({ kind: "pause", title: "Отключение рекламы" })]} />);

    expect(screen.getByRole("link", { name: /Открыть действие/ })).toHaveAttribute(
      "href",
      "/actions/20",
    );
  });
});
