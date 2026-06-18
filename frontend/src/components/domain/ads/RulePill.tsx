/**
 * RulePill / RulePills — мелкие тег-пиллы сработавших стоп-правил.
 *
 * Направление A+C: мягкий лёгкий тег (hairline-граница, скруглён, приглушённый
 * фон), `min-width` выравнивает короткие лейблы (клик/лид/рега) в одну ширину —
 * фикс «рваных» чипов. Лейбл — короткий через ruleCodeLabel(code, true),
 * title — полный человекочитаемый.
 */

import { ruleCodeLabel } from "@fb/shared";
import { cn } from "@/lib/utils/cn";

interface RulePillProps {
  code: string;
  className?: string;
}

/** Один пилл правила. */
export function RulePill({ code, className }: RulePillProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center justify-center min-w-[92px]",
        "bg-bg-2 border border-[var(--hairline)] rounded-[var(--radius-1)]",
        "px-2 py-0.5 font-display text-[10.5px] tracking-[0.02em] text-bg-10",
        "whitespace-nowrap",
        className,
      )}
      title={ruleCodeLabel(code, false)}
    >
      {ruleCodeLabel(code, true)}
    </span>
  );
}

interface RulePillsProps {
  codes: string[];
  /** Максимум видимых пиллов; остаток сворачивается в «+N». */
  max?: number;
  className?: string;
}

/** Список пиллов правил с overflow-сворачиванием «+N». */
export function RulePills({ codes, max, className }: RulePillsProps) {
  if (!codes || codes.length === 0) return null;
  const shown = max != null ? codes.slice(0, max) : codes;
  const hidden = max != null ? codes.length - shown.length : 0;
  return (
    <span className={cn("inline-flex items-center gap-1 flex-wrap", className)}>
      {shown.map((c) => (
        <RulePill key={c} code={c} />
      ))}
      {hidden > 0 ? (
        <span
          className="inline-block font-display text-[10.5px] text-bg-8 tabular-nums"
          title={codes.slice(shown.length).map((c) => ruleCodeLabel(c, false)).join(", ")}
        >
          +{hidden}
        </span>
      ) : null}
    </span>
  );
}
