/**
 * Button — кнопка мобильного UI-kit.
 * Тач-цель ≥ 44px (min-h-[44px]).
 * Варианты: primary (accent off-white), secondary (border), ghost, danger.
 */
import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md" | "lg";

const VARIANT_STYLES: Record<ButtonVariant, string> = {
  primary:
    "bg-[var(--color-accent)] text-bg-0 font-semibold hover:bg-[var(--color-accent-muted)] active:opacity-80",
  secondary:
    "border border-[var(--color-hairline-strong)] text-[var(--color-bg-11)] hover:border-[var(--color-bg-7)] active:opacity-80",
  ghost:
    "text-[var(--color-bg-10)] hover:text-[var(--color-bg-11)] hover:bg-[var(--color-bg-3)] active:opacity-80",
  danger:
    "bg-[var(--color-danger-bg)] text-[var(--color-danger)] border border-[var(--color-danger)] hover:opacity-90 active:opacity-70",
};

const SIZE_STYLES: Record<ButtonSize, string> = {
  sm: "min-h-[44px] px-3 text-[13px]",
  md: "min-h-[44px] px-4 text-[14px]",
  lg: "min-h-[52px] px-6 text-[15px]",
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  fullWidth?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  function Button(
    {
      variant = "primary",
      size = "md",
      loading = false,
      fullWidth = false,
      disabled,
      className,
      children,
      ...rest
    },
    ref,
  ) {
    const isDisabled = disabled || loading;
    return (
      <button
        ref={ref}
        {...rest}
        disabled={isDisabled}
        aria-busy={loading || undefined}
        data-loading={loading || undefined}
        className={cn(
          // базовые стили
          "inline-flex items-center justify-center gap-2",
          "rounded-[var(--radius-2)]",
          "font-body transition-opacity",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]",
          // размер
          SIZE_STYLES[size],
          // вариант
          VARIANT_STYLES[variant],
          // состояния
          isDisabled && "opacity-40 cursor-not-allowed pointer-events-none",
          fullWidth && "w-full",
          className,
        )}
      >
        {loading && (
          <span
            aria-hidden
            className="inline-block w-4 h-4 border-2 border-current border-t-transparent rounded-full animate-spin"
          />
        )}
        {children}
      </button>
    );
  },
);
