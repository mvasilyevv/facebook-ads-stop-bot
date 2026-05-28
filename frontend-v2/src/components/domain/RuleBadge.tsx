/**
 * RuleBadge — pill для rule_code (CPL_HIGH, FREQ_HIGH...).
 */

import { cn } from "@/lib/utils/cn";

interface RuleBadgeProps {
  code: string;
  className?: string;
}

export function RuleBadge({ code, className }: RuleBadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center font-display text-[10px] tracking-wider",
        "bg-bg-3 text-bg-10 border border-bg-6 px-1.5 py-0.5",
        className,
      )}
    >
      {code}
    </span>
  );
}
