import { createFileRoute } from "@tanstack/react-router";

import { MiniHeader } from "@/components/layout/MiniHeader";
import { RunsHistory } from "./RunsHistory";

export const Route = createFileRoute("/campaigns/")({
  component: CampaignRunsPage,
});

function CampaignRunsPage() {
  return (
    <div className="flex min-h-full flex-col pb-20">
      <MiniHeader eyebrow="DESKTOP-FIRST" title="Запуски кампаний" />
      <RunsHistory />
    </div>
  );
}
