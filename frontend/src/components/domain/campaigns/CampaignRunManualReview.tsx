import { AlertTriangle, ExternalLink } from "lucide-react";
import { adsManagerCampaignUrl, campaignMetaIdGroups } from "@fb/shared";

import { buttonStyles } from "@/components/ui/Button";
import { cn } from "@/lib/utils/cn";

interface CampaignRunManualReviewProps {
  createdMetaIds: Record<string, unknown>;
}

export function CampaignRunManualReview({ createdMetaIds }: CampaignRunManualReviewProps) {
  const groups = campaignMetaIdGroups(createdMetaIds);
  const populatedGroups = groups.filter((group) => group.ids.length > 0);
  const adsManagerUrl = adsManagerCampaignUrl(createdMetaIds);

  return (
    <section
      role="alert"
      aria-label="Требуется ручная сверка"
      className="rounded-[var(--radius-3)] border border-warning/40 bg-warning/10 p-4 text-warning"
    >
      <div className="flex items-start gap-3">
        <AlertTriangle size={18} className="mt-0.5 shrink-0" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <h3 className="m-0 font-display text-[14px] font-medium text-bg-11">
            Требуется ручная сверка
          </h3>
          <p className="mt-1 text-[13px] leading-5 text-bg-9">
            Результат создания неоднозначен. Не повторяйте запуск и не удаляйте объекты вслепую —
            сначала проверьте их фактическое состояние в Ads Manager.
          </p>
        </div>
      </div>

      {populatedGroups.length > 0 ? (
        <div className="mt-4 space-y-3">
          {populatedGroups.map((group) => (
            <div key={group.key}>
              <div className="text-[12px] font-semibold uppercase tracking-wider text-bg-8">
                {group.label} · {group.ids.length}
              </div>
              <div className="mt-1 break-all font-mono text-[12px] leading-5 text-bg-11">
                {group.ids.join(", ")}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <p className="mt-3 rounded-[var(--radius-2)] bg-bg-2 px-3 py-2 text-[12px] leading-5 text-bg-9">
          Подтверждённых идентификаторов в Meta нет. Это не доказывает, что объект не был создан:
          ответ мог потеряться после внешнего вызова.
        </p>
      )}

      {adsManagerUrl ? (
        <a
          href={adsManagerUrl}
          target="_blank"
          rel="noopener noreferrer"
          className={cn(
            buttonStyles({ variant: "secondary", size: "sm" }),
            "mt-4 w-full sm:w-auto",
          )}
        >
          Открыть Ads Manager
          <ExternalLink size={14} aria-hidden="true" />
        </a>
      ) : null}
    </section>
  );
}
