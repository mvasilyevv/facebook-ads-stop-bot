/**
 * RuleBadge — pill для кода стоп-правила.
 * Показывает человекочитаемый лейбл (cpl_stop → «Дорогой лид»),
 * tooltip — полное название + код для отладки.
 */

import { cn } from "@/lib/utils/cn";
import { ruleCodeLabel, ruleCodeTitle } from "@/lib/constants/states";

interface RuleBadgeProps {
  code: string;
  className?: string;
}

export function RuleBadge({ code, className }: RuleBadgeProps) {
  return (
    <span
      title={ruleCodeTitle(code)}
      className={cn(
        "inline-flex items-center font-display text-[10px] tracking-wider",
        "bg-bg-3 text-bg-10 border border-bg-6 px-1.5 py-0.5",
        className,
      )}
    >
      {ruleCodeLabel(code)}
    </span>
  );
}
