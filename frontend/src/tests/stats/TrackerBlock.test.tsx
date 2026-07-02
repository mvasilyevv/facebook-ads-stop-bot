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
          totals: {
            installs: 1000,
            registrations: 300,
            deposits: 50,
            revenue: "2500.00",
            roi_pct: "12.5",
          },
        }}
      />,
    );

    expect(screen.getByText("1,000")).toBeInTheDocument();
    expect(screen.getByText("300")).toBeInTheDocument();
    expect(screen.getByText("50")).toBeInTheDocument();
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
