/**
 * Skeleton — placeholder при загрузке данных.
 * shimmer-анимация, prefers-reduced-motion: статичный серый.
 */
import { cn } from "@/lib/cn";

interface SkeletonProps {
  className?: string;
  /** Количество строк (при height auto). */
  lines?: number;
}

export function Skeleton({ className, lines }: SkeletonProps) {
  if (lines && lines > 1) {
    return (
      <div className="flex flex-col gap-2">
        {Array.from({ length: lines }).map((_, i) => (
          <SkeletonBase
            key={i}
            className={cn(i === lines - 1 && "w-2/3", className)}
          />
        ))}
      </div>
    );
  }
  return <SkeletonBase className={className} />;
}

function SkeletonBase({ className }: { className?: string }) {
  return (
    <div
      role="status"
      aria-label="Загрузка..."
      className={cn(
        "bg-[var(--color-bg-3)] h-4 rounded-[var(--radius-1)]",
        // shimmer
        "relative overflow-hidden",
        "before:absolute before:inset-0",
        "before:bg-gradient-to-r before:from-transparent before:via-[rgba(255,255,255,0.04)] before:to-transparent",
        "before:animate-[shimmer_1.4s_linear_infinite]",
        "motion-reduce:before:animate-none",
        className,
      )}
    />
  );
}
