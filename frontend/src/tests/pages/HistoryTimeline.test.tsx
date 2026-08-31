import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { HistoryTimeline } from "@/components/history/HistoryTimeline";
import type { components } from "@fb/shared/api/generated";

type HistoryTimelineItem = components["schemas"]["OperatorEventItem"];

const timeline: HistoryTimelineItem[] = [
  {
    event_type: "alert",
    ts: "2026-05-15T14:32:00Z",
    fb_ad_id: "ad_123",
    ad_name: "Test Ad",
    campaign_name: "Test Campaign",
    stage: "stop",
    rule_codes: ["cpl_stop"],
    task_type: null,
    task_status: null,
  },
  {
    event_type: "task",
    ts: "2026-05-15T14:35:00Z",
    fb_ad_id: "ad_123",
    ad_name: "Test Ad",
    campaign_name: null,
    stage: null,
    rule_codes: null,
    task_type: "meta_api_mutation",
    task_status: "DONE",
  },
];

describe("HistoryTimeline", () => {
  it("объединяет stop-алерт и подтверждённое отключение", () => {
    render(<HistoryTimeline items={timeline} isLoading={false} error={null} />);

    expect(screen.getByText(/Test Ad: стоп → отключено в Meta/)).toBeInTheDocument();
    expect(screen.getByText(/15 мая/i)).toBeInTheDocument();
  });

  it("показывает честный empty state", () => {
    render(<HistoryTimeline items={[]} isLoading={false} error={null} />);

    expect(screen.getByText("Событий нет")).toBeInTheDocument();
  });

  it("отдаёт выбранный alert вызывающему экрану", async () => {
    const onAlertClick = vi.fn();
    render(
      <HistoryTimeline
        items={timeline}
        isLoading={false}
        error={null}
        onAlertClick={onAlertClick}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: /открыть карточку объявления/i }));
    expect(onAlertClick).toHaveBeenCalledWith(timeline[0]);
  });

  it("явно помечает алерт без fb_ad_id как несвязанный вместо тихого no-op", () => {
    const onAlertClick = vi.fn();
    const orphanAlert: HistoryTimelineItem = {
      event_type: "alert",
      ts: "2026-05-15T14:32:00Z",
      fb_ad_id: null,
      ad_name: null,
      campaign_name: "Test Campaign",
      stage: "warning",
      rule_codes: ["cpl_warning"],
      task_type: null,
      task_status: null,
    };
    render(
      <HistoryTimeline
        items={[orphanAlert]}
        isLoading={false}
        error={null}
        onAlertClick={onAlertClick}
      />,
    );

    expect(screen.queryByRole("button", { name: /открыть карточку объявления/i })).not.toBeInTheDocument();
    expect(screen.getByText("Без объявления")).toBeInTheDocument();
  });
});
