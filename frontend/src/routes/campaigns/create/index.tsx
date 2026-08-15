import { useState } from "react";
import { validateCampaignStep, type CampaignWizardStep } from "@fb/features/campaigns";
import { createFileRoute } from "@tanstack/react-router";
import { AlertTriangle, Clock, Plus, Save } from "lucide-react";

import { CampaignRunsHistory } from "@/components/domain/campaigns/CampaignRunsHistory";
import { WizardStep1Start } from "@/components/domain/campaigns/WizardStep1Start";
import { WizardStep2Identity } from "@/components/domain/campaigns/WizardStep2Identity";
import { WizardStep3Goal } from "@/components/domain/campaigns/WizardStep3Goal";
import { WizardStep4Structure } from "@/components/domain/campaigns/WizardStep4Structure";
import { WizardStep5Creatives } from "@/components/domain/campaigns/WizardStep5Creatives";
import { WizardStep6Preview } from "@/components/domain/campaigns/WizardStep6Preview";
import { WizardStep7Launch } from "@/components/domain/campaigns/WizardStep7Launch";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { ErrorState } from "@/components/ui/ErrorState";
import { Skeleton } from "@/components/ui/Skeleton";
import { toast } from "@/components/ui/Toast";
import { useCampaignDraftSync } from "@/features/campaigns/useCampaignDraftSync";
import { usePresets } from "@/lib/api/campaigns";
import { cn } from "@/lib/utils/cn";
import { getWizardFeatureState, useWizardStore } from "@/stores/campaignWizard";

export const Route = createFileRoute("/campaigns/create/")({
  component: CampaignCreatePage,
});

type PageTab = "wizard" | "history";

const TAB_LABELS: Record<PageTab, { label: string; icon: typeof Plus }> = {
  wizard: { label: "Создать", icon: Plus },
  history: { label: "История", icon: Clock },
};

const STEP_LABELS = [
  "Старт",
  "Идентичность",
  "Параметры",
  "Структура",
  "Концепты",
  "Превью",
  "Запуск",
] as const;

export function CampaignCreatePage() {
  const [activeTab, setActiveTab] = useState<PageTab>("wizard");
  const [resetOpen, setResetOpen] = useState(false);
  const store = useWizardStore();
  const draft = useCampaignDraftSync();
  const hasDraft = store.draftRevision > 0 || store.draftVersion > 0;

  if (draft.isHydrating) {
    return (
      <div aria-busy="true" aria-label="Восстановление черновика" className="space-y-4">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-72 w-full" />
      </div>
    );
  }

  if (draft.isHydrationError) {
    return (
      <ErrorState
        title="Черновик кампании недоступен"
        error="Создание не начнётся без подтверждённого серверного черновика."
        onRetry={() => void draft.reloadServerDraft()}
      />
    );
  }

  return (
    <>
      <PageHeader
        eyebrowNum="05"
        eyebrow="OPERATE · СОЗДАНИЕ КАМПАНИЙ"
        title="Создание кампаний"
        action={
          activeTab === "wizard" ? (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setResetOpen(true)}
              disabled={!hasDraft || draft.deletePending}
            >
              Сбросить
            </Button>
          ) : null
        }
      />

      <div
        className="mb-4 flex border-b border-[var(--color-hairline)]"
        role="tablist"
        aria-label="Раздел кампаний"
      >
        {(Object.entries(TAB_LABELS) as [PageTab, (typeof TAB_LABELS)[PageTab]][]).map(
          ([tab, { label, icon: Icon }]) => (
            <button
              key={tab}
              type="button"
              role="tab"
              aria-selected={activeTab === tab}
              onClick={() => setActiveTab(tab)}
              className={cn(
                "-mb-px flex min-h-11 flex-1 items-center justify-center gap-2 border-b-2 px-3 font-display text-[12px] uppercase tracking-wider transition-colors sm:flex-none sm:px-4",
                activeTab === tab
                  ? "border-accent text-bg-11"
                  : "border-transparent text-bg-8 hover:border-[var(--color-hairline-strong)] hover:text-bg-11",
              )}
            >
              <Icon size={14} aria-hidden="true" />
              {label}
            </button>
          ),
        )}
      </div>

      {activeTab === "wizard" ? (
        <>
          <DraftStatus
            state={store.draftSyncState}
            updatedAt={store.draftUpdatedAt}
            onReload={() => void draft.reloadServerDraft()}
          />
          <WizardLayout />
        </>
      ) : (
        <CampaignRunsHistory />
      )}

      <ConfirmDialog
        open={resetOpen}
        onOpenChange={setResetOpen}
        title="Сбросить серверный черновик?"
        description="Заполненные шаги и привязки файлов будут удалены. Уже поставленные в очередь запуски не изменятся."
        confirmLabel="Сбросить черновик"
        confirmVariant="danger"
        onConfirm={() => {
          void draft
            .deleteServerDraft()
            .then(() => {
              setActiveTab("wizard");
              toast.success("Черновик сброшен");
            })
            .catch(() => toast.error("Не удалось сбросить черновик. Обновите данные и повторите."));
        }}
      />
    </>
  );
}

