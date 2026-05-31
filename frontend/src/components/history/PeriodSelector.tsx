/**
 * PeriodSelector — выбор периода для History-страницы.
 * Пресеты 7/30/90 дней + кастомный from/to.
 * Валидация: max 90 дней (бэк вернёт 422 на >90).
 */

import { useState, type ChangeEvent } from "react";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils/cn";

export type PresetKey = "7d" | "30d" | "90d" | "custom";

export interface DateRange {
  from_iso: string;
  to_iso: string;
}

interface PeriodSelectorProps {
  value: DateRange;
  onChange: (range: DateRange) => void;
  className?: string;
}

const PRESETS: Array<{ key: PresetKey; label: string; days: number }> = [
  { key: "7d", label: "7д", days: 7 },
  { key: "30d", label: "30д", days: 30 },
  { key: "90d", label: "90д", days: 90 },
];

/** Возвращает ISO-строку начала дня N дней назад (UTC). */
function daysAgoIso(days: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - days);
  d.setUTCHours(0, 0, 0, 0);
  return d.toISOString().slice(0, 10);
}

/** Сегодня как ISO YYYY-MM-DD (UTC). */
function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

/** Возвращает разницу в днях между двумя ISO-датами. */
function diffDays(from: string, to: string): number {
  const a = new Date(from).getTime();
  const b = new Date(to).getTime();
  return Math.round((b - a) / 86400000);
}

/** Определяет активный пресет по текущим датам. */
function detectPreset(range: DateRange): PresetKey {
  const days = diffDays(range.from_iso, range.to_iso);
  const today = todayIso();
  if (range.to_iso === today) {
    if (days === 7) return "7d";
    if (days === 30) return "30d";
    if (days === 90) return "90d";
  }
  return "custom";
}

export function PeriodSelector({ value, onChange, className }: PeriodSelectorProps) {
  const [showCustom, setShowCustom] = useState(detectPreset(value) === "custom");
  const [customFrom, setCustomFrom] = useState(value.from_iso);
  const [customTo, setCustomTo] = useState(value.to_iso);
  const [customError, setCustomError] = useState<string | null>(null);

  const activePreset = detectPreset(value);

  const applyPreset = (days: number) => {
    setShowCustom(false);
    setCustomError(null);
    onChange({ from_iso: daysAgoIso(days), to_iso: todayIso() });
  };

  const applyCustom = () => {
    if (!customFrom || !customTo) {
      setCustomError("Укажите обе даты");
      return;
    }
    const days = diffDays(customFrom, customTo);
    if (days > 90) {
      setCustomError("Максимальный период — 90 дней");
      return;
    }
    if (days < 0) {
      setCustomError("Дата начала должна быть раньше конца");
      return;
    }
    setCustomError(null);
    onChange({ from_iso: customFrom, to_iso: customTo });
  };

  const handleFromChange = (e: ChangeEvent<HTMLInputElement>) => {
    setCustomFrom(e.target.value);
    setCustomError(null);
  };

  const handleToChange = (e: ChangeEvent<HTMLInputElement>) => {
    setCustomTo(e.target.value);
    setCustomError(null);
  };

  return (
    <div className={cn("flex flex-wrap items-center gap-2", className)}>
      {/* Кнопки пресетов */}
      <div className="flex items-center gap-1 border border-bg-5 p-0.5">
        {PRESETS.map((p) => (
          <button
            key={p.key}
            type="button"
            title={`Последние ${p.days} дней`}
            aria-pressed={activePreset === p.key}
            onClick={() => applyPreset(p.days)}
            className={cn(
              "h-7 px-3 font-display text-[11px] uppercase tracking-wider transition-colors",
              activePreset === p.key
                ? "bg-bg-4 text-accent"
                : "text-bg-9 hover:text-bg-11 hover:bg-bg-2",
            )}
          >
            {p.label}
          </button>
        ))}
      </div>

      {/* Кнопка «Произвольный период» */}
      <button
        type="button"
        onClick={() => setShowCustom((v) => !v)}
        className={cn(
          "h-7 px-3 font-display text-[11px] uppercase tracking-wider border border-bg-5 transition-colors",
          showCustom || activePreset === "custom"
            ? "bg-bg-4 text-accent border-bg-6"
            : "text-bg-9 hover:text-bg-11 hover:bg-bg-2",
        )}
      >
        Произвольный
      </button>

      {/* Кастомный диапазон */}
      {showCustom && (
        <div className="flex items-center gap-2 flex-wrap">
          <input
            type="date"
            value={customFrom}
            onChange={handleFromChange}
            max={customTo || todayIso()}
            className={cn(
              "h-7 px-2 bg-bg-2 border font-display text-[12px] text-bg-11 tracking-tight",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
              customError ? "border-danger" : "border-bg-5",
            )}
            aria-label="Дата начала периода"
          />
          <span className="font-display text-[11px] text-bg-7">—</span>
          <input
            type="date"
            value={customTo}
            onChange={handleToChange}
            min={customFrom}
            max={todayIso()}
            className={cn(
              "h-7 px-2 bg-bg-2 border font-display text-[12px] text-bg-11 tracking-tight",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
              customError ? "border-danger" : "border-bg-5",
            )}
            aria-label="Дата конца периода"
          />
          <Button size="xs" variant="secondary" onClick={applyCustom}>
            Применить
          </Button>
          {customError && (
            <span className="font-display text-[11px] text-danger">{customError}</span>
          )}
        </div>
      )}

      {/* Текущий диапазон */}
      {!showCustom && (
        <span className="font-display text-[11px] text-bg-9 tracking-tight">
          {value.from_iso} — {value.to_iso} <span className="text-bg-7">UTC</span>
        </span>
      )}
    </div>
  );
}
