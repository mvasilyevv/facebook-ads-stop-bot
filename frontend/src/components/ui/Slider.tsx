/**
 * Slider — ползунок на native input[type=range] (1–100), монохромный.
 * Используется для чувствительности стоп-правил оффера (stop% / warning%).
 * Значение и лейбл — в одну строку; накрашиваем accent-color на нативный контрол.
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
        <label htmlFor={id} className="text-[12px] font-display tracking-wider uppercase text-bg-9">
          {label}
        </label>
        <span className="font-display tabular-nums text-[13px] text-accent">{value}%</span>
      </div>
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
        className={[
          "h-11 min-h-11 w-full accent-[var(--color-accent)] cursor-pointer",
          "disabled:opacity-40 disabled:cursor-not-allowed",
          "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        ].join(" ")}
      />
      {hint ? <span className="text-[12px] text-bg-9">{hint}</span> : null}
    </div>
  );
}
