/**
 * Presentational status badge. Domain states are rendered only by typed
 * operator view-models; this primitive never guesses or normalizes status.
 */
import { BADGE_VARIANT_CLASSES, type BadgeVariant } from "@fb/shared/tokens/badgeVariants";
import { cn } from "@/lib/cn";

export type { BadgeVariant };

export type BadgeSize = "sm" | "md";

const SIZE_STYLES: Record<BadgeSize, string> = {
  sm: "h-6 px-2 text-[12px]",
  md: "h-7 px-2.5 text-[12px]",
};

interface BadgeProps {
  variant?: BadgeVariant;
  size?: BadgeSize;
  /** Показать точку-индикатор перед текстом. */
  withDot?: boolean;
  children: React.ReactNode;
  className?: string;
}

export function Badge({
  variant = "neutral",
  size = "md",
  withDot = false,
  children,
  className,
}: BadgeProps) {
  const tone = BADGE_VARIANT_CLASSES[variant];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border",
        "font-display font-medium leading-none uppercase tracking-wide",
        SIZE_STYLES[size],
        tone.surface,
        className,
      )}
    >
      {withDot && (
        <span aria-hidden className={cn("inline-block size-[6px] rounded-full", tone.dot)} />
      )}
      {children}
    </span>
  );
}
