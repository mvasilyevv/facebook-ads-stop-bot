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
import type { MonitoringState } from "./monitoringState";

interface HeroProps {
  /** Всего под контролем (включая отключённые) = total_ads_monitored. */
  total: number | null;
  normal: number;
  warning: number;
  stop: number;
  /** Отключённые (под контролем, но не крутятся) — для HealthBar. */
  disabled?: number;
  monitoringState: MonitoringState;
}

const STATUS_VIEW: Record<MonitoringState, { label: string; color: string; pulse: boolean }> = {
  healthy: { label: "СИСТЕМА В НОРМЕ", color: "var(--success)", pulse: true },
  paused: { label: "МОНИТОРИНГ НА ПАУЗЕ", color: "var(--warning)", pulse: false },
  degraded: { label: "СИСТЕМА ОГРАНИЧЕНА", color: "var(--warning)", pulse: true },
  offline: { label: "СИСТЕМА НЕДОСТУПНА", color: "var(--danger)", pulse: false },
  unknown: { label: "ОЖИДАЕМ ПЕРВЫЙ СКАН", color: "var(--info)", pulse: false },
};

export function Hero({
  total,
  normal,
  warning,
  stop,
  disabled = 0,
  monitoringState,
}: HeroProps) {
  const needsAttention = warning > 0 || stop > 0;
  const baseView = STATUS_VIEW[monitoringState];
  const view =
    monitoringState === "healthy" && needsAttention
      ? { label: "ТРЕБУЕТ ВНИМАНИЯ", color: "var(--warning)", pulse: true }
      : baseView;
  const value = useCountUp(total ?? 0);

  return (
    <div>
      {/* Статус-строка */}
      <div className="mb-3 flex items-center gap-[11px]">
        {view.pulse ? (
          <PulseDot size={10} color={view.color} />
        ) : (
          <span aria-hidden="true" className="size-2.5 rounded-full" style={{ background: view.color }} />
        )}
        <span
          aria-live="polite"
          className="font-display text-[10px] font-semibold uppercase tracking-[0.12em]"
          style={{ color: view.color }}
        >
          {view.label}
        </span>
      </div>

      {/* Гигантское число + подпись */}
      <div className="mb-[22px] flex items-baseline gap-3.5">
        <span
          className="font-display text-[64px] font-medium tabular-nums text-bg-11 sm:text-[88px]"
          style={{ lineHeight: 0.82, letterSpacing: "-0.04em" }}
        >
          {total === null ? "—" : value}
        </span>
        <span className="max-w-[160px] text-[16px] leading-[1.3] text-bg-10">
          объявлений под контролем
        </span>
      </div>

      {/* Health-bar */}
      {total === null ? (
        <p className="text-[12px] text-bg-8">Данные об объявлениях ещё не получены.</p>
      ) : (
        <HealthBar normal={normal} warning={warning} stop={stop} disabled={disabled} />
      )}
    </div>
  );
}
