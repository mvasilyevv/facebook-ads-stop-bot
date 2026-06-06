/**
 * Kbd — стилизованный keyboard hint.
 * Спека: bg-3, border-6, font-display 11px, height 20px.
 */
import { type HTMLAttributes } from "react";
import { cn } from "./cn";

export function Kbd({ className, children, ...rest }: HTMLAttributes<HTMLElement>) {
  return (
    <kbd
      className={cn(
        "inline-flex items-center justify-center",
        "h-5 px-1.5 min-w-[20px]",
        "font-display text-[11px] text-bg-10",
        "bg-bg-3 border border-bg-6",
        className,
      )}
      {...rest}
    >
      {children}
    </kbd>
  );
}
