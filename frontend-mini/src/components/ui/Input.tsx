/**
 * Input — поле ввода для мобильного UI-kit.
 * Тач-цель ≥ 44px (min-h-[44px]). Скругление radius-2, hairline-граница.
 * Поддерживает label, errorMessage, disabled.
 */
import { useId, type InputHTMLAttributes, type Ref } from "react";
import { cn } from "@/lib/cn";

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  errorMessage?: string;
  inputRef?: Ref<HTMLInputElement>;
}

export function Input({
  label,
  errorMessage,
  id,
  className,
  inputRef,
  "aria-describedby": ariaDescribedBy,
  "aria-invalid": ariaInvalid,
  ...rest
}: InputProps) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  const errorId = `${inputId}-error`;
  const describedBy = [ariaDescribedBy, errorMessage ? errorId : undefined]
    .filter(Boolean)
    .join(" ");
  return (
    <div className="flex flex-col gap-1">
      {label && (
        <label
          htmlFor={inputId}
          className="text-[12px] uppercase tracking-[0.07em] text-[var(--color-bg-9)] font-mono"
        >
          {label}
        </label>
      )}
      <input
        ref={inputRef}
        id={inputId}
        aria-describedby={describedBy || undefined}
        aria-invalid={errorMessage ? true : ariaInvalid}
        {...rest}
        className={cn(
          "min-h-[44px] px-3 w-full rounded-[var(--radius-2)]",
          "bg-[var(--color-bg-2)] border border-[var(--color-hairline)]",
          "text-[14px] text-[var(--color-bg-11)] font-body",
          "placeholder:text-[var(--color-bg-8)]",
          "focus:outline-none focus:border-[var(--color-accent)]",
          "disabled:opacity-40 disabled:cursor-not-allowed",
          "transition-colors duration-[var(--dur-base)]",
          errorMessage && "border-[var(--color-danger)]",
          className,
        )}
      />
      {errorMessage && (
        <p
          id={errorId}
          role="alert"
          className="text-[12px] text-[var(--color-danger)]"
        >
          {errorMessage}
        </p>
      )}
    </div>
  );
}
