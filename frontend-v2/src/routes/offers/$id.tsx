/**
 * /offers/$id — rules editor placeholder.
 */

import { createFileRoute } from "@tanstack/react-router";
import { PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { Settings2 } from "lucide-react";

export const Route = createFileRoute("/offers/$id")({
  component: OfferRulesPage,
});

function OfferRulesPage() {
  const { id } = Route.useParams();
  return (
    <>
      <PageHeader
        eyebrowNum="02"
        eyebrow="CATALOG · OFFER"
        title={`Offer ${id}.`}
        displayNumber=""
        subtitle="Rules editor"
      />
      <EmptyState
        icon={<Settings2 size={40} strokeWidth={1.25} aria-hidden="true" />}
        title="Coming soon"
        description="Round 8.X will implement 6-threshold rules editor."
      />
    </>
  );
}
