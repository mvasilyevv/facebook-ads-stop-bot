/**
 * BulkActionBar — sticky-панель bulk-действий при выборе строк.
 *
 * Позиция: fixed bottom:24px left:50% translateX(-50%) z-100.
 * Показывается только когда выбрано > 0 строк.
 *
 * Действия:
 *   - Disable (danger)
 *   - Snooze (выпадающий вариант минут: 15/30/60/120/240)
 *   - Mark claimed (вторичный)
 *   - Clear selection (ghost)
 *
 * Все коллбэки — снаружи, компонент presentational.
 */

import { useState, useRef, useEffect } from "react";
import { XCircle, BellOff, CheckCircle, X, ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils/cn";

// ─── Варианты снуза ──────────────────────────────────────────────────────────

const SNOOZE_OPTIONS: Array<{ label: string; minutes: number }> = [
  { label: "15 минут", minutes: 15 },
  { label: "30 минут", minutes: 30 },
  { label: "1 час", minutes: 60 },
  { label: "2 часа", minutes: 120 },
  { label: "4 часа", minutes: 240 },
];

// ─── Публичный API ───────────────────────────────────────────────────────────

export interface BulkActionBarProps {
  /** Количество выбранных строк. */
  count: number;
  /** Disabled = идёт мутация (кнопки блокируются). */
  isPending?: boolean;

  /** Коллбэки действий */
  onDisable: () => void;
  onSnooze: (minutes: number) => void;
  onMarkClaimed: () => void;
  onClear: () => void;
}

// ─── Компонент ───────────────────────────────────────────────────────────────

export function BulkActionBar({
  count,
  isPending = false,
  onDisable,
  onSnooze,
  onMarkClaimed,
  onClear,
}: BulkActionBarProps) {
  const [snoozeOpen, setSnoozeOpen] = useState(false);
  const snoozeRef = useRef<HTMLDivElement>(null);

  // Закрывать дропдаун клика вне
  useEffect(() => {
    if (!snoozeOpen) return;
    function handleClick(e: MouseEvent) {
      if (snoozeRef.current && !snoozeRef.current.contains(e.target as Node)) {
        setSnoozeOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [snoozeOpen]);

  // Выбрать вариант снуза
  function handleSnooze(minutes: number) {
    setSnoozeOpen(false);
    onSnooze(minutes);
  }

  return (
    <div
      role="toolbar"
      aria-label={`Действия над ${count} выбранными объявлениями`}
      className={cn(
        "fixed bottom-6 left-1/2 -translate-x-1/2 z-[100]",
        "flex items-center gap-3 px-4 py-2.5",
        "bg-bg-2 border border-bg-7",
        "shadow-[0_8px_32px_rgba(0,0,0,0.6),inset_0_1px_0_rgba(255,255,255,0.04)]",
      )}
    >
      {/* Счётчик выбранных */}
      <span className="font-display text-[13px] text-bg-11 shrink-0">
        <span className="text-accent font-semibold tabular-nums">{count}</span> выбрано
      </span>

      <Divider />

      {/* Disable */}
      <Button
        variant="danger"
        size="sm"
        leftIcon={<XCircle size={14} aria-hidden="true" />}
        onClick={onDisable}
        disabled={isPending}
        aria-label={`Отключить ${count} объявлений`}
      >
        Отключить
      </Button>

      {/* Snooze с дропдауном вариантов */}
      <div ref={snoozeRef} className="relative">
        <button
          type="button"
          onClick={() => setSnoozeOpen((v) => !v)}
          disabled={isPending}
          aria-haspopup="listbox"
          aria-expanded={snoozeOpen}
          aria-label="Снузировать выбранные объявления"
          className={cn(
            "inline-flex items-center gap-1.5 h-7 px-3",
            "bg-bg-3 border border-bg-6 text-bg-11",
            "font-display text-[12px] tracking-wide",
            "hover:bg-bg-4 hover:border-bg-7 transition-colors",
            "disabled:opacity-40 disabled:cursor-not-allowed",
          )}
        >
          <BellOff size={13} aria-hidden="true" />
          Снуз
          <ChevronDown
            size={11}
            aria-hidden="true"
            className={cn("transition-transform", snoozeOpen && "rotate-180")}
          />
        </button>

        {/* Дропдаун вариантов */}
        {snoozeOpen && (
          <div
            role="listbox"
            aria-label="Время снуза"
            className={cn(
              "absolute bottom-full left-0 mb-1 min-w-[140px]",
              "bg-bg-3 border border-bg-6",
              "shadow-[0_4px_16px_rgba(0,0,0,0.5)]",
              "z-10",
            )}
          >
            {SNOOZE_OPTIONS.map((opt) => (
              <button
                key={opt.minutes}
                type="button"
                role="option"
                aria-selected={false}
                onClick={() => handleSnooze(opt.minutes)}
                className={cn(
                  "w-full text-left px-3 py-2",
                  "font-display text-[12px] text-bg-11",
                  "hover:bg-bg-4 transition-colors",
                )}
              >
                {opt.label}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Mark claimed */}
      <Button
        variant="secondary"
        size="sm"
        leftIcon={<CheckCircle size={13} aria-hidden="true" />}
        onClick={onMarkClaimed}
        disabled={isPending}
        aria-label={`Отметить ${count} объявлений как «в работе»`}
      >
        В работе
      </Button>

      <Divider />

      {/* Сбросить выбор */}
      <Button
        variant="ghost"
        size="sm"
        leftIcon={<X size={13} aria-hidden="true" />}
        onClick={onClear}
        disabled={isPending}
        aria-label="Сбросить выбор"
      >
        Сбросить
      </Button>
    </div>
  );
}

/** Вертикальный разделитель между группами кнопок. */
function Divider() {
  return <span aria-hidden="true" className="w-px h-5 bg-bg-6 shrink-0" />;
}
