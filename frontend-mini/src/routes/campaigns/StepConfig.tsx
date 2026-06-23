/**
 * StepConfig — шаг 3 визарда: бюджет / таргет / назначение / дата.
 * Основные run-параметры кампании. Продвинутые настройки — беклог.
 */
import { useState } from "react";
import { Input, Button, Select } from "@/components/ui";
import { Eyebrow } from "@/components/data";
import { haptic } from "@/lib/tg";
import { useWizardStore } from "./-wizardStore";

/** Дата today+1 в формате YYYY-MM-DD. */
function defaultStartDate(): string {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  return d.toISOString().slice(0, 10);
}

const LAUNCH_STATE_OPTIONS = [
  { value: "campaign_paused", label: "Кампания на паузе (дети активны)" },
  { value: "all_paused",      label: "Всё на паузе" },
];

const BUDGET_LEVEL_OPTIONS = [
  { value: "campaign", label: "CBO (уровень кампании)" },
  { value: "adset",    label: "ABO (уровень адсета)" },
];

export function StepConfig() {
  const { config, updateConfig, nextStep, prevStep } = useWizardStore();

  const [destinationLink, setDestinationLink] = useState(config.destination_link ?? "");
  const [dailyCents, setDailyCents] = useState(
    config.daily_budget_cents ? String(config.daily_budget_cents / 100) : "",
  );
  const [budgetLevel, setBudgetLevel] = useState<"campaign" | "adset">(
    config.budget_level ?? "campaign",
  );
  const [countries, setCountries] = useState<string>(
    (config.countries ?? []).join(", "),
  );
  const [startDate, setStartDate] = useState(config.start_date ?? defaultStartDate());
  const [launchState, setLaunchState] = useState<"campaign_paused" | "all_paused">(
    config.launch_state ?? "campaign_paused",
  );
  const [error, setError] = useState<string | null>(null);

  function parseCountries(raw: string): string[] {
    return raw.split(/[,\s]+/).map((s) => s.trim().toUpperCase()).filter(Boolean);
  }

  function handleNext() {
    setError(null);
    if (!destinationLink.trim()) {
      setError("Укажите ссылку назначения (трекинг-URL)");
      return;
    }
    const dailyCentsNum = dailyCents.trim() ? Math.round(parseFloat(dailyCents) * 100) : null;
    if (dailyCentsNum !== null && isNaN(dailyCentsNum)) {
      setError("Некорректный бюджет");
      return;
    }
    if (dailyCentsNum !== null && dailyCentsNum > 10_000_000) {
      setError("Дневной бюджет превышает $100 000 — проверьте");
      return;
    }
    haptic.impact("light");
    updateConfig({
      destination_link: destinationLink.trim(),
      daily_budget_cents: dailyCentsNum,
      budget_level: budgetLevel,
      countries: parseCountries(countries),
      start_date: startDate || defaultStartDate(),
      launch_state: launchState,
    });
    nextStep();
  }

  return (
    <div className="flex flex-col gap-5 p-4 pb-8">
      <Eyebrow num="03">ПАРАМЕТРЫ ЗАЛИВА</Eyebrow>

      <div className="flex flex-col gap-4">
        <Input
          label="Ссылка назначения (трекинг-URL)"
          placeholder="https://example.com/lp?s1=..."
          value={destinationLink}
          onChange={(e) => setDestinationLink(e.target.value)}
          type="url"
          autoCapitalize="none"
          autoCorrect="off"
        />

        <Input
          label="Дата старта (YYYY-MM-DD)"
          placeholder={defaultStartDate()}
          value={startDate}
          onChange={(e) => setStartDate(e.target.value)}
          type="date"
        />

        <Select
          label="Уровень бюджета"
          value={budgetLevel}
          options={BUDGET_LEVEL_OPTIONS}
          onChange={(e) => setBudgetLevel(e.target.value as "campaign" | "adset")}
        />

        <Input
          label="Дневной бюджет ($)"
          placeholder="50.00"
          value={dailyCents}
          onChange={(e) => setDailyCents(e.target.value)}
          inputMode="decimal"
          type="text"
        />

        <Input
          label="Страны (через запятую)"
          placeholder="GH, NG, KE +AQ авто"
          value={countries}
          onChange={(e) => setCountries(e.target.value)}
          autoCapitalize="characters"
        />

        <Select
          label="Статус запуска"
          value={launchState}
          options={LAUNCH_STATE_OPTIONS}
          onChange={(e) => setLaunchState(e.target.value as "campaign_paused" | "all_paused")}
        />
      </div>

      {/* Подсказка про launch_state */}
      <div className="border border-[var(--hairline)] bg-bg-1 p-3 rounded-[var(--radius-2)]">
        <p className="text-[11px] text-bg-8 leading-relaxed">
          <strong className="text-bg-10">campaign_paused</strong> — рекомендуется.
          Кампания создаётся на паузе, но дети активны: модерация проходит, старт — одним тумблером
          в Ads Manager. Спенда нет.
        </p>
      </div>

      {error !== null && (
        <p className="text-[12px] text-[var(--color-danger)]">{error}</p>
      )}

      <div className="flex flex-col gap-3 mt-2">
        <Button fullWidth onClick={handleNext}>
          Далее
        </Button>
        <Button variant="ghost" fullWidth onClick={() => { haptic.selection(); prevStep(); }}>
          Назад
        </Button>
      </div>
    </div>
  );
}