function DraftStatus({
  state,
  updatedAt,
  onReload,
}: {
  state: ReturnType<typeof useWizardStore.getState>["draftSyncState"];
  updatedAt: string | null;
  onReload: () => void;
}) {
  if (state === "conflict") {
    return (
      <div
        role="alert"
        className="mb-5 flex flex-col gap-3 rounded-[var(--radius-2)] border border-warning/40 bg-warning/10 p-3 text-[13px] text-bg-10 sm:flex-row sm:items-center"
      >
        <span className="flex min-w-0 items-start gap-2">
          <AlertTriangle size={16} className="mt-0.5 shrink-0 text-warning" aria-hidden="true" />
          Черновик изменён в другой вкладке. Загрузите серверную версию, чтобы не затереть
          изменения.
        </span>
        <Button variant="secondary" size="sm" onClick={onReload} className="min-h-11 sm:ml-auto">
          Загрузить серверный
        </Button>
      </div>
    );
  }

  const label =
    state === "saving"
      ? "Сохраняем черновик…"
      : state === "error"
        ? "Черновик не сохранён — повторим после следующего изменения"
        : updatedAt
          ? `Сохранён на сервере ${formatDraftTime(updatedAt)}`
          : "Черновик сохранится на сервере после первого изменения";

  return (
    <div
      className={cn(
        "mb-5 flex min-h-6 items-center gap-2 font-display text-[12px]",
        state === "error" ? "text-warning" : "text-bg-9",
      )}
      role={state === "error" ? "alert" : "status"}
    >
      <Save size={14} aria-hidden="true" />
      {label}
    </div>
  );
}

function formatDraftTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "недавно";
  return date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

