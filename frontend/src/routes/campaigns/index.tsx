import { createFileRoute, Link } from "@tanstack/react-router";
import { Plus } from "lucide-react";

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
        eyebrowNum="05"
        eyebrow="РЕКЛАМА · КАМПАНИИ"
        title="Кампании"
        subtitle="Создание, ход выполнения и результат"
        action={
          <Link to="/campaigns/create" className={buttonStyles({ variant: "primary", size: "sm" })}>
            <Plus size={15} aria-hidden="true" />
            Создать
          </Link>
        }
      />
      <CampaignRunsHistory />
    </>
  );
}
