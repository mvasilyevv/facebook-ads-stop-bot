import { createFileRoute, Link } from "@tanstack/react-router";
import { Layers3, Plus } from "lucide-react";

import { CampaignRunsHistory } from "@/components/domain/campaigns/CampaignRunsHistory";
import { PageHeader } from "@/components/layout/PageHeader";
import { buttonStyles } from "@/components/ui/Button";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export const Route = createFileRoute("/campaigns/")({
  component: CampaignsPage,
  // ?run=<uuid> открывает залив сразу развёрнутым: действие «создание кампании»
  // ведёт оператора к самому заливу, а не к конвейеру его обработки.
  // Значение из адресной строки не доверенное — что не идентификатор, то отброшено.
  validateSearch: (search: Record<string, unknown>): { run?: string } => {
    const raw = search.run;
    return typeof raw === "string" && UUID_RE.test(raw) ? { run: raw.toLowerCase() } : {};
  },
});

/** Campaign creation and its execution journal have the same meaning on web and TMA. */
function CampaignsPage() {
  const { run } = Route.useSearch();
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
      <CampaignRunsHistory openRunId={run ?? null} />
    </>
  );
}
