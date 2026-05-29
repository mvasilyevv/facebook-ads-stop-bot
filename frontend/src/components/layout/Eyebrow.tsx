/**
 * Eyebrow — маленький uppercase-лейбл вида "01 / OPERATE".
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
    <div className={cn("eyebrow", className)}>
      {num ? <span className="eyebrow-num">{num}</span> : null}
      {children}
    </div>
  );
}
