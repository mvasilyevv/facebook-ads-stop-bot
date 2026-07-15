import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MetaDelayedNote, TrackerLiveStrip } from "@/components/data/SourceStatus";

describe("source status", () => {
  it("показывает event-driven метрики и качество AdSet.pro", () => {
    render(
      <TrackerLiveStrip
        data={{
          tracker: {
            available: true,
            totals: {
              registrations: 18,
              ftds: 6,
              confirmed_deposits: 5,
              redeposits: 2,
            },
            unmatched_events: 1,
            last_event_at: "2026-07-14T10:00:00Z",
            processing_lag_ms: 640,
            data_quality: "live",
            backlog: 0,
            duplicate_events: 3,
            unsupported_events: 2,
            reconciliation_drift: 0,
          },
        }}
      />,
    );

    expect(screen.getByText("AdSet.pro · Live")).toBeInTheDocument();
    expect(screen.getByText("Данные согласованы")).toBeInTheDocument();
    expect(screen.getByText("Регистрации").parentElement).toHaveTextContent("18");
    expect(screen.getByText("FTD").parentElement).toHaveTextContent("6");
    expect(screen.getByText("Подтверждены").parentElement).toHaveTextContent("5");
    expect(screen.getByText("Не сопоставлено").parentElement).toHaveTextContent("1");
    expect(screen.getByText(/Обработка:/).parentElement).toHaveTextContent("640 мс");
    expect(screen.getByText(/Дубли:/).parentElement).toHaveTextContent("3");
    expect(screen.getByText(/Неподдерживаемые:/).parentElement).toHaveTextContent("2");
    expect(screen.getByText(/Расхождение сверки:/).parentElement).toHaveTextContent("0");
  });

  it("честно помечает источник Meta как задержанный", () => {
    render(<MetaDelayedNote />);
    expect(screen.getByText("Meta · возможна задержка")).toBeInTheDocument();
  });
});
