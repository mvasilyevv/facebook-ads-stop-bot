/**
 * Input + SearchInput — текстовые поля с иконками и лейблами.
 * Спека: bg-2, border-6, accent-outline при focus.
 * SearchInput — shortcut с leading Search icon и trailing Kbd.
 */
import { forwardRef, useId, type InputHTMLAttributes, type ReactNode } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { Search } from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { Kbd } from "./Kbd";

const inputStyles = cva(
  [
    "w-full bg-bg-2 border border-[var(--hairline-strong)] text-bg-11 rounded-[var(--radius-2)]",
    "placeholder:text-bg-9",
    "transition-colors duration-[120ms]",
    // focus-within outline ставится на обёртку; здесь убираем дефолтный outline
    "focus:bg-bg-3 focus:border-accent focus:outline-none",
    "disabled:opacity-40 disabled:cursor-not-allowed",
    "font-body",
    // focus-visible — для пользователей клавиатуры (outline на самом input)
    "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
  ],
  {
    variants: {
      size: {
        sm: "h-7 px-2.5 text-[12.5px]",
        md: "h-8 px-3 text-[13.5px]",
        lg: "h-10 px-4 text-[14px]",
      },
      hasError: {
        true: "border-danger focus:border-danger",
        false: "",
      },
    },
    defaultVariants: {
      size: "md",
      hasError: false,
    },
  },
);

type InputSize = "sm" | "md" | "lg";

export interface InputProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, "size">,
    VariantProps<typeof inputStyles> {
  size?: InputSize;
  leftIcon?: ReactNode;
  rightIcon?: ReactNode;
  errorMessage?: string;
  label?: string;
  helpText?: string;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  {
    leftIcon,
    rightIcon,
    errorMessage,
    label,
    helpText,
    size = "md",
    className,
    id: idProp,
    type = "text",
    ...rest
  },
  ref,
) {
  // Auto-generated id — гарантирует привязку label→input даже без явного id.
  const generatedId = useId();
  const id = idProp ?? generatedId;
  const errorId = errorMessage ? `${id}-error` : undefined;
  const helpId = helpText ? `${id}-help` : undefined;

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
        {leftIcon ? (
          <span
            aria-hidden="true"
            className="absolute left-2.5 top-1/2 -translate-y-1/2 text-bg-9 pointer-events-none"
          >
            {leftIcon}
          </span>
        ) : null}
        <input
          ref={ref}
          id={id}
          type={type}
          aria-invalid={!!errorMessage}
          aria-describedby={[errorId, helpId].filter(Boolean).join(" ") || undefined}
          className={cn(
            inputStyles({ size, hasError: !!errorMessage }),
            leftIcon && "pl-8",
            rightIcon && "pr-8",
            className,
          )}
          {...rest}
        />
        {rightIcon ? (
          <span
            aria-hidden="true"
            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-bg-9 pointer-events-none"
          >
            {rightIcon}
          </span>
        ) : null}
      </div>
      {errorMessage ? (
        <span id={errorId} role="alert" className="text-[11px] text-danger font-display">
          {errorMessage}
        </span>
      ) : null}
      {helpText && !errorMessage ? (
        <span id={helpId} className="text-[11px] text-bg-9">
          {helpText}
        </span>
      ) : null}
    </div>
  );
});

// ─── SearchInput ───────────────────────────────────────────────────────────────

interface SearchInputProps extends Omit<InputProps, "leftIcon" | "rightIcon" | "type"> {
  /** Горячая клавиша, которая отображается справа (напр. "/"). */
  shortcutKey?: string;
}

/** Поле поиска с иконкой Search и опциональной Kbd-подсказкой справа. */
export const SearchInput = forwardRef<HTMLInputElement, SearchInputProps>(
  function SearchInput({ shortcutKey, size = "md", ...rest }, ref) {
    return (
      <Input
        ref={ref}
        type="search"
        size={size}
        leftIcon={<Search size={14} />}
        rightIcon={shortcutKey ? <Kbd>{shortcutKey}</Kbd> : undefined}
        {...rest}
      />
    );
  },
);
