/**
 * CampaignsPage — экран «Создание кампаний» (визард 7 шагов) + история.
 * Декомпозиция: каждый шаг — отдельный компонент (<500 строк).
 * Нижний tab-bar скрыт на шагах 2–7 (только history показывает полный layout).
 */
import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { MiniHeader } from "@/components/layout/MiniHeader";
import { Tabs } from "@/components/ui";
import type { TabItem } from "@/components/ui/Tabs";
import { haptic } from "@/lib/tg";
import { useWizardStore } from "./-wizardStore";
import { WIZARD_STEPS, WIZARD_STEP_LABEL } from "@/lib/campaignTypes";
import { StepStart } from "./StepStart";
import { StepIdentity } from "./StepIdentity";
import { StepConfig } from "./StepConfig";
import { StepStructure } from "./StepStructure";
import { StepCreatives } from "./StepCreatives";
import { StepPreview } from "./StepPreview";
import { StepLaunch } from "./StepLaunch";
import { RunsHistory } from "./RunsHistory";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const Route = (createFileRoute as any)("/campaigns/")({
  component: CampaignsPage,
});

const PAGE_TABS: TabItem[] = [
  { key: "wizard", label: "Визард" },
  { key: "history", label: "История" },
];

/** Индикатор прогресса шагов. */
function WizardProgress() {
  const { step } = useWizardStore();
  const currentIdx = WIZARD_STEPS.indexOf(step);
  return (
    <div className="px-4 py-2 border-b border-[var(--hairline)] bg-bg-0">
      <div className="flex items-center gap-1 overflow-x-auto no-scrollbar pb-0.5">
        {WIZARD_STEPS.map((s, idx) => (
          <div key={s} className="flex items-center gap-1 shrink-0">
            <div
              className={
                idx < currentIdx
                  ? "w-2 h-2 rounded-full bg-[var(--color-success)]"
                  : idx === currentIdx
                    ? "w-2 h-2 rounded-full bg-accent"
                    : "w-2 h-2 rounded-full bg-bg-4"
              }
              aria-label={`Шаг ${idx + 1}: ${WIZARD_STEP_LABEL[s]}${idx === currentIdx ? " (текущий)" : ""}`}
            />
            {idx < WIZARD_STEPS.length - 1 && (
              <div className={`h-px w-3 ${idx < currentIdx ? "bg-[var(--color-success)]" : "bg-bg-4"}`} />
            )}
          </div>
        ))}
      </div>
      <p className="text-[10px] text-bg-8 mt-1">
        Шаг {WIZARD_STEPS.indexOf(step) + 1} из {WIZARD_STEPS.length} —{" "}
        <span className="text-bg-10">{WIZARD_STEP_LABEL[step]}</span>
      </p>
    </div>
  );
}

/** Роутинг между шагами визарда. */
function WizardStep({
  onCloneRun,
}: {
  onCloneRun: () => void;
}) {
  const { step } = useWizardStore();
  switch (step) {
    case "start":     return <StepStart onCloneRun={onCloneRun} />;
    case "identity":  return <StepIdentity />;
    case "config":    return <StepConfig />;
    case "structure": return <StepStructure />;
    case "creatives": return <StepCreatives />;
    case "preview":   return <StepPreview />;
    case "launch":    return <StepLaunch />;
    default:          return null;
  }
}

function CampaignsPage() {
  const [tab, setTab] = useState<"wizard" | "history">("wizard");
  const { step } = useWizardStore();

  function handleTabChange(v: string) {
    haptic.selection();
    setTab(v as "wizard" | "history");
  }

  function goToHistoryForClone() {
    setTab("history");
  }

  const isOnStart = step === "start";

  return (
    <div className="flex flex-col min-h-full pb-20">
      {/* Шапка */}
      <MiniHeader
        eyebrow="ИНСТРУМЕНТЫ"
        title="Кампании"
      />

      {/* Вкладки Визард / История */}
      <div className="px-4 pt-3 pb-0 border-b border-[var(--hairline)]">
        <Tabs
          active={tab}
          onChange={handleTabChange}
          items={PAGE_TABS}
        />
      </div>

      {/* Визард */}
      {tab === "wizard" && (
        <div className="flex flex-col flex-1">
          {/* Прогресс-индикатор (только не на стартовом шаге) */}
          {!isOnStart && <WizardProgress />}

          <WizardStep onCloneRun={goToHistoryForClone} />
        </div>
      )}

      {/* История */}
      {tab === "history" && (
        <RunsHistory />
      )}
    </div>
  );
}
