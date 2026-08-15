import { createFileRoute, Link } from "@tanstack/react-router";
import { Layers3, Plus } from "lucide-react";

import { CampaignRunsHistory } from "@/components/domain/campaigns/CampaignRunsHistory";
import { PageHeader } from "@/components/layout/PageHeader";
import { buttonStyles } from "@/components/ui/Button";

export const Route = createFileRoute("/campaigns/")({
  component: CampaignsPage,
});

/** Campaign creation and its execution journal have the same meaning on web and TMA. */
function CampaignsPage() {
  return (
    <>
      <PageHeader
        title="Кампании"
        subtitle="Создание, ход выполнения и результат"
        action={
          <div className="flex flex-wrap gap-2">
            <Link
              to="/campaigns/presets"
              className={buttonStyles({ variant: "secondary", size: "sm" })}
            >
              <Layers3 size={15} aria-hidden="true" />
              Пресеты
            </Link>
            <Link
              to="/campaigns/create"
              className={buttonStyles({ variant: "primary", size: "sm" })}
            >
              <Plus size={15} aria-hidden="true" />
              Создать
            </Link>
          </div>
        }
      />
      <CampaignRunsHistory />
    </>
  );
}
