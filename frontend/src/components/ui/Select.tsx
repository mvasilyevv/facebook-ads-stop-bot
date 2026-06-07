/**
 * Select — стилизованный нативный <select>.
 * Спека: ads.html .select — bg-2, border-6, height 32px, font-display 12px.
 * Кастомный Popover-combobox — отдельный компонент.
 */
import { forwardRef, type SelectHTMLAttributes } from "react";
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
  sm: "h-7 px-2.5 pr-8 text-[12px]",
  md: "h-8 px-3 pr-8 text-[12.5px]",
  lg: "h-10 px-4 pr-10 text-[14px]",
};

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { options, size = "md", label, errorMessage, placeholder, className, id, ...rest },
  ref,
) {
  return (
    <div className="flex flex-col gap-1.5">
      {label ? (
        <label
          htmlFor={id}
          className="text-[11px] font-display tracking-wider uppercase text-bg-9"
        >
          {label}
        </label>
      ) : null}
      <div className="relative">
        <select
          ref={ref}
          id={id}
          aria-invalid={!!errorMessage}
          className={cn(
            "w-full appearance-none bg-bg-2 border border-bg-6 text-bg-11",
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
        <span role="alert" className="text-[11px] text-danger font-display">
          {errorMessage}
        </span>
      ) : null}
    </div>
  );
});
