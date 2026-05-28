/**
 * Skeleton — shimmer loading placeholder.
 */

import { type HTMLAttributes } from "react";
import { cn } from "@/lib/utils/cn";

interface SkeletonProps extends HTMLAttributes<HTMLDivElement> {
  /** Высота в пикселях. Default 14 (line of text). */
  height?: number;
  /** Ширина в пикселях или строкой ("60%"). */
  width?: number | string;
}

export function Skeleton({ height = 14, width = "100%", className, style, ...rest }: SkeletonProps) {
  return (
    <div
      role="status"
      aria-label="Загрузка"
      style={{ height, width, ...style }}
      className={cn("bg-bg-3 animate-pulse", className)}
      {...rest}
    />
  );
}
