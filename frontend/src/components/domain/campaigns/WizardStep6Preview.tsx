/**
 * Шаг 6 — Превью dry-run + выбор launch_state.
 *
 * Вызывает POST /tools/campaigns/validate и показывает план:
 * - число кампаний / adset'ов / ads
 * - нейминг
 * - launch_state (campaign_paused / all_paused)
 */

import { type FC, useEffect } from "react";
import {
  CheckCircle,
  AlertCircle,
  Layers,
  ChevronDown,
  ChevronRight,
  ShieldCheck,
  ShieldAlert,
} from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils/cn";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { useValidateConfig } from "@/lib/api/campaigns";
import type { CampaignConfig, ValidatePlanOut, CampaignPlanOut } from "@/lib/api/campaigns";
import type { WizardPreview } from "@/stores/campaignWizard";

interface WizardStep6PreviewProps {
  config: CampaignConfig;
  preview: WizardPreview;
  onChange: (v: Partial<WizardPreview>) => void;
}

const LAUNCH_STATE_OPTIONS: {
  value: "campaign_paused" | "all_paused";
  icon: typeof ShieldCheck;
  label: string;
  desc: string;
}[] = [
  {
    value: "campaign_paused",
    icon: ShieldCheck,
    label: "Кампания PAUSED, дети активны",
    desc: "Дефолт. Спенда нет, модерация идёт. Снимаешь паузу одним тумблером — всё стартует.",
  },
  {
    value: "all_paused",
    icon: ShieldAlert,
    label: "Всё PAUSED (кампания + adset'ы + ads)",
    desc: "Максимальная пауза. Нужно активировать вручную на каждом уровне.",
  },
];

