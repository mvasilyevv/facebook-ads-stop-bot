/**
 * CountryMultiSelect — выбор стран по русскому названию с поиском.
 *
 * Гео в Meta задаётся ISO-2 кодом, но код криптичен (GH=Гана vs GE=Грузия —
 * опечатка в букву, незаметная без названия). Здесь показываем русское имя и
 * флаг, а наружу (values/onChange) отдаём ISO-2 коды — контракт с бэком не
 * меняется. Печатаешь «ган» → выбираешь «🇬🇭 Гана» → в сторе «GH».
 *
 * Поведение: ↑/↓ — навигация, Enter — выбрать активную опцию, Esc — закрыть,
 * Backspace на пустом поле — удалить последнюю страну, клик вне — закрыть.
 */
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { searchCountries, countryNameRu, countryFlagEmoji } from "@fb/shared";
import { cn } from "@/lib/utils/cn";
import { Chip } from "./Pill";

export interface CountryMultiSelectProps {
  /** Выбранные страны как ISO-2 коды (контролируемо). */
  values: string[];
  /** Вызывается при добавлении/удалении страны (ISO-2 upper). */
  onChange: (codes: string[]) => void;
  label?: string;
  placeholder?: string;
  helpText?: string;
  errorMessage?: string;
  disabled?: boolean;
  id?: string;
  "aria-label"?: string;
}

export function CountryMultiSelect({
  values,
  onChange,
  label,
  placeholder,
  helpText,
  errorMessage,
  disabled,
  id: idProp,
  "aria-label": ariaLabel,
}: CountryMultiSelectProps) {
  const genId = useId();
  const id = idProp ?? genId;
  const [draft, setDraft] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const errorId = errorMessage ? `${id}-error` : undefined;
  const helpId = helpText ? `${id}-help` : undefined;
  const listId = `${id}-listbox`;

  // Опции выпадашки — только когда открыто; ограничиваем выдачу.
  const options = open ? searchCountries(draft, { exclude: values, limit: 8 }) : [];
  const activeIdx = Math.min(active, Math.max(0, options.length - 1));

  // Клик вне — закрыть выпадашку.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  function add(code: string) {
    const up = code.toUpperCase();
    if (!values.includes(up)) onChange([...values, up]);
    setDraft("");
    setActive(0);
    inputRef.current?.focus();
  }

  function remove(code: string) {
    onChange(values.filter((c) => c !== code));
  }

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setActive((a) => Math.min(a + 1, options.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const opt = options[activeIdx];
      if (opt) add(opt.code);
    } else if (e.key === "Escape") {
      setOpen(false);
    } else if (e.key === "Backspace" && draft === "") {
      const last = values[values.length - 1];
      if (last) remove(last);
    }
  }

  return (
    <div className="flex flex-col gap-1.5" ref={rootRef}>
      {label ? (
        <label htmlFor={id} className="text-[11px] font-display tracking-wider uppercase text-bg-9">
          {label}
        </label>
      ) : null}

      <div className="relative">
        <div
          className={cn(
            "w-full bg-bg-2 border rounded-[var(--radius-2)]",
            "flex flex-wrap items-center gap-1.5 px-2 py-1.5 min-h-8",
            "transition-colors duration-[120ms]",
            "focus-within:bg-bg-3 focus-within:border-accent",
            errorMessage ? "border-danger" : "border-[var(--hairline-strong)]",
            disabled && "opacity-40 cursor-not-allowed",
          )}
        >
          {values.map((code) => (
            <Chip key={code} onRemove={disabled ? undefined : () => remove(code)}>
              <span aria-hidden="true">{countryFlagEmoji(code)}</span> {countryNameRu(code)}
            </Chip>
          ))}
          <input
            id={id}
            ref={inputRef}
            value={draft}
            disabled={disabled}
            role="combobox"
            aria-expanded={open}
            aria-controls={listId}
            aria-autocomplete="list"
            onChange={(e) => {
              setDraft(e.target.value);
              setActive(0);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={handleKeyDown}
            placeholder={values.length === 0 ? placeholder : ""}
            aria-invalid={!!errorMessage}
            aria-describedby={[errorId, helpId].filter(Boolean).join(" ") || undefined}
            aria-label={ariaLabel}
            autoComplete="off"
            spellCheck={false}
            className={cn(
              "flex-1 min-w-[80px] bg-transparent outline-none",
              "text-[13.5px] text-bg-11 placeholder:text-bg-9 h-6",
            )}
          />
        </div>

        {/* Выпадашка с опциями */}
        {open && options.length > 0 ? (
          <ul
            id={listId}
            role="listbox"
            className={cn(
              "absolute z-30 mt-1 w-full max-h-64 overflow-auto",
              "bg-bg-2 border border-[var(--hairline-strong)] rounded-[var(--radius-2)]",
              "shadow-[0_8px_24px_rgba(0,0,0,0.4)] py-1",
            )}
          >
            {options.map((opt, i) => (
              <li
                key={opt.code}
                role="option"
                aria-selected={i === activeIdx}
                onMouseEnter={() => setActive(i)}
                onMouseDown={(e) => {
                  // mousedown (не click) — чтобы не потерять фокус инпута раньше add().
                  e.preventDefault();
                  add(opt.code);
                }}
                className={cn(
                  "flex items-center gap-2.5 px-3 py-1.5 cursor-pointer",
                  "text-[13px] text-bg-11",
                  i === activeIdx ? "bg-accent-bg" : "hover:bg-bg-3",
                )}
              >
                <span aria-hidden="true" className="text-[15px] leading-none">
                  {opt.flag}
                </span>
                <span className="flex-1 min-w-0 truncate">{opt.name}</span>
                <span className="font-display text-[11px] text-bg-7 tabular-nums">{opt.code}</span>
              </li>
            ))}
          </ul>
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
}
