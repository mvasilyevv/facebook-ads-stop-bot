/**
 * StepPreview — шаг 6 визарда: dry-run spec + выбор launch_state.
 * Зовёт POST /tools/campaigns/validate, показывает план без создания.
 */
import { useEffect, useState } from "react";
import { CheckCircle, ChevronDown, ChevronUp } from "lucide-react";
import { Button, Select, Skeleton } from "@/components/ui";
import { Eyebrow } from "@/components/data";
import { haptic } from "@/lib/tg";
import { useValidateCampaign } from "@/lib/api";
import type { CampaignConfig, ValidatePlanResponse, CampaignPlan } from "@/lib/campaignTypes";
import { useWizardStore } from "./-wizardStore";
import { cn } from "@/lib/cn";

const LAUNCH_STATE_OPTIONS = [
  { value: "campaign_paused", label: "Кампания на паузе (рекомендуется)" },
  { value: "all_paused",      label: "Всё на паузе" },
];

function CampaignPlanCard({ plan }: { plan: CampaignPlan }) {
  const [expanded, setExpanded] = useState(false);
  const totalAds = plan.adsets.reduce((s, a) => s + a.ad_count, 0);
  return (
    <div className="border border-[var(--hairline)] rounded-[var(--radius-3)] overflow-hidden">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center justify-between px-3.5 py-3 min-h-[52px] bg-bg-1 active:bg-bg-2"
      >
        <div className="flex-1 min-w-0 text-left">
          <p className="font-display text-[13px] text-bg-11 truncate leading-snug">{plan.name}</p>
          <p className="font-display tabular-nums text-[11px] text-bg-8 mt-0.5">
            {plan.kind} · {plan.adsets.length} адс · {totalAds} ads · {plan.status}
          </p>
        </div>
        {expanded ? (
          <ChevronUp size={15} strokeWidth={1.8} className="text-bg-8 shrink-0" />
        ) : (
          <ChevronDown size={15} strokeWidth={1.8} className="text-bg-8 shrink-0" />
        )}
      </button>
      {expanded && (
        <div className="divide-y divide-[var(--hairline)] border-t border-[var(--hairline)]">
          {plan.adsets.map((adset, i) => (
            <div key={i} className="flex items-center justify-between px-4 py-2.5 min-h-[44px] bg-bg-0">
              <p className="text-[12px] text-bg-10 truncate flex-1">{adset.name}</p>
              <span className="font-display tabular-nums text-[11px] text-bg-8 shrink-0 ml-3">
                {adset.ad_count} ads
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function PlanSummary({ plan }: { plan: ValidatePlanResponse }) {
  return (
    <div className="flex flex-col gap-3">
      {/* KPI-строка */}
      <div className="grid grid-cols-3 gap-2">
        {[
          { label: "Кампаний", value: plan.campaign_count },
          { label: "Адсетов",  value: plan.adset_count },
          { label: "Ads",      value: plan.ad_count },
        ].map((kpi) => (
          <div
            key={kpi.label}
            className="border border-[var(--hairline)] bg-bg-1 rounded-[var(--radius-2)] px-3 py-2.5 text-center"
          >
            <p className="font-display tabular-nums text-[20px] text-bg-11">{kpi.value}</p>
            <p className="text-[10px] uppercase tracking-[0.07em] text-bg-7 mt-0.5">{kpi.label}</p>
          </div>
        ))}
      </div>

      <div className="border border-[var(--hairline)] bg-bg-1 px-3.5 py-2.5 rounded-[var(--radius-2)]">
        <p className="text-[10px] uppercase tracking-[0.08em] text-bg-7 mb-1">Оффер / статус</p>
        <p className="font-display text-[13px] text-bg-11">
          {plan.offer_code} · {plan.launch_state} · {plan.copies_per_concept} копий/концепт
        </p>
      </div>

      {/* Список кампаний */}
      <div className="flex flex-col gap-2">
        {plan.campaigns.map((c) => (
          <CampaignPlanCard key={c.key} plan={c} />
        ))}
      </div>
    </div>
  );
}

export function StepPreview() {
  const { config, updateConfig, setValidatePlan, nextStep, prevStep } = useWizardStore();
  const validate = useValidateCampaign();
  const [launchState, setLaunchState] = useState<"campaign_paused" | "all_paused">(
    config.launch_state ?? "campaign_paused",
  );
  const [error, setError] = useState<string | null>(null);

  // Автозапуск dry-run при первом рендере
  useEffect(() => {
    if (!validate.data && !validate.isPending) {
      void validate.mutateAsync(config as CampaignConfig).catch((err) => {
        setError((err as Error).message);
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleLaunchState(val: string) {
    const v = val as "campaign_paused" | "all_paused";
    setLaunchState(v);
    updateConfig({ launch_state: v });
  }

  function handleNext() {
    if (!validate.data) {
      setError("Дождитесь завершения проверки");
      return;
    }
    haptic.impact("medium");
    setValidatePlan(validate.data);
    nextStep();
  }

  return (
    <div className="flex flex-col gap-4 p-4 pb-8">
      <Eyebrow num="06">ПРЕВЬЮ</Eyebrow>

      {/* Сухой прогон */}
      {validate.isPending && (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-[80px]" />
          <Skeleton className="h-[52px]" />
          <Skeleton className="h-[52px]" />
        </div>
      )}

      {validate.data && !validate.isPending && (
        <>
          <div className="flex items-center gap-2 text-[var(--color-success)]">
            <CheckCircle size={16} strokeWidth={2} aria-hidden />
            <span className="text-[13px] font-medium">Конфиг валидный</span>
          </div>
          <PlanSummary plan={validate.data} />
        </>
      )}

      {/* Выбор launch_state */}
      <Select
        label="Статус запуска"
        value={launchState}
        options={LAUNCH_STATE_OPTIONS}
        onChange={(e) => handleLaunchState(e.target.value)}
      />

      {/* Повторить проверку */}
      {!validate.isPending && (
        <button
          type="button"
          onClick={() => {
            setError(null);
            void validate.mutateAsync(config as CampaignConfig).catch((err) => {
              setError((err as Error).message);
            });
          }}
          className={cn(
            "text-[12px] text-bg-8 underline underline-offset-2",
            "min-h-[44px] text-center",
          )}
        >
          Повторить проверку
        </button>
      )}

      {(error ?? validate.error) !== null && (
        <p className="text-[12px] text-[var(--color-danger)]">
          {error ?? (validate.error as Error | null)?.message}
        </p>
      )}

      <div className="flex flex-col gap-3 mt-2">
        <Button
          fullWidth
          onClick={handleNext}
          disabled={!validate.data || validate.isPending}
        >
          Запустить залив
        </Button>
        <Button variant="ghost" fullWidth onClick={() => { haptic.selection(); prevStep(); }}>
          Назад
        </Button>
      </div>
    </div>
  );
}
