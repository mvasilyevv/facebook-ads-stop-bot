/**
 * RulePill — тег сработавшего стоп-правила.
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
        "bg-bg-2 border border-[var(--color-hairline)] rounded-[var(--radius-1)]",
        "px-2 py-0.5 font-display text-[12px] tracking-[0.02em] text-bg-10",
        "whitespace-nowrap",
        className,
      )}
      title={ruleCodeLabel(code, false)}
    >
      {ruleCodeLabel(code, true)}
    </span>
  );
}
