/**
 * Тесты TrackerBlock — карточка «Трекер (AdSet.pro)».
 */
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { TrackerBlock } from "@/components/stats/TrackerBlock";

describe("TrackerBlock", () => {
  // available=true — рендерит реальные метрики трекера + attribution_note.
  it("рендерит метрики трекера когда available=true", () => {
    render(
      <TrackerBlock
        data={{
          available: true,
          day_utc: "2026-07-02",
          attribution_note: "Атрибуция по click_id, окно 7 дней",
          unmatched_events: 1,
          last_event_at: "2026-07-14T10:00:00Z",
          processing_lag_ms: 380,
          data_quality: "live",
          backlog: 0,
          duplicate_events: 0,
          unsupported_events: 0,
          totals: {
            installs: 1000,
            registrations: 300,
            deposits: 50,
            ftds: 50,
            confirmed_deposits: 42,
            redeposits: 4,
            revenue: "2500.00",
            roi_pct: "12.5",
          },
        } as never}
      />,
    );

    expect(screen.getByText("1,000")).toBeInTheDocument();
    expect(screen.getByText("300")).toBeInTheDocument();
    expect(screen.getByText("50")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("Данные согласованы")).toBeInTheDocument();
    expect(screen.getByText("Не сопоставлено").parentElement).toHaveTextContent("1");
    expect(screen.getByText("$2,500.00")).toBeInTheDocument();
    expect(screen.getByText("12.5%")).toBeInTheDocument();
    expect(screen.getByText(/Атрибуция по click_id/)).toBeInTheDocument();
  });

  // available=false — трекер недоступен/запрос упал на бэке; приглушённая заглушка,
  // без фейковых нулей поверх отсутствующих данных.
  it("показывает «Нет данных трекера» когда available=false", () => {
    render(
      <TrackerBlock
        data={{
          available: false,
          day_utc: null,
          attribution_note: "",
          unmatched_events: 0,
          data_quality: "unknown",
          backlog: 0,
          duplicate_events: 0,
          unsupported_events: 0,
        }}
      />,
    );

    expect(screen.getByText("Нет данных трекера")).toBeInTheDocument();
    expect(screen.queryByText("Installs")).not.toBeInTheDocument();
  });

  // loading=true — skeleton, не бросает.
  it("рендерит skeleton при loading=true", () => {
    render(<TrackerBlock loading />);
    expect(screen.getByRole("status", { name: "Загрузка трекера" })).toBeInTheDocument();
  });
});
