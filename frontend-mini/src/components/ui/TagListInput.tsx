import { useId, useState, type KeyboardEvent } from "react";
import { X } from "lucide-react";

import { cn } from "@/lib/cn";

interface TagListInputProps {
  label: string;
  values: string[];
  onChange: (values: string[]) => void;
  normalize?: (value: string) => string;
  validate?: (value: string) => string | null;
  placeholder?: string;
  errorMessage?: string;
  helpText?: string;
}

/** Mobile-safe multi-value input: every committed value is a removable tag. */
export function TagListInput({
  label,
  values,
  onChange,
  normalize,
  validate,
  placeholder,
  errorMessage,
  helpText,
}: TagListInputProps) {
  const id = useId();
  const [draft, setDraft] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const error = errorMessage ?? localError;

  const commit = () => {
    const tokens = draft
      .split(/[\s,;]+/)
      .map((value) => (normalize ? normalize(value.trim()) : value.trim()))
      .filter(Boolean);
    if (tokens.length === 0) {
      setDraft("");
      return;
    }
    const invalid = tokens.find((value) => validate?.(value));
    if (invalid) {
      setLocalError(validate?.(invalid) ?? "Некорректное значение");
      return;
    }
    onChange([...new Set([...values, ...tokens])]);
    setDraft("");
    setLocalError(null);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      commit();
    } else if (event.key === "Backspace" && draft === "" && values.length > 0) {
      onChange(values.slice(0, -1));
    }
  };

  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="text-[12px] uppercase tracking-[0.08em] text-bg-9">
        {label}
      </label>
      <div
        className={cn(
          "flex min-h-12 flex-wrap items-center gap-1.5 rounded-[var(--radius-2)] border bg-bg-2 px-2 py-1.5",
          error
            ? "border-danger"
            : "border-[var(--color-hairline-strong)] focus-within:border-accent",
        )}
      >
        {values.map((value) => (
          <span
            key={value}
            className="inline-flex min-h-9 items-center gap-1 rounded-[var(--radius-1)] bg-bg-4 pl-2 text-[12px] text-bg-11"
          >
            {value}
            <button
              type="button"
              aria-label={`Удалить ${value}`}
              onClick={() => onChange(values.filter((candidate) => candidate !== value))}
              className="flex size-9 items-center justify-center text-bg-8"
            >
              <X size={13} aria-hidden="true" />
            </button>
          </span>
        ))}
        <input
          id={id}
          value={draft}
          onChange={(event) => {
            setDraft(event.target.value);
            setLocalError(null);
          }}
          onKeyDown={onKeyDown}
          onBlur={commit}
          placeholder={values.length === 0 ? placeholder : ""}
          className="h-11 min-w-[90px] flex-1 bg-transparent px-1 text-[14px] text-bg-11 outline-none placeholder:text-bg-8"
          aria-invalid={Boolean(error)}
        />
      </div>
      {error ? (
        <p role="alert" className="text-[12px] text-danger">
          {error}
        </p>
      ) : helpText ? (
        <p className="text-[12px] text-bg-8">{helpText}</p>
      ) : null}
    </div>
  );
}
