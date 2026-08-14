/**
 * Button — primary/secondary/warning/danger/ghost/ghost-danger × lg/md/sm/xs/icon.
 * Размеры и состояния задаются общими production design tokens.
 *
 * Деструктив и возобновление спенда обязаны читаться как отдельный класс
 * действий: у danger и warning плотная рамка цвета статуса, а не полупрозрачная.
 * disabled — плоская заливка, а не opacity: приглушённый красный на тёмном фоне
 * давал контраст 1.67:1 и делал заблокированную кнопку нечитаемой.
 */
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils/cn";

export const buttonStyles = cva(
  [
    "inline-flex items-center justify-center gap-2",
    "border font-medium font-body rounded-[var(--radius-2)]",
    "transition-colors duration-[120ms]",
    "disabled:cursor-not-allowed",
    "disabled:opacity-100 disabled:bg-bg-2 disabled:border-[var(--color-hairline)] disabled:text-bg-8",
    "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
    "[&_svg]:w-3.5 [&_svg]:h-3.5 [&_svg]:shrink-0",
  ],
  {
    variants: {
      variant: {
        primary:
          "bg-accent border-accent text-bg-0 hover:bg-accent-muted hover:border-accent-muted",
        secondary:
          "bg-bg-2 border-[var(--color-hairline-strong)] text-bg-11 hover:bg-bg-3 hover:border-bg-7",
        // Возобновление реального спенда: не нейтральная утилита, но и не деструктив.
        warning:
          "bg-warning-bg border-warning text-warning hover:bg-warning/15 hover:border-warning",
        danger: "bg-danger-bg border-danger text-danger hover:bg-danger/15 hover:border-danger",
        ghost: "bg-transparent border-transparent text-bg-10 hover:bg-bg-2 hover:text-bg-11",
        "ghost-danger":
          "bg-transparent border-transparent text-danger hover:bg-danger-bg hover:border-danger",
        link: "bg-transparent border-transparent text-bg-11 hover:text-accent underline-offset-4 hover:underline px-0",
      },
      size: {
        // icon-only: квадрат без px
        icon: "h-11 w-11 p-0",
        xs: "h-11 px-3 text-[12px]",
        sm: "h-11 px-3 text-[13px]",
        md: "h-11 px-4 text-[14px]",
        lg: "h-12 px-5 text-[15px]",
      },
      fullWidth: {
        true: "w-full",
        false: "",
      },
    },
    defaultVariants: {
      variant: "secondary",
      size: "md",
      fullWidth: false,
    },
  },
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonStyles> {
  leftIcon?: ReactNode;
  rightIcon?: ReactNode;
  /** Показывает spinner вместо leftIcon, disabled=true. */
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    children,
    variant,
    size,
    fullWidth,
    leftIcon,
    rightIcon,
    loading,
    disabled,
    className,
    ...rest
  },
  ref,
) {
  return (
    <button
      ref={ref}
      className={cn(buttonStyles({ variant, size, fullWidth }), className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      data-loading={loading || undefined}
      {...rest}
    >
      {loading ? (
        <span
          aria-hidden="true"
          className="inline-block size-3.5 animate-spin border border-current border-r-transparent rounded-full"
        />
      ) : (
        leftIcon
      )}
      {children}
      {!loading && rightIcon}
    </button>
  );
});
