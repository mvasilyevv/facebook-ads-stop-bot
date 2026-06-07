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
        "bg-[var(--color-bg-3)] h-4",
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

/** Карточка-скелетон для списка объявлений. */
export function AdCardSkeleton() {
  return (
    <div className="bg-[var(--color-bg-1)] border border-[var(--color-bg-5)] p-4 space-y-3">
      <div className="flex justify-between items-start gap-2">
        <div className="flex-1 space-y-1">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-4 w-40" />
        </div>
        <Skeleton className="h-5 w-16" />
      </div>
      <div className="grid grid-cols-4 gap-2">
        {[...Array(4)].map((_, i) => (
          <div key={i} className="space-y-1">
            <Skeleton className="h-3 w-full" />
            <Skeleton className="h-4 w-full" />
          </div>
        ))}
      </div>
    </div>
  );
}