function WizardLayout() {
  const store = useWizardStore();
  const { data: presets, isError, refetch } = usePresets();
  const [errors, setErrors] = useState<Record<string, string>>({});
  const selectedPreset = presets?.find((preset) => preset.id === store.start.preset_id) ?? null;

  const handleStartChange = (value: Parameters<typeof store.setStart>[0]) => {
    store.setStart(value);
    if (value.mode === "preset" && value.preset_id) {
      const preset = presets?.find((candidate) => candidate.id === value.preset_id);
      if (preset) {
        store.applyPreset(preset);
        toast.success(`Пресет «${preset.name}» применён`);
      }
    }
  };

  const validateAndNext = () => {
    const nextErrors = validateCampaignStep(getWizardFeatureState(), store.currentStep);
    if (store.currentStep === 6 && !store.preview.plan) {
      nextErrors.preview = "Дождитесь подтверждённого сервером плана";
    }
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length === 0) store.goNext();
  };

  let config: ReturnType<typeof store.buildConfig> | null = null;
  let configError: string | null = null;
  if (store.currentStep >= 6) {
    try {
      config = store.buildConfig(selectedPreset);
    } catch {
      configError = "Конфигурация неполна. Вернитесь к отмеченному шагу и проверьте данные.";
    }
  }

  if (isError) {
    return (
      <ErrorState
        title="Пресеты кампаний недоступны"
        error="Обновите данные и повторите. Если проблема сохранится, откройте диагностику API."
        onRetry={() => void refetch()}
      />
    );
  }

  return (
    <div className="flex min-w-0 flex-col gap-6 lg:flex-row">
      <aside className="w-full shrink-0 lg:w-44" aria-label="Шаги создания кампании">
        <ol className="flex gap-1 overflow-x-auto pb-2 lg:sticky lg:top-6 lg:block lg:space-y-0.5 lg:overflow-visible lg:pb-0">
          {STEP_LABELS.map((label, index) => {
            const step = (index + 1) as CampaignWizardStep;
            const done = store.currentStep > step;
            const active = store.currentStep === step;
            return (
              <li key={step} className="shrink-0 lg:w-full">
                <button
                  type="button"
                  onClick={() => step <= store.currentStep && store.goTo(step)}
                  disabled={step > store.currentStep}
                  aria-current={active ? "step" : undefined}
                  className={cn(
                    "flex min-h-11 w-full min-w-max items-center gap-2.5 rounded-[var(--radius-2)] px-3 py-2 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-40",
                    active
                      ? "bg-accent-bg text-accent"
                      : done
                        ? "text-bg-10 hover:bg-bg-2 hover:text-bg-11"
                        : "text-bg-8",
                  )}
                >
                  <span
                    className={cn(
                      "flex size-5 shrink-0 items-center justify-center rounded-full border text-[12px] font-medium",
                      active
                        ? "border-accent bg-accent text-bg-0"
                        : done
                          ? "border-success/40 bg-success/20 text-success"
                          : "border-[var(--color-hairline)] bg-bg-2 text-bg-8",
                    )}
                    aria-hidden="true"
                  >
                    {done ? "✓" : step}
                  </span>
                  <span className="font-display text-[12px] tracking-wider">{label}</span>
                </button>
              </li>
            );
          })}
        </ol>
      </aside>

      <section className="min-w-0 flex-1" aria-label="Текущий шаг создания кампании">
        <div className="max-w-2xl pb-24 md:pb-0">
          {store.currentStep === 1 ? (
            <WizardStep1Start
              mode={store.start.mode}
              presetId={store.start.preset_id}
              onChange={handleStartChange}
            />
          ) : null}
          {store.currentStep === 2 ? (
            <WizardStep2Identity
              values={store.identity}
              onChange={store.setIdentity}
              onGoalChange={store.setGoal}
              errors={errors}
            />
          ) : null}
          {store.currentStep === 3 ? (
            <WizardStep3Goal
              values={store.goal}
              onChange={store.setGoal}
              currency={store.identity.currency || null}
              currencyExponent={store.identity.currency_exponent}
              errors={errors}
            />
          ) : null}
          {store.currentStep === 4 ? (
            <WizardStep4Structure
              campaigns={store.structure.campaigns}
              onChange={(campaigns) => store.setStructure({ campaigns })}
              errors={errors.structure}
            />
          ) : null}
          {store.currentStep === 5 ? (
            <WizardStep5Creatives
              values={store.creatives}
              campaigns={store.structure.campaigns}
              onChange={store.setCreatives}
              errors={errors.creatives}
            />
          ) : null}
          {store.currentStep === 6 && config ? (
            <WizardStep6Preview
              config={config}
              preview={store.preview}
              onChange={store.setPreview}
            />
          ) : null}
          {store.currentStep === 7 && config ? (
            <WizardStep7Launch
              config={config}
              presetId={store.start.preset_id}
              draftRevision={store.draftRevision || null}
              draftSyncState={store.draftSyncState}
              accountIds={store.identity.ad_account_ids ?? []}
              launchReceipt={store.launchReceipt}
              onLaunchReceipt={store.setLaunchReceipt}
              onDraftCleared={store.markDraftCleared}
              onFinish={store.reset}
            />
          ) : null}

          {configError ? (
            <div
              role="alert"
              className="rounded-[var(--radius-2)] border border-warning/40 bg-warning/10 p-4 text-[13px] text-bg-10"
            >
              {configError}
            </div>
          ) : null}
          {errors.preview ? (
            <p role="alert" className="mt-4 text-[13px] text-warning">
              {errors.preview}
            </p>
          ) : null}

          <div className="sticky bottom-[calc(4rem+env(safe-area-inset-bottom))] z-20 -mx-3 mt-8 flex items-center justify-between border-t border-[var(--color-hairline)] bg-bg-0/95 px-3 py-3 backdrop-blur md:static md:mx-0 md:bg-transparent md:px-0 md:py-5 md:backdrop-blur-none">
            <Button variant="secondary" onClick={store.goPrev} disabled={store.currentStep === 1}>
              ← Назад
            </Button>
            {store.currentStep < 7 ? (
              <Button variant="primary" onClick={validateAndNext}>
                {store.currentStep === 6 ? "Подтвердить план" : "Далее →"}
              </Button>
            ) : null}
          </div>
        </div>
      </section>
    </div>
  );
}
