/**
 * Input — поле ввода для мобильного UI-kit.
 * Тач-цель ≥ 44px (min-h-[44px]). Острые углы (radius 0).
 * Поддерживает label, errorMessage, disabled.
 */
import type { InputHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  errorMessage?: string;
}

export function Input({ label, errorMessage, id, className, ...rest }: InputProps) {
  const inputId = id ?? `input-${Math.random().toString(36).slice(2)}`;
  return (
    <div className="flex flex-col gap-1">
      {label && (
        <label
          htmlFor={inputId}
          className="text-[11px] uppercase tracking-[0.07em] text-[var(--color-bg-9)] font-mono"
        >
          {label}
        </label>
      )}
      <input
        id={inputId}
        {...rest}
        className={cn(
          "min-h-[44px] px-3 w-full",
          "bg-[var(--color-bg-2)] border border-[var(--color-bg-5)]",
          "text-[14px] text-[var(--color-bg-11)] font-body",
          "placeholder:text-[var(--color-bg-7)]",
          "focus:outline-none focus:border-[var(--color-accent)]",
          "disabled:opacity-40 disabled:cursor-not-allowed",
          "transition-colors duration-[var(--dur-base)]",
          errorMessage && "border-[var(--color-danger)]",
          className,
        )}
      />
      {errorMessage && (
        <p className="text-[12px] text-[var(--color-danger)]">{errorMessage}</p>
      )}
    </div>
  );
}
