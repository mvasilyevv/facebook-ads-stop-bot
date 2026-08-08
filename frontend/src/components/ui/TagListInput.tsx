/**
 * TagListInput — ввод списка значений «тэгами» вместо строки с запятыми.
 *
 * Печатаешь значение, Enter/запятая → добавляется chip ниже; × на chip удаляет.
 * Вставка "123, 456" разбивает на несколько токенов. Дедуп. Backspace на пустом
 * поле удаляет последний chip. Опц. normalize (напр. срез act_) и validate
 * (напр. только числовой ID) — невалидный токен не добавляется, остаётся в поле.
 */
import { useId, useState, type ClipboardEvent, type KeyboardEvent } from "react";
import { cn } from "@/lib/utils/cn";
import { Chip } from "./Pill";

export interface TagListInputProps {
  /** Текущий список значений (контролируемый). */
  values: string[];
  /** Вызывается при добавлении/удалении значения. */
  onChange: (values: string[]) => void;
  label?: string;
  placeholder?: string;
  helpText?: string;
  /** Внешняя ошибка (напр. submit-валидация «минимум 1»). */
  errorMessage?: string;
  disabled?: boolean;
  id?: string;
  "aria-label"?: string;
  /** Нормализация токена перед добавлением (трим уже сделан). */
  normalize?: (token: string) => string;
  /** Валидация нормализованного токена: текст ошибки или null. */
  validate?: (token: string) => string | null;
  /** Разделители ввода/вставки. Дефолт: пробел, запятая, ;, перенос. */
  splitPattern?: RegExp;
}

const DEFAULT_SPLIT = /[\s,;]+/;

export function TagListInput({
  values,
  onChange,
  label,
  placeholder,
  helpText,
  errorMessage,
  disabled,
  id: idProp,
  "aria-label": ariaLabel,
  normalize,
  validate,
  splitPattern = DEFAULT_SPLIT,
}: TagListInputProps) {
  const generatedId = useId();
  const id = idProp ?? generatedId;
  const [draft, setDraft] = useState("");
  const [localError, setLocalError] = useState<string | undefined>();

  const error = errorMessage ?? localError;
  const errorId = error ? `${id}-error` : undefined;
  const helpId = helpText ? `${id}-help` : undefined;

  function commit(raw: string) {
    const parts = raw
      .split(splitPattern)
      .map((t) => t.trim())
      .filter(Boolean);
    if (parts.length === 0) {
      setDraft("");
      return;
    }
    const next = [...values];
    const rejected: string[] = [];
    for (const part of parts) {
      const norm = (normalize ? normalize(part) : part).trim();
      if (!norm) continue;
      const err = validate ? validate(norm) : null;
      if (err) {
        rejected.push(part);
        continue;
      }
      if (!next.includes(norm)) next.push(norm);
    }
    if (next.length !== values.length) onChange(next);
    setDraft(rejected.join(" "));
    setLocalError(rejected.length ? `Не подходит: ${rejected.join(", ")}` : undefined);
  }

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      commit(draft);
    } else if (e.key === "Backspace" && draft === "" && values.length > 0) {
      onChange(values.slice(0, -1));
    }
  }

  function handlePaste(e: ClipboardEvent<HTMLInputElement>) {
    const text = e.clipboardData.getData("text");
    if (splitPattern.test(text)) {
      e.preventDefault();
      commit(draft ? `${draft} ${text}` : text);
    }
  }

  function remove(idx: number) {
    onChange(values.filter((_, i) => i !== idx));
  }

  return (
    <div className="flex flex-col gap-1.5">
      {label ? (
        <label htmlFor={id} className="text-[12px] font-display tracking-wider uppercase text-bg-9">
          {label}
        </label>
      ) : null}
      <div
        className={cn(
          "w-full bg-bg-2 border rounded-[var(--radius-2)]",
          "flex min-h-11 flex-wrap items-center gap-1.5 px-2 py-1.5",
          "transition-colors duration-[120ms]",
          "focus-within:bg-bg-3 focus-within:border-accent",
          error ? "border-danger" : "border-[var(--color-hairline-strong)]",
          disabled && "opacity-40 cursor-not-allowed",
        )}
      >
        {values.map((v, i) => (
          <Chip key={v} onRemove={disabled ? undefined : () => remove(i)}>
            {v}
          </Chip>
        ))}
        <input
          id={id}
          value={draft}
          disabled={disabled}
          onChange={(e) => {
            setDraft(e.target.value);
            if (localError) setLocalError(undefined);
          }}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          onBlur={() => commit(draft)}
          placeholder={values.length === 0 ? placeholder : ""}
          aria-invalid={!!error}
          aria-describedby={[errorId, helpId].filter(Boolean).join(" ") || undefined}
          aria-label={ariaLabel}
          autoComplete="off"
          spellCheck={false}
          className={cn(
            "flex-1 min-w-[80px] bg-transparent outline-none",
            "h-11 text-[13.5px] text-bg-11 placeholder:text-bg-9",
          )}
        />
      </div>
      {error ? (
        <span id={errorId} role="alert" className="text-[12px] text-danger font-display">
          {error}
        </span>
      ) : null}
      {helpText && !error ? (
        <span id={helpId} className="text-[12px] text-bg-9">
          {helpText}
        </span>
      ) : null}
    </div>
  );
}
