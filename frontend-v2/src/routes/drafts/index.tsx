/**
 * Drafts placeholder.
 */

import { createFileRoute } from "@tanstack/react-router";
import { PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { FileEdit } from "lucide-react";

export const Route = createFileRoute("/drafts/")({
  component: DraftsPage,
});

function DraftsPage() {
  return (
    <>
      <PageHeader
        eyebrowNum="04"
        eyebrow="OPERATE · DRAFTS"
        title="Drafts."
        displayNumber="04"
        subtitle="0 pending · 0 expiring within 1h"
      />
      <EmptyState
        icon={<FileEdit size={40} strokeWidth={1.25} aria-hidden="true" />}
        title="No pending drafts"
        description="AI is quiet today. When a draft appears, it will live here with diff and approval controls."
      />
    </>
  );
}
