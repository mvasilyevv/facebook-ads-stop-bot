/**
 * Offers placeholder.
 */

import { createFileRoute } from "@tanstack/react-router";
import { PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { Tag } from "lucide-react";

export const Route = createFileRoute("/offers/")({
  component: OffersPage,
});

function OffersPage() {
  return (
    <>
      <PageHeader
        eyebrowNum="02"
        eyebrow="CATALOG"
        title="Offers."
        displayNumber="02"
        subtitle="0 active · 0 inactive"
      />
      <EmptyState
        icon={<Tag size={40} strokeWidth={1.25} aria-hidden="true" />}
        title="Coming soon"
        description="Round 8.X will implement offer grid cards, rules editor drawer, and compare endpoint integration."
      />
    </>
  );
}
