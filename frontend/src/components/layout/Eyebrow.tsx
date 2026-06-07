/**
 * Eyebrow — маленький uppercase-лейбл вида "01 / OPERATE".
 * Соответствует макету: font-display, 10px, tracking .14em, text-bg-8.
 */

import { type ReactNode } from "react";
import { cn } from "@/lib/utils/cn";

interface EyebrowProps {
  num?: string;
  className?: string;
  children: ReactNode;
}

export function Eyebrow({ num, className, children }: EyebrowProps) {
  return (
    <div
      className={cn(
        "font-display text-[10px] tracking-[.14em] uppercase text-bg-8 mb-3",
        className,
      )}
    >
      {num ? <span className="text-bg-7 mr-2">{num}</span> : null}
      {children}
    </div>
  );
}
