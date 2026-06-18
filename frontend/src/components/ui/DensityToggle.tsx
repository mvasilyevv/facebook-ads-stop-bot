/**
 * DensityToggle — переключатель плотности строк таблиц (comfortable/compact/dense).
 *
 * Store (useUiStore.density + tokens.css [data-density]) существовал с самого
 * начала, но UI-переключателя не было — фича была мертва. Размещается рядом
 * с keyboard-legend на Ads (канон: 44/34/28px высоты строки).
 *
 * Три кнопки-сегмента с мини-пиктограммой «линий» — плотность видна без слов.
 */

import { useUiStore, type Density } from "@/stores/ui";
import { cn } from "@/lib/utils/cn";

const OPTIONS: Array<{ value: Density; label: string; bars: number }> = [
  { value: "comfortable", label: "Просторно", bars: 2 },
  { value: "compact", label: "Компактно", bars: 3 },
  { value: "dense", label: "Плотно", bars: 4 },
];

export function DensityToggle({ className }: { className?: string }) {
  const density = useUiStore((s) => s.density);
  const setDensity = useUiStore((s) => s.setDensity);

  return (
    <div
      role="radiogroup"
      aria-label="Плотность строк"
      className={cn(
        "inline-flex border border-[var(--hairline-strong)] rounded-[var(--radius-2)] overflow-hidden",
        className,
      )}
    >
      {OPTIONS.map((o) => {
        const on = density === o.value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={on}
            aria-label={o.label}
            title={o.label}
            onClick={() => setDensity(o.value)}
            className={cn(
              "inline-flex flex-col items-center justify-center gap-[2px] w-7 h-[22px]",
              "transition-colors duration-[120ms] cursor-pointer",
              "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent",
              on ? "bg-bg-3 text-bg-11" : "text-bg-8 hover:text-bg-10",
            )}
          >
            {/* Пиктограмма: N горизонтальных линий = плотность строк */}
            {Array.from({ length: o.bars }).map((_, i) => (
              <span
                key={i}
                aria-hidden="true"
                className="block w-3 h-px"
                style={{ background: "currentColor" }}
              />
            ))}
          </button>
        );
      })}
    </div>
  );
}
