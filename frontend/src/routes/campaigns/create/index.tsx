/**
 * Страница «Создание кампаний» — оркестратор 7-шагового визарда.
 *
 * Две вкладки:
 *   - Создать (визард)
 *   - История запусков
 *
 * Визард: 7 шагов, состояние в Zustand (useWizardStore).
 * Переход: Back / Next / кнопки шагов (stepper).
 */

import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { Plus, Clock } from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/Button";
import { toast } from "@/components/ui/Toast";
import { useWizardStore } from "@/stores/campaignWizard";
import { usePresets } from "@/lib/api/campaigns";

import { WizardStep1Start } from "@/components/domain/campaigns/WizardStep1Start";
import { WizardStep2Identity, validateIdentity } from "@/components/domain/campaigns/WizardStep2Identity";
import { WizardStep3Goal, validateGoal } from "@/components/domain/campaigns/WizardStep3Goal";
import { WizardStep4Structure, validateStructure } from "@/components/domain/campaigns/WizardStep4Structure";
import { WizardStep5Creatives, validateCreatives } from "@/components/domain/campaigns/WizardStep5Creatives";
import { WizardStep6Preview } from "@/components/domain/campaigns/WizardStep6Preview";
import { WizardStep7Launch } from "@/components/domain/campaigns/WizardStep7Launch";
import { CampaignRunsHistory } from "@/components/domain/campaigns/CampaignRunsHistory";

export const Route = createFileRoute("/campaigns/create/")({
  component: CampaignCreatePage,
});

// ─── Вкладки ─────────────────────────────────────────────────────────────────

type PageTab = "wizard" | "history";

const TAB_LABELS: Record<PageTab, { label: string; icon: typeof Plus }> = {
  wizard: { label: "Создать", icon: Plus },
  history: { label: "История запусков", icon: Clock },
};

// ─── Шаги визарда ────────────────────────────────────────────────────────────

const STEP_LABELS = [
  "Старт",
  "Идентичность",
  "Параметры",
  "Структура",
  "Концепты",
  "Превью",
  "Запуск",
];

// ─── Компонент ────────────────────────────────────────────────────────────────

function CampaignCreatePage() {
  const [activeTab, setActiveTab] = useState<PageTab>("wizard");

  // При «клон» из истории — переключаем на визард с clone_run_id
  const handleCloneFromHistory = (runId: string) => {
    store.reset();
    store.setStart({ mode: "clone", clone_run_id: runId });
    store.goTo(1);
    setActiveTab("wizard");
  };

  const store = useWizardStore();

  return (
    <>
      <PageHeader
        eyebrowNum="05"
        eyebrow="OPERATE · СОЗДАНИЕ КАМПАНИЙ"
        title="Создание кампаний"
        action={
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              store.reset();
              setActiveTab("wizard");
            }}
          >
            Сбросить
          </Button>
        }
      />

      {/* Вкладки */}
      <div className="flex items-center gap-0.5 mb-6 border-b border-[var(--hairline)]">
        {(Object.entries(TAB_LABELS) as [PageTab, (typeof TAB_LABELS)[PageTab]][]).map(
          ([tab, { label, icon: Icon }]) => (
            <button
              key={tab}
              type="button"
              onClick={() => setActiveTab(tab)}
              className={cn(
                "flex items-center gap-2 px-4 py-2.5 font-display text-[12px] tracking-wider uppercase transition-colors border-b-2 -mb-px",
                activeTab === tab
                  ? "text-bg-11 border-accent"
                  : "text-bg-8 border-transparent hover:text-bg-11 hover:border-[var(--hairline-strong)]",
              )}
            >
              <Icon size={13} />
              {label}
            </button>
          ),
        )}
      </div>

      {activeTab === "history" ? (
        <CampaignRunsHistory onClone={handleCloneFromHistory} />
      ) : (
        <WizardLayout />
      )}
    </>
  );
}

// ─── WizardLayout ─────────────────────────────────────────────────────────────

