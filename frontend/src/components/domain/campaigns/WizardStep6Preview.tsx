/**
 * Шаг 6 — dry-run preview неизменяемого all-paused плана.
 *
 * Вызывает POST /tools/campaigns/validate и показывает план:
 * - число кампаний / adset'ов / ads
 * - нейминг
 * - явный safety-инвариант: campaign/ad set/ad создаются PAUSED
 */

import { type FC, useEffect } from "react";
import { safeApiProblemMessage } from "@fb/operator-api";
import {
  CheckCircle,
  AlertCircle,
  Layers,
  ChevronDown,
  ChevronRight,
  ShieldCheck,
} from "lucide-react";
import { useState } from "react";
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

export const WizardStep6Preview: FC<WizardStep6PreviewProps> = ({ config, preview, onChange }) => {
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

  // Блоки без концептов: если кампания не получила ни одного концепта,
  // launch отобьёт 422 — предупреждаем заранее, чтобы байер вернулся на шаг 5.
  const emptyBlocks = config.campaigns
    .filter((c) => (c.concept_refs ?? []).length === 0)
    .map((c) => c.key);

  return (
    <div className="space-y-6">
      {/* Заголовок */}
      <div>
        <div className="font-display text-[12px] tracking-[0.14em] uppercase text-bg-8 mb-1">
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
            <span>{emptyBlocks.join(", ")}</span>. Вернись на шаг 5 — запуск
            таких кампаний будет отклонён.
          </span>
        </div>
      )}

      {/* Dry-run plan */}
      <div className="border border-[var(--color-hairline)] rounded-[var(--radius-3)] overflow-hidden">
        {/* Шапка */}
        <div className="flex items-center justify-between px-4 py-3 bg-bg-2 border-b border-[var(--color-hairline)]">
          <span className="font-display text-[12px] tracking-wider uppercase text-bg-8">
            ПЛАН ЗАЛИВА
          </span>
          <Button
            variant="secondary"
            size="sm"
            onClick={refreshPlan}
            loading={validateMut.isPending}
            className="min-h-11"
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
              {safeApiProblemMessage(
                validateMut.error,
                "Не удалось подтвердить план. Проверьте контекст кабинета и повторите.",
              )}
            </div>
          )}

          {plan && <PlanView plan={plan} />}
        </div>
      </div>

      <div
        role="status"
        className="flex items-start gap-3 rounded-[var(--radius-2)] border border-warning/35 bg-warning/10 p-4"
      >
        <ShieldCheck size={18} className="mt-0.5 shrink-0 text-warning" aria-hidden="true" />
        <div>
          <div className="font-display text-[13px] font-medium text-bg-11">
            Всё создаётся на паузе
          </div>
          <div className="mt-1 text-[12px] text-bg-9">
            Кампания, ad set и ad останутся PAUSED. Активация доступна только отдельным ручным
            действием после review.
          </div>
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
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[
          { label: "Кампаний", value: plan.campaign_count },
          { label: "Adset'ов", value: plan.adset_count },
          { label: "Объявлений", value: plan.ad_count },
          { label: "Копий/конц.", value: plan.copies_per_concept },
        ].map(({ label, value }) => (
          <div
            key={label}
            className="border border-[var(--color-hairline)] rounded-[var(--radius-2)] p-3 bg-bg-1 text-center"
          >
            <div className="font-display text-[20px] font-medium text-bg-11">{value}</div>
            <div className="text-[12px] text-bg-8 font-display uppercase tracking-wider mt-0.5">
              {label}
            </div>
          </div>
        ))}
      </div>

      <div className="rounded-[var(--radius-2)] border border-success/30 bg-success/10 p-3">
        <div className="flex items-center gap-2 font-display text-[12px] uppercase tracking-wider text-success">
          <CheckCircle size={13} aria-hidden="true" />
          Контекст кабинета подтверждён сервером
        </div>
        <dl className="mt-2 grid grid-cols-1 gap-x-4 gap-y-2 text-[12px] sm:grid-cols-2">
          <div>
            <dt className="text-bg-8">Старт</dt>
            <dd className="mt-0.5 font-numeric text-bg-11">{plan.start_time}</dd>
          </div>
          <div>
            <dt className="text-bg-8">Часовой пояс</dt>
            <dd className="mt-0.5 text-bg-11">{plan.timezone_name}</dd>
          </div>
          <div>
            <dt className="text-bg-8">Валюта</dt>
            <dd className="mt-0.5 text-bg-11">{plan.currency}</dd>
          </div>
          <div>
            <dt className="text-bg-8">Снимок Meta</dt>
            <dd className="mt-0.5 text-bg-11">
              {new Date(plan.account_context_observed_at).toLocaleString("ru-RU")}
            </dd>
          </div>
        </dl>
      </div>

      {/* Нейминг по кампаниям */}
      <div>
        <div className="font-display text-[12px] tracking-[0.14em] uppercase text-bg-8 mb-2">
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
        <span className="text-bg-8">·</span>
        Политика: <span className="text-bg-11 font-numeric">{plan.creation_policy}</span>
      </div>
    </div>
  );
}

// ─── CampaignPlanRow ──────────────────────────────────────────────────────────

function CampaignPlanRow({ campaign }: { campaign: CampaignPlanOut }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="border border-[var(--color-hairline)] rounded-[var(--radius-2)] overflow-hidden">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex min-h-11 w-full items-center gap-2 bg-bg-2 px-3 py-2.5 text-left transition-colors hover:bg-bg-3"
        aria-expanded={expanded}
      >
        {expanded ? (
          <ChevronDown size={13} className="text-bg-8 shrink-0" />
        ) : (
          <ChevronRight size={13} className="text-bg-8 shrink-0" />
        )}
        <Layers size={13} className="text-bg-8 shrink-0" />
        <span className="min-w-0 text-[12px] text-bg-11 flex-1 truncate" title={campaign.name}>
          {campaign.name}
        </span>
        <span className="text-[12px] text-bg-8 font-display uppercase tracking-wider shrink-0">
          {campaign.adsets.length} adset
          {campaign.adsets.length !== 1 ? "s" : ""}
        </span>
      </button>
      {expanded && (
        <div className="divide-y divide-[var(--color-hairline)]">
          {campaign.adsets.map((adset) => (
            <div key={adset.name} className="px-6 py-2 flex items-center gap-2 bg-bg-1">
              <span className="min-w-0 text-[12px] text-bg-9 flex-1 truncate" title={adset.name}>
                {adset.name}
              </span>
              <span className="text-[12px] text-bg-8">{adset.ad_count} ads</span>
              <span className="inline-flex items-center gap-1 rounded bg-bg-3 px-1.5 py-0.5 font-display text-[12px] uppercase tracking-wider text-bg-8">
                <ShieldCheck size={11} aria-hidden="true" />
                {adset.status}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
