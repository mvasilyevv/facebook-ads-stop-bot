import type { Meta, StoryObj } from "@storybook/react-vite";

import type { AnalyticsDaypart } from "@fb/shared";

import { DaypartHeatmap } from "../src/components/analytics/DaypartHeatmap";

const data: AnalyticsDaypart = {
  state: "partial",
  as_of: "2026-07-20T18:00:00Z",
  freshness_seconds: 120,
  issues: ["Tracker не подтвердил часть часовых интервалов"],
  sources: {
    meta: {
      source: "meta",
      status: "good",
      last_event_at: "2026-07-20T18:00:00Z",
      lag_seconds: 120,
      unmatched_events: 0,
      missing_timezone_account_ids: [],
      issues: [],
    },
    tracker: {
      source: "tracker",
      status: "degraded",
      last_event_at: "2026-07-20T17:57:00Z",
      lag_seconds: 300,
      unmatched_events: 0,
      missing_timezone_account_ids: [],
      issues: ["Нет регистрации для одного Meta-интервала"],
    },
  },
  timezone: "Europe/Kaliningrad",
  from_iso: "2026-07-14T00:00:00Z",
  to_iso: "2026-07-21T00:00:00Z",
  cells: [
    { weekday: 1, hour: 9, clicks: 12, registrations: 0, ftds: 0 },
    { weekday: 1, hour: 10, clicks: 7, registrations: null, ftds: null },
    { weekday: 3, hour: 18, clicks: null, registrations: 2, ftds: 1 },
  ],
};

function AnalyticsEvidenceStates() {
  return (
    <main className="min-h-screen bg-bg-0 p-4 text-bg-11 sm:p-8">
      <div className="mx-auto max-w-6xl rounded-[var(--radius-3)] border border-[var(--color-hairline)] bg-bg-1 p-5">
        <DaypartHeatmap data={data} windowState="ready" />
      </div>
    </main>
  );
}

const meta = {
  title: "Analytics/Evidence states",
  component: AnalyticsEvidenceStates,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof AnalyticsEvidenceStates>;

export default meta;
type Story = StoryObj<typeof meta>;

export const SparseKnownZeroAndUnknown: Story = {};