function WizardLayout() {
  const store = useWizardStore();
  const { data: presets } = usePresets();

  const [errors, setErrors] = useState<Record<string, unknown>>({});

  const currentStep = store.currentStep;

  // Применить пресет при смене start.mode = preset + preset_id
  const handleStartChange = (v: Parameters<typeof store.setStart>[0]) => {
    store.setStart(v);
    if (v.mode === "preset" && v.preset_id) {
      const preset = presets?.find((p) => p.id === v.preset_id);
      if (preset) {
        store.applyPreset(preset);
        toast.success(`Пресет «${preset.name}» применён`);
      }
    }
  };

  // Валидация перед переходом вперёд
  const validateAndNext = () => {
    setErrors({});
    let err: Record<string, string> | string | null = null;

    if (currentStep === 2) {
      const e = validateIdentity(store.identity);
      if (Object.keys(e).length > 0) {
        setErrors(e);
        return;
      }
    }

    if (currentStep === 3) {
      const e = validateGoal(store.goal);
      if (Object.keys(e).length > 0) {
        setErrors(e);
        return;
      }
    }

    if (currentStep === 4) {
      err = validateStructure(store.structure.campaigns);
      if (err) {
        setErrors({ structure: err });
        return;
      }
    }

    if (currentStep === 5) {
      err = validateCreatives(store.creatives);
      if (err) {
        setErrors({ creatives: err });
        return;
      }
    }

    store.goNext();
  };

  const config = currentStep >= 6 ? store.buildConfig() : null;

  return (
    <div className="flex gap-6">
      {/* Stepper — левая колонка */}
      <aside className="shrink-0 w-40">
        <div className="sticky top-6 space-y-0.5">
          {STEP_LABELS.map((label, i) => {
            const step = (i + 1) as (typeof currentStep);
            const done = currentStep > step;
            const active = currentStep === step;
            return (
              <button
                key={step}
                type="button"
                onClick={() => {
                  // Назад — всегда; вперёд — только если уже было пройдено
                  if (step <= currentStep) store.goTo(step);
                }}
                disabled={step > currentStep}
                className={cn(
                  "w-full flex items-center gap-2.5 px-3 py-2 rounded-[var(--radius-2)] text-left transition-colors",
                  "disabled:cursor-not-allowed disabled:opacity-40",
                  active
                    ? "bg-accent-bg text-accent"
                    : done
                      ? "text-bg-10 hover:bg-bg-2 hover:text-bg-11"
                      : "text-bg-7",
                )}
              >
                <span
                  className={cn(
                    "size-5 shrink-0 rounded-full flex items-center justify-center text-[11px] font-display font-medium border",
                    active
                      ? "bg-accent border-accent text-bg-0"
                      : done
                        ? "bg-success/20 border-success/40 text-success"
                        : "bg-bg-2 border-[var(--hairline)] text-bg-7",
                  )}
                >
                  {done ? "✓" : step}
                </span>
                <span className="font-display text-[11px] tracking-wider">{label}</span>
              </button>
            );
          })}
        </div>
      </aside>

      {/* Основной контент */}
      <main className="flex-1 min-w-0">
        <div className="max-w-2xl">
          {/* Шаги */}
          {currentStep === 1 && (
            <WizardStep1Start
              mode={store.start.mode}
              presetId={store.start.preset_id}
              cloneRunId={store.start.clone_run_id}
              onChange={handleStartChange}
            />
          )}

          {currentStep === 2 && (
            <WizardStep2Identity
              values={store.identity}
              onChange={store.setIdentity}
              onGoalChange={store.setGoal}
              errors={errors as Record<string, string>}
            />
          )}

          {currentStep === 3 && (
            <WizardStep3Goal
              values={store.goal}
              onChange={store.setGoal}
              errors={errors as Record<string, string>}
            />
          )}

          {currentStep === 4 && (
            <WizardStep4Structure
              campaigns={store.structure.campaigns}
              onChange={(campaigns) => store.setStructure({ campaigns })}
              errors={errors.structure as string | undefined}
            />
          )}

          {currentStep === 5 && (
            <WizardStep5Creatives
              values={store.creatives}
              campaigns={store.structure.campaigns}
              onChange={store.setCreatives}
              errors={errors.creatives as string | undefined}
            />
          )}

          {currentStep === 6 && config && (
            <WizardStep6Preview
              config={config}
              preview={store.preview}
              onChange={store.setPreview}
            />
          )}

          {currentStep === 7 && config && (
            <WizardStep7Launch
              config={config}
              presetId={store.start.preset_id}
              runId={store.runId}
              onRunId={(id) => store.setRunId(id)}
            />
          )}

          {/* Навигация */}
          <div className="flex items-center justify-between mt-8 pt-5 border-t border-[var(--hairline)]">
            <Button
              variant="secondary"
              onClick={store.goPrev}
              disabled={currentStep === 1}
            >
              ← Назад
            </Button>

            {currentStep < 7 && (
              <Button
                variant="primary"
                onClick={validateAndNext}
              >
                Далее →
              </Button>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
