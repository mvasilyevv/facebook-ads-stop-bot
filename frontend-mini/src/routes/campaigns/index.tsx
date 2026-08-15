import { createFileRoute, Link } from "@tanstack/react-router";
import { Layers3, Plus } from "lucide-react";

import { MiniHeader } from "@/components/layout/MiniHeader";
import { RunsHistory } from "./RunsHistory";

export const Route = createFileRoute("/campaigns/")({
  component: CampaignRunsPage,
});

function CampaignRunsPage() {
  return (
    <div className="flex min-h-full flex-col pb-20">
      <MiniHeader
        eyebrowNum="05"
        eyebrow="РЕКЛАМА · КАМПАНИИ"
        title="Кампании"
        right={
          <Link
            to="/campaigns/create"
            className="flex min-h-11 items-center gap-2 rounded-[var(--radius-2)] bg-accent px-3 text-[13px] font-semibold text-bg-0 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            <Plus size={16} aria-hidden="true" />
            Создать
          </Link>
        }
      />
      <div className="px-4 pt-4">
        <Link
          to="/campaigns/presets"
          className="inline-flex min-h-11 items-center gap-2 text-[13px] text-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          <Layers3 size={16} aria-hidden="true" />
          Управлять пресетами
        </Link>
      </div>
      <RunsHistory />
    </div>
  );
}
