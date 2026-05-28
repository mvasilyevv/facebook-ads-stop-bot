/**
 * /ads/$fbAdId — детальный drawer для одного объявления.
 * Placeholder.
 */

import { createFileRoute } from "@tanstack/react-router";
import { PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { FileText } from "lucide-react";

export const Route = createFileRoute("/ads/$fbAdId")({
  component: AdDetailPage,
});

function AdDetailPage() {
  const { fbAdId } = Route.useParams();
  return (
    <>
      <PageHeader
        eyebrowNum="04"
        eyebrow="OPERATE · AD DETAIL"
        title={`Ad ${fbAdId}.`}
        displayNumber=""
        subtitle="Drill-down view"
      />
      <EmptyState
        icon={<FileText size={40} strokeWidth={1.25} aria-hidden="true" />}
        title="Coming soon"
        description="Round 8.X will implement timeline drawer with metrics, alerts and task history."
      />
    </>
  );
}
