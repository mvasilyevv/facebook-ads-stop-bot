import { createFileRoute, Link } from "@tanstack/react-router";
import { History } from "lucide-react";

import { MiniHeader } from "@/components/layout/MiniHeader";
import { CampaignWizard } from "@/features/campaigns/CampaignWizard";
import { getStoredRole } from "@/lib/auth";

export const Route = createFileRoute("/campaigns/create/")({
  component: CampaignCreatePage,
});

function CampaignCreatePage() {
  const owner = getStoredRole() === "owner";
  return (
    <div className="flex min-h-full min-w-0 flex-col">
      <MiniHeader
        eyebrowNum="05"
        eyebrow="РЕКЛАМА · СОЗДАНИЕ"
        title="Новая кампания"
        right={
          <Link
            to="/campaigns"
            aria-label="История запусков"
            className="flex size-11 items-center justify-center rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] text-bg-10 focus-visible:outline-2 focus-visible:outline-accent"
          >
            <History size={18} aria-hidden="true" />
          </Link>
        }
      />
      {owner ? (
        <CampaignWizard />
      ) : (
        <div className="px-4 py-6">
          <p
            role="status"
            className="rounded-[var(--radius-2)] border border-warning/35 bg-warning/10 p-4 text-[14px] leading-5 text-bg-10"
          >
            Создание доступно только owner. Получатели уведомлений могут
            просматривать ход и результат запусков.
          </p>
        </div>
      )}
    </div>
  );
}
