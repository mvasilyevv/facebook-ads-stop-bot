/**
 * Select — стилизованный нативный <select>.
 * Для сложного поиска используется отдельный Popover-combobox.
 */
import { forwardRef, type SelectHTMLAttributes, useId } from "react";
import { cn } from "@/lib/utils/cn";
import { ChevronDown } from "lucide-react";

export interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, "size"> {
  options: SelectOption[];
  size?: "sm" | "md" | "lg";
  label?: string;
  errorMessage?: string;
  placeholder?: string;
}

const SIZE_CLASS: Record<NonNullable<SelectProps["size"]>, string> = {
  sm: "h-11 px-2.5 pr-8 text-[12px]",
  md: "h-11 px-3 pr-8 text-[12.5px]",
  lg: "h-12 px-4 pr-10 text-[14px]",
};

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  {
    options,
    size = "md",
    label,
    errorMessage,
    placeholder,
    className,
    id,
    "aria-describedby": describedBy,
    ...rest
  },
  ref,
) {
  const generatedId = useId();
  const controlId = id ?? (label ? `${generatedId}-select` : undefined);
  const errorId = errorMessage ? `${controlId ?? generatedId}-error` : undefined;
  const descriptionIds = [describedBy, errorId].filter(Boolean).join(" ") || undefined;

  return (
    <div className="flex flex-col gap-1.5">
      {label ? (
        <label
          htmlFor={controlId}
          className="text-[12px] font-display tracking-wider uppercase text-bg-9"
        >
          {label}
        </label>
      ) : null}
      <div className="relative">
        <select
          ref={ref}
          id={controlId}
          aria-describedby={descriptionIds}
          aria-invalid={!!errorMessage}
          className={cn(
            "w-full appearance-none bg-bg-2 border border-[var(--color-hairline-strong)] text-bg-11 rounded-[var(--radius-2)]",
            "font-display tracking-[0.02em]",
            "transition-colors duration-[120ms]",
            "focus:bg-bg-3 focus:border-accent focus:outline-none",
            "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
            "disabled:opacity-40 disabled:cursor-not-allowed",
            "cursor-pointer",
            SIZE_CLASS[size],
            errorMessage && "border-danger focus:border-danger",
            className,
          )}
          {...rest}
        >
          {placeholder ? (
            <option value="" disabled>
              {placeholder}
            </option>
          ) : null}
          {options.map((opt) => (
            <option key={opt.value} value={opt.value} disabled={opt.disabled}>
              {opt.label}
            </option>
          ))}
        </select>
        <ChevronDown
          aria-hidden="true"
          size={12}
          className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-bg-9"
        />
      </div>
      {errorMessage ? (
        <span id={errorId} role="alert" className="text-[12px] text-danger font-display">
          {errorMessage}
        </span>
      ) : null}
    </div>
  );
});
