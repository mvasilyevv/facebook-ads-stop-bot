/**
 * StatsPeriodTabs — «Сегодня | 7д | 30д | 90д | Период…» для страницы /stats.
 *
 * Отличие от чистого History PeriodSelector: первая вкладка «Сегодня» —
 * отдельный режим (GET /stats/today, без from_iso/to_iso), остальные —
 * пресеты периода (GET /stats/period). Custom-диапазон переиспользует
 * PeriodSelector (те же date-input + 90-дневный кап).
 */

import { useState, type FC } from "react";
import { Button } from "@/components/ui/Button";
import { PeriodSelector, type Period } from "@/components/history/PeriodSelector";
import { cn } from "@/lib/utils/cn";

export type StatsMode =
  | { kind: "today" }
  | { kind: "period"; period: Period };

const PRESETS = [7, 30, 90] as const;
type PresetDays = (typeof PRESETS)[number];

function buildPreset(days: PresetDays): Period {
  const to = new Date();
  const from = new Date(to.getTime() - days * 86400 * 1000);
  return { from_iso: from.toISOString(), to_iso: to.toISOString() };
}

interface StatsPeriodTabsProps {
  value: StatsMode;
  onChange: (mode: StatsMode) => void;
  className?: string;
}

export const StatsPeriodTabs: FC<StatsPeriodTabsProps> = ({ value, onChange, className }) => {
  const [customOpen, setCustomOpen] = useState(false);

  const activePreset =
    value.kind === "period"
      ? PRESETS.find((d) => {
          const p = buildPreset(d);
          return (
            p.from_iso.slice(0, 10) === value.period.from_iso.slice(0, 10) &&
            p.to_iso.slice(0, 10) === value.period.to_iso.slice(0, 10)
          );
        })
      : undefined;

  const isCustom = value.kind === "period" && activePreset === undefined;

  return (
    <div className={cn("flex flex-wrap items-center gap-3", className)} role="group" aria-label="Период статистики">
      <div className="flex gap-1">
        <Button
          size="sm"
          variant={value.kind === "today" ? "primary" : "secondary"}
          aria-pressed={value.kind === "today"}
          onClick={() => {
            setCustomOpen(false);
            onChange({ kind: "today" });
          }}
        >
          Сегодня
        </Button>
        {PRESETS.map((d) => (
          <Button
            key={d}
            size="sm"
            variant={activePreset === d ? "primary" : "secondary"}
            aria-pressed={activePreset === d}
            onClick={() => {
              setCustomOpen(false);
              onChange({ kind: "period", period: buildPreset(d) });
            }}
          >
            {d === 7 ? "7д" : d === 30 ? "30д" : "90д"}
          </Button>
        ))}
        <Button
          size="sm"
          variant={isCustom || customOpen ? "primary" : "secondary"}
          aria-pressed={isCustom}
          onClick={() => setCustomOpen((v) => !v)}
        >
          Период…
        </Button>
      </div>

      {customOpen ? (
        <PeriodSelector
          value={value.kind === "period" ? value.period : buildPreset(30)}
          onChange={(p) => onChange({ kind: "period", period: p })}
        />
      ) : null}
    </div>
  );
};
