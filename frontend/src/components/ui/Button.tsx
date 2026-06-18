/**
 * Button — primary/secondary/danger/ghost/ghost-danger × lg/md/sm/xs/icon.
 * Дизайн-спека: docs/frontend_mockups/dashboard.html .btn
 * Макет: height 36px (md), 28px (sm); акцент bg на primary.
 */
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils/cn";

const buttonStyles = cva(
  [
    "inline-flex items-center justify-center gap-2",
    "border font-medium font-body rounded-[var(--radius-2)]",
    "transition-colors duration-[120ms]",
    "disabled:opacity-40 disabled:cursor-not-allowed",
    "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
    "[&_svg]:w-3.5 [&_svg]:h-3.5 [&_svg]:shrink-0",
  ],
  {
    variants: {
      variant: {
        primary:
          "bg-accent border-accent text-bg-0 hover:bg-accent-muted hover:border-accent-muted",
        secondary:
          "bg-bg-2 border-[var(--hairline-strong)] text-bg-11 hover:bg-bg-3 hover:border-bg-7",
        danger:
          "bg-danger-bg border-[rgba(199,98,92,0.3)] text-danger hover:bg-[rgba(199,98,92,0.15)] hover:border-[rgba(199,98,92,0.5)]",
        ghost:
          "bg-transparent border-transparent text-bg-10 hover:bg-bg-2 hover:text-bg-11",
        "ghost-danger":
          "bg-transparent border-transparent text-danger hover:bg-danger-bg hover:border-[rgba(199,98,92,0.3)]",
        link:
          "bg-transparent border-transparent text-bg-11 hover:text-accent underline-offset-4 hover:underline px-0",
      },
      size: {
        // icon-only: квадрат без px
        icon: "h-8 w-8 p-0",
        xs: "h-6 px-2 text-[11px]",
        sm: "h-7 px-3 text-[12.5px]",
        md: "h-9 px-4 text-[13.5px]",
        lg: "h-10 px-5 text-[14px]",
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
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonStyles> {
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