export const WizardStep6Preview: FC<WizardStep6PreviewProps> = ({
  config,
  preview,
  onChange,
}) => {
  const validateMut = useValidateConfig();

  // Автозапуск dry-run при монтировании шага
  useEffect(() => {
    if (!preview.plan) {
      validateMut.mutate(config, {
        onSuccess: (plan) => onChange({ plan }),
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refreshPlan = () => {
    validateMut.mutate(config, {
      onSuccess: (plan) => onChange({ plan }),
    });
  };

  const plan = preview.plan;

  // Блоки без концептов: после kind-фильтра buildConfig видео-кампания без видео (или
  // наоборот) получает пустой concept_refs. Launch такой блок отобьёт 422 — предупреждаем
  // заранее, чтобы байер вернулся на шаг 5 и не упёрся в ошибку на запуске.
  const emptyBlocks = config.campaigns
    .filter((c) => (c.concept_refs ?? []).length === 0)
    .map((c) => c.key);

  return (
    <div className="space-y-6">
      {/* Заголовок */}
      <div>
        <div className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-7 mb-1">
          ШАГ 6 · ПРЕВЬЮ
        </div>
        <h2 className="font-display text-[20px] font-medium text-bg-11 leading-tight m-0">
          Dry-run: что создастся
        </h2>
        <p className="text-[13px] text-bg-9 mt-1">
          Без реального создания в Meta — только план нейминга и подсчёт объектов.
        </p>
      </div>

      {/* Предупреждение о кампаниях без концептов */}
      {emptyBlocks.length > 0 && (
        <div
          role="alert"
          className="flex items-start gap-2 text-[12px] text-danger bg-danger/10 border border-danger/30 rounded-[var(--radius-2)] px-3 py-2.5"
        >
          <AlertCircle size={14} className="shrink-0 mt-0.5" />
          <span>
            Без концептов (нет файлов подходящего типа):{" "}
            <span className="font-mono">{emptyBlocks.join(", ")}</span>. Вернись на шаг 5 —
            запуск таких кампаний будет отклонён.
          </span>
        </div>
      )}

      {/* Dry-run plan */}
      <div className="border border-[var(--hairline)] rounded-[var(--radius-3)] overflow-hidden">
        {/* Шапка */}
        <div className="flex items-center justify-between px-4 py-3 bg-bg-2 border-b border-[var(--hairline)]">
          <span className="font-display text-[11px] tracking-wider uppercase text-bg-8">
            ПЛАН ЗАЛИВА
          </span>
          <Button
            variant="secondary"
            size="sm"
            onClick={refreshPlan}
            loading={validateMut.isPending}
          >
            Пересчитать
          </Button>
        </div>

        {/* Контент */}
        <div className="p-4">
          {validateMut.isPending && !plan && (
            <div className="space-y-2">
              <Skeleton className="h-5 w-2/3" />
              <Skeleton className="h-5 w-1/2" />
              <Skeleton className="h-5 w-3/4" />
            </div>
          )}

          {validateMut.isError && (
            <div
              role="alert"
              className="flex items-center gap-2 text-[12px] text-danger bg-danger/10 border border-danger/30 rounded-[var(--radius-2)] px-3 py-2"
            >
              <AlertCircle size={13} className="shrink-0" />
              {validateMut.error instanceof Error
                ? validateMut.error.message
                : "Ошибка валидации конфига"}
            </div>
          )}

          {plan && <PlanView plan={plan} />}
        </div>
      </div>

      {/* launch_state */}
      <div>
        <div className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-7 mb-3">
          СТАТУС ПРИ СОЗДАНИИ (launch_state)
        </div>
        <div className="space-y-2">
          {LAUNCH_STATE_OPTIONS.map(({ value, icon: Icon, label, desc }) => {
            const isActive = preview.launch_state === value;
            return (
              <button
                key={value}
                type="button"
                onClick={() => onChange({ launch_state: value })}
                className={cn(
                  "w-full text-left p-4 rounded-[var(--radius-2)] border flex items-start gap-3 transition-all duration-[120ms]",
                  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
                  isActive
                    ? "bg-accent-bg border-accent"
                    : "bg-bg-2 border-[var(--hairline)] hover:border-[var(--hairline-strong)]",
                )}
                aria-pressed={isActive}
              >
                <Icon
                  size={18}
                  className={cn("shrink-0 mt-0.5", isActive ? "text-accent" : "text-bg-7")}
                />
                <div>
                  <div
                    className={cn(
                      "font-display text-[13px] font-medium mb-0.5",
                      isActive ? "text-accent" : "text-bg-11",
                    )}
                  >
                    {label}
                  </div>
                  <div className="text-[12px] text-bg-8">{desc}</div>
                </div>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
};

// ─── PlanView — детальный план ────────────────────────────────────────────────

function PlanView({ plan }: { plan: ValidatePlanOut }) {
  return (
    <div className="space-y-4">
      {/* Счётчики */}
      <div className="grid grid-cols-4 gap-3">
        {[
          { label: "Кампаний", value: plan.campaign_count },
          { label: "Adset'ов", value: plan.adset_count },
          { label: "Объявлений", value: plan.ad_count },
          { label: "Копий/конц.", value: plan.copies_per_concept },
        ].map(({ label, value }) => (
          <div
            key={label}
            className="border border-[var(--hairline)] rounded-[var(--radius-2)] p-3 bg-bg-1 text-center"
          >
            <div className="font-display text-[20px] font-medium text-bg-11">{value}</div>
            <div className="text-[10px] text-bg-7 font-display uppercase tracking-wider mt-0.5">
              {label}
            </div>
          </div>
        ))}
      </div>

      {/* Нейминг по кампаниям */}
      <div>
        <div className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-7 mb-2">
          НЕЙМИНГ
        </div>
        <div className="space-y-2">
          {plan.campaigns.map((camp) => (
            <CampaignPlanRow key={camp.key} campaign={camp} />
          ))}
        </div>
      </div>

      {/* Оффер + статус */}
      <div className="flex items-center gap-2 text-[12px] text-bg-8">
        <CheckCircle size={13} className="text-success" />
        Оффер: <span className="text-bg-11 font-medium">{plan.offer_code}</span>
        <span className="text-bg-7">·</span>
        launch_state: <span className="text-bg-11 font-mono">{plan.launch_state}</span>
      </div>
    </div>
  );
}

// ─── CampaignPlanRow ──────────────────────────────────────────────────────────

function CampaignPlanRow({ campaign }: { campaign: CampaignPlanOut }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="border border-[var(--hairline)] rounded-[var(--radius-2)] overflow-hidden">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-2.5 bg-bg-2 hover:bg-bg-3 transition-colors text-left"
      >
        {expanded ? (
          <ChevronDown size={13} className="text-bg-7 shrink-0" />
        ) : (
          <ChevronRight size={13} className="text-bg-7 shrink-0" />
        )}
        <Layers size={13} className="text-bg-7 shrink-0" />
        <span className="font-mono text-[12px] text-bg-11 flex-1 truncate" title={campaign.name}>
          {campaign.name}
        </span>
        <span className="text-[10px] text-bg-7 font-display uppercase tracking-wider shrink-0">
          {campaign.kind} · {campaign.adsets.length} adset
          {campaign.adsets.length !== 1 ? "s" : ""}
        </span>
      </button>
      {expanded && (
        <div className="divide-y divide-[var(--hairline)]">
          {campaign.adsets.map((adset) => (
            <div key={adset.name} className="px-6 py-2 flex items-center gap-2 bg-bg-1">
              <span className="font-mono text-[11px] text-bg-9 flex-1 truncate" title={adset.name}>
                {adset.name}
              </span>
              <span className="text-[10px] text-bg-7">{adset.ad_count} ads</span>
              <span
                className={cn(
                  "text-[10px] font-display uppercase tracking-wider px-1.5 py-0.5 rounded",
                  adset.status === "ACTIVE"
                    ? "bg-success/10 text-success"
                    : "bg-bg-3 text-bg-7",
                )}
              >
                {adset.status}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
