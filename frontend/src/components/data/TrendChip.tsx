/**
 * TrendChip — индикатор тренда (↑/↓ + проценты), окрашенный по семантике.
 *
 * Портировано из design_handoff/components.jsx (Trend). Логика цвета:
 *   - для «хороших» метрик (tone=normal/disabled): рост = success, падение = bg-10;
 *   - для «плохих» метрик (tone=warning/stop): рост = danger, падение = success.
 * Это операторская семантика: рост WARNING/STOP — плохо, рост ACTIVE — хорошо.
 *
 * Если данных по тренду нет (value == null) — рисуем «—», без фейка.
 */

import { ArrowUp, ArrowDown } from "lucide-react";

export type TrendTone = "normal" | "warning" | "stop" | "disabled";

interface TrendChipProps {
  /** Знаковая дельта (для определения направления). null/0 → «—». */
  value: number | null | undefined;
  /** Готовая строка процента ("+2.5%", "−20%"). */
  pct: string;
  /** Семантика метрики — определяет, рост это хорошо или плохо. */
  tone: TrendTone;
}

export function TrendChip({ value, pct, tone }: TrendChipProps) {
  if (value == null || value === 0) {
    return (
      <span className="font-display text-[12px] tabular-nums text-bg-8">—</span>
    );
  }

  const up = value > 0;
  const badMetric = tone === "stop" || tone === "warning";
  const color = up
    ? badMetric
      ? "text-danger"
      : "text-success"
    : badMetric
      ? "text-success"
      : "text-bg-10";

  const Arrow = up ? ArrowUp : ArrowDown;

  return (
    <span
      className={`inline-flex items-center gap-0.5 font-display text-[12px] font-medium tabular-nums ${color}`}
    >
      <Arrow size={12} strokeWidth={2} aria-hidden="true" />
      {pct}
    </span>
  );
}
