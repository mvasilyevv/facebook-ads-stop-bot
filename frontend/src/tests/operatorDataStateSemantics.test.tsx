import { render, screen } from "@testing-library/react";

import {
  DataStateBadge,
  DataStateNotice,
  OperatorSectionFrame,
} from "@fb/operator-ui";

describe("operator data-state color semantics", () => {
  it("reserves confirmed and degraded tones for ready and partial", () => {
    render(
      <div>
        <DataStateBadge state="ready" />
        <DataStateBadge state="partial" />
      </div>,
    );

    expect(screen.getByText("Данные актуальны")).toHaveAttribute("data-tone", "confirmed");
    expect(screen.getByText("Данные неполные")).toHaveAttribute("data-tone", "degraded");
  });

  it("keeps stale and unavailable neutral without inventing active danger", () => {
    render(
      <div>
        <DataStateBadge state="stale" />
        <DataStateBadge state="unavailable" />
        <DataStateNotice state="stale" />
        <DataStateNotice state="unavailable" />
      </div>,
    );

    expect(screen.getAllByText("Данные устарели")[0]).toHaveAttribute("data-tone", "neutral");
    expect(screen.getAllByText("Источник недоступен")[0]).toHaveAttribute("data-tone", "neutral");
    for (const notice of document.querySelectorAll(".operator-state-notice")) {
      expect(notice).toHaveAttribute("data-tone", "neutral");
    }
  });

  it("shows every primary degraded cause instead of hiding all but the first", () => {
    render(
      <DataStateNotice
        state="partial"
        issues={[
          {
            code: "meta_partial",
            title: "Meta отвечает частично",
            detail: "Часть объявлений недоступна.",
            severity: "warning",
            correlation_id: "corr-1",
          },
          {
            code: "tracker_stale",
            title: "Tracker отстаёт",
            detail: "Последнее событие старше допустимого окна.",
            severity: "warning",
            correlation_id: "corr-2",
          },
        ]}
      />,
    );

    expect(screen.getByText("Meta отвечает частично")).toBeInTheDocument();
    expect(screen.getByText("Tracker отстаёт")).toBeInTheDocument();
    expect(screen.getByText("Последнее событие старше допустимого окна.")).toBeInTheDocument();
  });

  it("renders the explicit empty state instead of empty children", () => {
    render(
      <OperatorSectionFrame
        title="Задачи"
        section={{
          state: "empty",
          as_of: "2026-07-27T10:00:00Z",
          freshness_seconds: 0,
          sources: [],
          issues: [],
          data: [],
        }}
        empty={<p>Задач нет</p>}
      >
        {() => <p>Нельзя показывать пустые данные как содержимое</p>}
      </OperatorSectionFrame>,
    );

    expect(screen.getByText("Задач нет")).toBeInTheDocument();
    expect(
      screen.queryByText("Нельзя показывать пустые данные как содержимое"),
    ).not.toBeInTheDocument();
  });
});
