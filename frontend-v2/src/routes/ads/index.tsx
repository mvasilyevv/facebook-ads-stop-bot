/**
 * Ads placeholder.
 */

import { createFileRoute } from "@tanstack/react-router";
import { PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { Layers } from "lucide-react";

export const Route = createFileRoute("/ads/")({
  component: AdsPage,
});

function AdsPage() {
  return (
    <>
      <PageHeader
        eyebrowNum="04"
        eyebrow="OPERATE"
        title="Ads."
        displayNumber="04"
        subtitle="0 active · 0 warning · 0 stop"
      />
      <EmptyState
        icon={<Layers size={40} strokeWidth={1.25} aria-hidden="true" />}
        title="Coming soon"
        description="Round 8.X will implement virtualized ads table with bulk actions, drawer, and keyboard navigation."
      />
    </>
  );
}
