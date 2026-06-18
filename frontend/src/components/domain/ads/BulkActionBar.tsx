/**
 * BulkActionBar — floating-панель bulk-действий (канон ads-web.jsx BulkBar).
 *
 * Появляется по центру внизу при выборе ≥1 строки. Стиль: bg-3, 1px bg-7
 * border, анимация входа fbRise. Содержимое: «N выбрано» + Disable (danger,
 * money) + Snooze 1ч (прямая кнопка) + «Очистить выбор».
 *
 * Disable открывает confirm-with-typing на стороне страницы.
 * Presentational: все колбэки приходят снаружи.
 */

import { Ban, Clock, CheckSquare, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/Button";

export interface BulkActionBarProps {
  /** Количество выбранных строк. */
  count: number;
  /** Идёт мутация → блокируем кнопки. */
  isPending?: boolean;

  onDisable: () => void;
  /** Snooze выбранных (минуты). */
  onSnooze: (minutes: number) => void;
  /** Отметить «в работе» (claimed). */
  onMarkClaimed?: () => void;
  /** Hard-delete выбранных из каталога (необратимо). */
  onDelete?: () => void;
  onClear: () => void;
}

export function BulkActionBar({
  count,
  isPending = false,
  onDisable,
  onSnooze,
  onMarkClaimed,
  onDelete,
  onClear,
}: BulkActionBarProps) {
  return (
    <div
      role="toolbar"
      aria-label={`Действия над ${count} выбранными объявлениями`}
      className="fixed bottom-6 left-1/2 -translate-x-1/2 z-[35] flex items-center gap-4 bg-bg-3 border border-[var(--hairline-strong)] rounded-[var(--radius-3)] px-4 py-2.5"
      style={{
        animation: "fbRise var(--dur-base) var(--ease-out)",
        boxShadow: "inset 0 1px 0 rgba(255,255,255,0.04)",
      }}
    >
      <span className="text-[13px] text-bg-11">
        <b className="font-display tabular-nums">{count}</b> выбрано
      </span>

      <span aria-hidden="true" className="w-px h-[22px] bg-[var(--hairline-strong)]" />

      {/* MONEY: Disable — открывает confirm-with-typing на странице */}
      <Button
        variant="danger"
        size="sm"
        leftIcon={<Ban size={14} aria-hidden="true" />}
        onClick={onDisable}
        disabled={isPending}
        aria-label={`Отключить ${count} объявлений`}
      >
        Отключить
      </Button>

      {/* Снуз 1 час — прямая кнопка */}
      <Button
        variant="secondary"
        size="sm"
        leftIcon={<Clock size={14} aria-hidden="true" />}
        onClick={() => onSnooze(60)}
        disabled={isPending}
        aria-label="Снуз на 1 час"
      >
        Снуз 1ч
      </Button>

      {/* Отметить в работе (опционально) */}
      {onMarkClaimed && (
        <Button
          variant="secondary"
          size="sm"
          leftIcon={<CheckSquare size={14} aria-hidden="true" />}
          onClick={onMarkClaimed}
          disabled={isPending}
          aria-label={`Отметить ${count} объявлений в работе`}
        >
          В работе
        </Button>
      )}

      {/* Hard-delete из каталога — открывает confirm-with-typing на странице */}
      {onDelete && (
        <Button
          variant="ghost-danger"
          size="sm"
          leftIcon={<Trash2 size={14} aria-hidden="true" />}
          onClick={onDelete}
          disabled={isPending}
          aria-label={`Удалить ${count} объявлений из базы`}
        >
          Удалить из базы
        </Button>
      )}

      <Button
        variant="ghost"
        size="sm"
        leftIcon={<X size={14} aria-hidden="true" />}
        onClick={onClear}
        disabled={isPending}
        aria-label="Очистить выбор"
      >
        Очистить
      </Button>
    </div>
  );
}
