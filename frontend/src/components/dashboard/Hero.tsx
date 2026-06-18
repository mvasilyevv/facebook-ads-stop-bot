/**
 * Hero — левая колонка hero+chart строки Dashboard.
 *
 * Канон design_handoff/web-dashboard.jsx:
 *   - pulsing status-dot + eyebrow «СИСТЕМА В НОРМЕ» (success) / «ТРЕБУЕТ ВНИМАНИЯ» (warning)
 *   - ГИГАНТСКОЕ число 88px (count-up 0→target) = объявлений под контролем
 *   - caption «объявлений под контролем» сбоку
 *   - HealthBar (доли Норма/Предупреждение/Стоп/Отключено)
 *
 * `total` = ВСЕ объявления под контролем бота, включая отключённые (он их и выключил).
 * Совпадает с total_ads_monitored и с мини-аппом — disabled не выкидываем из счёта.
 * `needsAttention` = есть warning/stop. Управляет цветом статус-дота и eyebrow.
 */

import { PulseDot } from "@/components/data/PulseDot";
import { HealthBar } from "./HealthBar";
import { useCountUp } from "@/lib/hooks/useCountUp";

interface HeroProps {
  /** Всего под контролем (включая отключённые) = total_ads_monitored. */
  total: number;
  normal: number;
  warning: number;
  stop: number;
  /** Отключённые (под контролем, но не крутятся) — для HealthBar. */
  disabled?: number;
}

export function Hero({ total, normal, warning, stop, disabled = 0 }: HeroProps) {
  const needsAttention = warning > 0 || stop > 0;
  const accentColor = needsAttention ? "var(--warning)" : "var(--success)";
  const value = useCountUp(total);

  return (
    <div>
      {/* Статус-строка */}
      <div className="mb-3 flex items-center gap-[11px]">
        <PulseDot size={10} color={accentColor} />
        <span
          className="font-display text-[10px] font-semibold uppercase tracking-[0.12em]"
          style={{ color: accentColor }}
        >
          {needsAttention ? "ТРЕБУЕТ ВНИМАНИЯ" : "СИСТЕМА В НОРМЕ"}
        </span>
      </div>

      {/* Гигантское число + подпись */}
      <div className="mb-[22px] flex items-baseline gap-3.5">
        <span
          className="font-display font-medium tabular-nums text-bg-11"
          style={{ fontSize: 88, lineHeight: 0.82, letterSpacing: "-0.04em" }}
        >
          {value}
        </span>
        <span className="max-w-[160px] text-[16px] leading-[1.3] text-bg-10">
          объявлений под контролем
        </span>
      </div>

      {/* Health-bar */}
      <HealthBar normal={normal} warning={warning} stop={stop} disabled={disabled} />
    </div>
  );
}
