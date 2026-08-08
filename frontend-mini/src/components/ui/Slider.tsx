/**
 * Slider — ползунок на native input[type=range] (1–100), монохромный.
 * Портировано из frontend/src/components/ui/Slider.tsx (паритет web/mini,
 * MID-21 аудита 02.07) — используется для чувствительности стоп-правил оффера
 * (stop% / warning%). Сам native input занимает 44px по высоте; визуальный
 * track остаётся тонким через browser-specific track pseudo-elements.
 */
import { useId } from "react";

interface SliderProps {
  label: string;
  /** Текущее значение (целое в [min, max]). */
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
  disabled?: boolean;
  /** Подпись-описание под ползунком. */
  hint?: string;
}

export function Slider({
  label,
  value,
  onChange,
  min = 1,
  max = 100,
  step = 1,
  disabled = false,
  hint,
}: SliderProps) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between">
        <label
          htmlFor={id}
          className="text-[12px] uppercase tracking-[0.07em] text-[var(--color-bg-9)] font-mono"
        >
          {label}
        </label>
        <span className="font-display tabular-nums text-[14px] text-accent">
          {value}%
        </span>
      </div>
      <div className="flex min-h-11 items-center">
        <input
          id={id}
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          disabled={disabled}
          aria-label={label}
          onChange={(e) => onChange(Number(e.target.value))}
          className="mini-slider h-11 w-full cursor-pointer accent-[var(--color-accent)] disabled:cursor-not-allowed disabled:opacity-40"
        />
      </div>
      {hint ? <span className="text-[12px] text-bg-9">{hint}</span> : null}
    </div>
  );
}
