/**
 * Eyebrow — маркер-надзаголовок канона (num="0X" + текст).
 * 12px, uppercase, tracking 0.12em. num — accent-muted, разделитель "/" — AA bg-8.
 * Единый источник стиля с web (frontend/src/components/data/Eyebrow.tsx).
 */
import type { CSSProperties, ReactNode } from "react";
import { cn } from "@/lib/cn";

interface EyebrowProps {
  /** Номер-маркер (например "01"). */
  num?: string;
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
}

export function Eyebrow({ num, children, className, style }: EyebrowProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 font-display text-[12px] font-semibold uppercase tracking-[0.12em] text-bg-9",
        className,
      )}
      style={style}
    >
      {num ? (
        <>
          <span className="text-accent-muted tabular-nums">{num}</span>
          <span className="text-bg-8">/</span>
        </>
      ) : null}
      {children}
    </span>
  );
}
