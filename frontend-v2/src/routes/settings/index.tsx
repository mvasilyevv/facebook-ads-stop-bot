/**
 * Settings placeholder.
 */

import { createFileRoute } from "@tanstack/react-router";
import { PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { Settings as SettingsIcon } from "lucide-react";

export const Route = createFileRoute("/settings/")({
  component: SettingsPage,
});

function SettingsPage() {
  return (
    <>
      <PageHeader
        eyebrowNum="05"
        eyebrow="SYSTEM"
        title="Settings."
        displayNumber="05"
        subtitle="Observer · Telegram · Vision · Workers · Health"
      />
      <EmptyState
        icon={<SettingsIcon size={40} strokeWidth={1.25} aria-hidden="true" />}
        title="Coming soon"
        description="Round 8.X will implement tab-based settings panels: observer / telegram / vision / workers."
      />
    </>
  );
}
