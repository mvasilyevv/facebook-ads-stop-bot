/**
 * PeriodSelector — выбор временного периода для History-страницы.
 * Пресеты: 7 / 30 / 90 дней. Custom-диапазон через два date-input.
 * Максимум 90 дней (иначе бэк 422). Нарушение подсвечивается.
 */

import { useState, useCallback, type FC } from "react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { cn } from "@/lib/utils/cn";

/** Период в ISO-строках UTC. */
export interface Period {
  from_iso: string;
  to_iso: string;
}

const PRESETS = [7, 30, 90] as const;
type PresetDays = (typeof PRESETS)[number];

/** Строим период «последние N дней» относительно текущего UTC-момента. */
function buildPreset(days: PresetDays): Period {
  const to = new Date();
  const from = new Date(to.getTime() - days * 86400 * 1000);
  return {
    from_iso: from.toISOString(),
    to_iso: to.toISOString(),
  };
}

/** Локальная дата "YYYY-MM-DD" → UTC ISO начало/конец дня. */
function dateToFromIso(dateStr: string): string {
  return new Date(`${dateStr}T00:00:00Z`).toISOString();
}
function dateToToIso(dateStr: string): string {
  return new Date(`${dateStr}T23:59:59Z`).toISOString();
}

/** Разность дней между двумя ISO-строками. */
function diffDays(from: string, to: string): number {
  return Math.round((new Date(to).getTime() - new Date(from).getTime()) / 86400_000);
}

/** ISO → "YYYY-MM-DD" для <input type="date">. */
function isoToDate(iso: string): string {
  return iso.slice(0, 10);
}

interface PeriodSelectorProps {
  value: Period;
  onChange: (p: Period) => void;
  className?: string;
}

export const PeriodSelector: FC<PeriodSelectorProps> = ({ value, onChange, className }) => {
  // Определяем активный пресет (или custom)
  const activePreset = PRESETS.find((d) => {
    const p = buildPreset(d);
    return (
      isoToDate(p.from_iso) === isoToDate(value.from_iso) &&
      isoToDate(p.to_iso) === isoToDate(value.to_iso)
    );
  });

  const [customFrom, setCustomFrom] = useState(isoToDate(value.from_iso));
  const [customTo, setCustomTo] = useState(isoToDate(value.to_iso));
  const [customError, setCustomError] = useState<string | null>(null);

  const handlePreset = useCallback(
    (days: PresetDays) => {
      setCustomError(null);
      const p = buildPreset(days);
      setCustomFrom(isoToDate(p.from_iso));
      setCustomTo(isoToDate(p.to_iso));
      onChange(p);
    },
    [onChange],
  );

  const applyCustom = useCallback(() => {
    if (!customFrom || !customTo) return;
    const from = dateToFromIso(customFrom);
    const to = dateToToIso(customTo);
    const days = diffDays(from, to);
    if (days < 0) {
      setCustomError("Дата «от» должна быть раньше «до»");
      return;
    }
    if (days > 90) {
      setCustomError("Максимальный диапазон — 90 дней");
      return;
    }
    setCustomError(null);
    onChange({ from_iso: from, to_iso: to });
  }, [customFrom, customTo, onChange]);

  return (
    <div className={cn("flex flex-wrap items-center gap-3", className)} role="group" aria-label="Период истории">
      {/* Пресеты */}
      <div className="flex gap-1">
        {PRESETS.map((d) => (
          <Button
            key={d}
            size="sm"
            variant={activePreset === d ? "primary" : "secondary"}
            onClick={() => handlePreset(d)}
            aria-pressed={activePreset === d}
          >
            {d === 7 ? "7 дн" : d === 30 ? "30 дн" : "90 дн"}
          </Button>
        ))}
      </div>

      {/* Разделитель */}
      <span className="text-bg-6 font-display text-[11px]" aria-hidden="true">|</span>

      {/* Custom-диапазон */}
      <div className="flex items-center gap-2">
        <Input
          type="date"
          size="sm"
          aria-label="С даты"
          value={customFrom}
          onChange={(e) => setCustomFrom(e.target.value)}
          max={customTo || undefined}
          className="w-[130px]"
        />
        <span className="text-bg-8 font-display text-[11px]" aria-hidden="true">—</span>
        <Input
          type="date"
          size="sm"
          aria-label="По дату"
          value={customTo}
          onChange={(e) => setCustomTo(e.target.value)}
          min={customFrom || undefined}
          className="w-[130px]"
        />
        <Button size="sm" variant="secondary" onClick={applyCustom}>
          Применить
        </Button>
      </div>

      {/* Ошибка custom-диапазона */}
      {customError ? (
        <span className="text-danger font-display text-[11px]" role="alert">
          {customError}
        </span>
      ) : null}
    </div>
  );
};
