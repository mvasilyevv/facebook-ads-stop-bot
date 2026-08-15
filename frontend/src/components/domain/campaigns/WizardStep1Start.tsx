/**
 * Шаг 1 — старт визарда: новый залив / из пресета.
 *
 * - Две карточки-опции (radio-like)
 * - При выборе "из пресета" — выпадающий список пресетов
 */

import { campaignPresetsDataState } from "@fb/features/campaigns";
import { Link } from "@tanstack/react-router";
import { type FC } from "react";
import { Layers, Plus, RefreshCw, Settings2 } from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { usePresets } from "@/lib/api/campaigns";
import type { StartMode } from "@/stores/campaignWizard";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";

interface WizardStep1StartProps {
  mode: StartMode;
  presetId?: string | null;
  onChange: (v: { mode: StartMode; preset_id?: string | null }) => void;
}

const OPTIONS: { mode: StartMode; icon: FC<{ size: number }>; label: string; desc: string }[] = [
  {
    mode: "new",
    icon: Plus,
    label: "Новый залив",
    desc: "Начать с чистого листа — задать все параметры вручную.",
  },
  {
    mode: "preset",
    icon: Layers,
    label: "Из пресета",
    desc: "Подставить аудиторию, бюджет, нейминг и URL tags — затем при необходимости изменить.",
  },
];

export const WizardStep1Start: FC<WizardStep1StartProps> = ({ mode, presetId, onChange }) => {
  const presetsQuery = usePresets();
  const presets = presetsQuery.data ?? [];
  const dataState = campaignPresetsDataState({
    isPending: presetsQuery.isPending,
    isError: presetsQuery.isError,
    count: presets.length,
  });
  const selectedPreset = presets.find((preset) => preset.id === presetId) ?? null;

  const presetOptions = presets.map((preset) => ({
    value: preset.id,
    label: preset.daily_budget ? preset.name : `${preset.name} · требует заполнения`,
  }));

  return (
    <div className="space-y-6">
      {/* Заголовок шага */}
      <div>
        <div className="font-display text-[12px] tracking-[0.14em] uppercase text-bg-8 mb-1">
          ШАГ 1 · СТАРТ
        </div>
        <h2 className="font-display text-[20px] font-medium text-bg-11 leading-tight m-0">
          Как начать?
        </h2>
        <p className="text-[13px] text-bg-9 mt-1">
          Начните с чистой конфигурации или загрузите проверенный пресет.
        </p>
      </div>

      {/* Две карточки-опции */}
      <div className="grid gap-3 sm:grid-cols-2">
        {OPTIONS.map(({ mode: m, icon: Icon, label, desc }) => {
          const isActive = mode === m;
          return (
            <button
              key={m}
              type="button"
              onClick={() => onChange({ mode: m, preset_id: null })}
              className={cn(
                "text-left p-4 rounded-[var(--radius-3)] border transition-all duration-[120ms]",
                "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
                isActive
                  ? "bg-accent-bg border-accent"
                  : "bg-bg-2 border-[var(--color-hairline)] hover:border-[var(--color-hairline-strong)] hover:bg-bg-3",
              )}
              aria-pressed={isActive}
            >
              <div
                className={cn(
                  "size-8 rounded-[var(--radius-2)] flex items-center justify-center mb-3",
                  isActive ? "bg-accent text-bg-0" : "bg-bg-3 text-bg-9",
                )}
              >
                <Icon size={16} />
              </div>
              <div
                className={cn(
                  "font-display text-[13px] font-medium mb-1",
                  isActive ? "text-accent" : "text-bg-11",
                )}
              >
                {label}
              </div>
              <div className="text-[12px] text-bg-8 leading-snug">{desc}</div>
            </button>
          );
        })}
      </div>

      {/* Дополнительный выбор при preset */}
      {mode === "preset" && (
        <div className="space-y-3" data-state={dataState}>
          <div className="flex min-h-11 items-center justify-between gap-3">
            <div className="text-[12px] font-display tracking-wider uppercase text-bg-8">
              Выберите пресет
            </div>
            <Link
              to="/campaigns/presets"
              className="inline-flex min-h-11 items-center gap-2 rounded-[var(--radius-2)] px-3 text-[13px] text-bg-10 hover:bg-bg-2 hover:text-bg-11 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              <Settings2 size={15} aria-hidden="true" />
              Управление
            </Link>
          </div>
          {dataState === "stale" ? (
            <Skeleton className="h-11 w-full" />
          ) : dataState === "unavailable" ? (
            <div
              role="alert"
              className="flex items-center justify-between gap-3 border-y border-danger/35 py-3 text-[13px] text-danger"
            >
              <span>Пресеты сейчас недоступны. Новый залив можно заполнить вручную.</span>
              <button
                type="button"
                aria-label="Повторить загрузку пресетов"
                onClick={() => void presetsQuery.refetch()}
                className="flex size-11 shrink-0 items-center justify-center rounded-[var(--radius-2)] border border-danger/40"
              >
                <RefreshCw size={15} aria-hidden="true" />
              </button>
            </div>
          ) : dataState === "empty" ? (
            <div role="status" className="border-y border-[var(--color-hairline)] py-4">
              <p className="text-[13px] text-bg-10">Сохранённых пресетов пока нет.</p>
              <p className="mt-1 text-[12px] text-bg-8">
                Создайте первый шаблон с гео, бюджетом и неймингом.
              </p>
            </div>
          ) : (
            <Select
              options={presetOptions}
              placeholder="— Выберите пресет —"
              value={presetId ?? ""}
              onChange={(e) => onChange({ mode: "preset", preset_id: e.target.value || null })}
            />
          )}
          {selectedPreset ? (
            <div className="border-l-2 border-accent pl-4" role="status">
              <p className="font-display text-[13px] font-medium text-bg-11">
                Подставлено из «{selectedPreset.name}»
              </p>
              <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2 text-[12px] sm:grid-cols-3">
                <PresetFact label="Гео" value={selectedPreset.countries.join(" · ")} />
                <PresetFact
                  label="Возраст"
                  value={`${selectedPreset.age_min}–${selectedPreset.age_max}`}
                />
                <PresetFact
                  label="Бюджет"
                  value={
                    selectedPreset.daily_budget
                      ? `$${selectedPreset.daily_budget} · ${selectedPreset.budget_level === "campaign" ? "CBO" : "ABO"}`
                      : "Нужно заполнить"
                  }
                />
                <PresetFact
                  label="Пол"
                  value={selectedPreset.genders.length ? selectedPreset.genders.join(" · ") : "Все"}
                />
                <PresetFact
                  label="Плейсменты"
                  value={
                    selectedPreset.placements.length
                      ? selectedPreset.placements.join(" · ")
                      : "Авто"
                  }
                />
                <PresetFact label="Событие" value="Purchase · зафиксировано" />
              </dl>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
};

function PresetFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-bg-8">{label}</dt>
      <dd className="mt-0.5 break-words text-bg-10">{value || "—"}</dd>
    </div>
  );
}
