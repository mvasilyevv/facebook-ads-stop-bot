/**
 * ReasonBlock — блок «AI reasoning» с цитатой.
 *
 * Макет (drafts.html .reason):
 *   - bg-bg-2, accent левый border 2px
 *   - eyebrow: "AI reasoning" + source (claude-opus-*)
 *   - italic quote текст
 */

import { cn } from "@/lib/utils/cn";

interface ReasonBlockProps {
  /** Текст обоснования от AI. */
  text: string;
  /** Источник: провайдер/модель, напр. "claude-opus-4-7". */
  source?: string | null;
  className?: string;
}

export function ReasonBlock({ text, source, className }: ReasonBlockProps) {
  return (
    <div
      className={cn(
        "bg-bg-2 border-l-2 border-accent rounded-r-[var(--radius-2)]",
        "px-4 py-[14px]",
        "text-[13px] text-bg-10 leading-[1.55]",
        className,
      )}
    >
      {/* Eyebrow с источником */}
      <div className="flex items-center gap-2 mb-2">
        <span className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-8">
          AI reasoning
        </span>
        {source ? (
          <span className="font-display text-[10px] tracking-[0.04em] text-accent">
            {source}
          </span>
        ) : null}
      </div>

      {/* Цитата курсивом с декоративными кавычками */}
      <p className="m-0 font-body italic text-bg-10">
        <span
          aria-hidden="true"
          className="text-bg-7 text-[22px] leading-none align-[-0.2em] mr-0.5"
        >
          "
        </span>
        {text}
        <span
          aria-hidden="true"
          className="text-bg-7 text-[22px] leading-none align-[-0.2em] ml-0.5"
        >
          "
        </span>
      </p>
    </div>
  );
}
