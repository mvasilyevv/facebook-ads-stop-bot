/**
 * RulePill / RulePills — мелкие код-пиллы сработавших стоп-правил.
 *
 * Канон design_handoff (ads-web.jsx .rulepill + components.jsx RulePills):
 * bg-3, border-6, 10.5px mono, tracking. Лейбл — короткий код через
 * ruleCodeLabel(code, true), title — полный человекочитаемый.
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
        "inline-block bg-bg-3 border border-bg-6",
        "px-1.5 py-0.5 font-display text-[10.5px] tracking-[0.04em] text-bg-10",
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
