/**
 * Badge — presentational status pills.
 * Минимальный служебный текст — 12px; высота сохраняет свободное вертикальное дыхание.
 * Domain states arrive from strict typed operator view-models; Badge never
 * guesses how to normalize an unknown state.
 */
import { type HTMLAttributes } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { BADGE_VARIANT_CLASSES, type BadgeVariant as SharedBadgeVariant } from "@fb/shared/tokens/badgeVariants";
import { cn } from "@/lib/utils/cn";

const SURFACE_VARIANTS = Object.fromEntries(
  Object.entries(BADGE_VARIANT_CLASSES).map(([variant, classes]) => [variant, classes.surface]),
) as Record<SharedBadgeVariant, string>;

const DOT_VARIANTS = Object.fromEntries(
  Object.entries(BADGE_VARIANT_CLASSES).map(([variant, classes]) => [variant, classes.dot]),
) as Record<SharedBadgeVariant, string>;

const badgeStyles = cva(
  [
    "inline-flex items-center gap-1.5",
    "rounded-full border font-display font-medium uppercase",
    "tracking-[0.08em]",
    "whitespace-nowrap shrink-0",
  ],
  {
    variants: {
      variant: SURFACE_VARIANTS,
      size: {
        sm: "h-6 px-2 text-[12px]",
        md: "h-7 px-2.5 text-[12px]",
      },
    },
    defaultVariants: {
      variant: "neutral",
      size: "md",
    },
  },
);

const dotStyles = cva("rounded-full shrink-0", {
  variants: {
    variant: DOT_VARIANTS,
  },
  defaultVariants: { variant: "neutral" },
});

export type BadgeVariant = NonNullable<VariantProps<typeof badgeStyles>["variant"]>;

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeStyles> {
  /** Показывать точку-статус (default true). */
  withDot?: boolean;
}

export function Badge({ children, variant, size, withDot = true, className, ...rest }: BadgeProps) {
  return (
    <span className={cn(badgeStyles({ variant, size }), className)} {...rest}>
      {withDot ? (
        // dot 6×6px согласно спеке
        <span aria-hidden="true" className={cn(dotStyles({ variant }), "size-1.5")} />
      ) : null}
      {children}
    </span>
  );
}
