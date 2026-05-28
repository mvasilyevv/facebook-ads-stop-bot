/**
 * History placeholder.
 */

import { createFileRoute } from "@tanstack/react-router";
import { PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { Clock } from "lucide-react";

export const Route = createFileRoute("/history/")({
  component: HistoryPage,
});

function HistoryPage() {
  return (
    <>
      <PageHeader
        eyebrowNum="03"
        eyebrow="HISTORY"
        title="History."
        displayNumber="03"
        subtitle="Last 30 days · 0 events"
      />
      <EmptyState
        icon={<Clock size={40} strokeWidth={1.25} aria-hidden="true" />}
        title="Coming soon"
        description="Round 8.X will implement timeline view, summary stats, and drill-down filters."
      />
    </>
  );
}
