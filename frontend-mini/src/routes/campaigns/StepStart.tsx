/**
 * StepStart — шаг 1 визарда: новый / из пресета / клон.
 * Выбор режима старта. Загружает список пресетов для выбора.
 */
import { PlusCircle, Layers, Copy } from "lucide-react";
import { Badge, Skeleton, EmptyState } from "@/components/ui";
import { Eyebrow } from "@/components/data";
import { haptic } from "@/lib/tg";
import { useCampaignPresets } from "@/lib/api";
import type { CampaignPreset } from "@/lib/campaignTypes";
import { useWizardStore } from "./-wizardStore";
import { cn } from "@/lib/cn";

interface StepStartProps {
  onCloneRun: () => void;
}

export function StepStart({ onCloneRun }: StepStartProps) {
  const { data: presets, isLoading, isError } = useCampaignPresets();
  const { setPreset, nextStep } = useWizardStore();

  function handleNew() {
    haptic.selection();
    setPreset(null);
    nextStep();
  }

  function handlePreset(preset: CampaignPreset) {
    haptic.impact("medium");
    setPreset(preset);
    nextStep();
  }

  function handleClone() {
    haptic.selection();
    onCloneRun();
  }

  return (
    <div className="flex flex-col gap-5 p-4 pb-8">
      <Eyebrow num="01">РЕЖИМ СТАРТА</Eyebrow>

      {/* Новый (с нуля) */}
      <button
        type="button"
        onClick={handleNew}
        className={cn(
          "w-full text-left min-h-[64px] px-4 py-3.5",
          "border border-[var(--hairline)] bg-bg-1 rounded-[var(--radius-3)]",
          "flex items-center gap-3 active:bg-bg-2",
        )}
      >
        <PlusCircle size={20} strokeWidth={1.6} className="text-accent shrink-0" aria-hidden />
        <div>
          <p className="font-display text-[14px] text-bg-11 leading-snug">Новая кампания</p>
          <p className="text-[12px] text-bg-8 mt-0.5">С нуля, без пресета</p>
        </div>
      </button>

      {/* Из пресета */}
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <Layers size={14} strokeWidth={1.6} className="text-bg-8" aria-hidden />
          <Eyebrow>ИЗ ПРЕСЕТА</Eyebrow>
        </div>

        {isLoading && (
          <div className="flex flex-col gap-2">
            {Array.from({ length: 2 }, (_, i) => (
              <Skeleton key={i} className="h-[60px]" />
            ))}
          </div>
        )}

        {isError && !isLoading && (
          <EmptyState
            title="Ошибка загрузки пресетов"
            description="Проверьте соединение"
          />
        )}

        {!isLoading && !isError && (presets ?? []).length === 0 && (
          <p className="text-[13px] text-bg-8 px-1">Пресетов нет — начните с новой кампании</p>
        )}

        {!isLoading && !isError && (presets ?? []).length > 0 && (
          <div className="flex flex-col gap-0 border border-[var(--hairline)] divide-y divide-[var(--hairline)] rounded-[var(--radius-3)] overflow-hidden">
            {(presets ?? []).map((preset) => (
              <button
                key={preset.id}
                type="button"
                onClick={() => handlePreset(preset)}
                className="w-full text-left bg-bg-1 px-3.5 py-3 min-h-[52px] flex items-center justify-between gap-3 active:bg-bg-2"
              >
                <div className="flex-1 min-w-0">
                  <p className="font-display text-[13px] text-bg-11 truncate leading-snug">
                    {preset.name}
                  </p>
                  <p className="font-display tabular-nums text-[11px] text-bg-8 mt-0.5">
                    {preset.offer_code ?? "—"} · {preset.byer_tag ?? "—"} · {preset.act_id}
                  </p>
                </div>
                <Badge variant="done">пресет</Badge>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Клон из истории */}
      <button
        type="button"
        onClick={handleClone}
        className={cn(
          "w-full text-left min-h-[56px] px-4 py-3.5",
          "border border-[var(--hairline)] bg-bg-1 rounded-[var(--radius-3)]",
          "flex items-center gap-3 active:bg-bg-2",
        )}
      >
        <Copy size={18} strokeWidth={1.6} className="text-bg-9 shrink-0" aria-hidden />
        <div>
          <p className="font-display text-[13px] text-bg-11 leading-snug">Клон из истории</p>
          <p className="text-[12px] text-bg-8 mt-0.5">Открыть список запусков</p>
        </div>
      </button>
    </div>
  );
}
