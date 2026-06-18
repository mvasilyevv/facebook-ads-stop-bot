/**
 * Select — нативный select для мобильного UI-kit.
 * Тач-цель ≥ 44px. Скругление radius-2. Соответствует дизайну Input.
 */
import { useId, type SelectHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

interface SelectOption {
  value: string;
  label: string;
}

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  options: SelectOption[];
  errorMessage?: string;
}

export function Select({ label, options, errorMessage, id, className, ...rest }: SelectProps) {
  const generatedId = useId();
  const selectId = id ?? generatedId;
  return (
    <div className="flex flex-col gap-1">
      {label && (
        <label
          htmlFor={selectId}
          className="text-[11px] uppercase tracking-[0.07em] text-[var(--color-bg-9)] font-mono"
        >
          {label}
        </label>
      )}
      <select
        id={selectId}
        {...rest}
        className={cn(
          "min-h-[44px] px-3 w-full appearance-none rounded-[var(--radius-2)]",
          "bg-[var(--color-bg-2)] border border-[var(--hairline)]",
          "text-[14px] text-[var(--color-bg-11)] font-body",
          "focus:outline-none focus:border-[var(--color-accent)]",
          "disabled:opacity-40 disabled:cursor-not-allowed",
          "transition-colors duration-[var(--dur-base)]",
          errorMessage && "border-[var(--color-danger)]",
          className,
        )}
      >
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
      {errorMessage && (
        <p className="text-[12px] text-[var(--color-danger)]">{errorMessage}</p>
      )}
    </div>
  );
}
