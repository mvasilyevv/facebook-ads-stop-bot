/**
 * Pagination — showing X–Y of N + prev/next.
 *
 * Полностью presentational: страница управляет offset снаружи,
 * компонент только отображает диапазон и кнопки навигации.
 */

import { Button } from "@/components/ui/Button";

export interface PaginationProps {
  /** Текущее смещение (0-based). */
  offset: number;
  /** Количество строк на текущей странице (длина полученного среза). */
  pageSize: number;
  /** Общее число строк (из X-Total-Count). null — если бэк не вернул. */
  total: number | null;
  /** Переход к предыдущей странице. */
  onPrev: () => void;
  /** Переход к следующей странице. */
  onNext: () => void;
  /** Дополнительный className для контейнера. */
  className?: string;
}

export function Pagination({
  offset,
  pageSize,
  total,
  onPrev,
  onNext,
  className,
}: PaginationProps) {
  // Вычисляем границы диапазона
  const from = pageSize === 0 ? 0 : offset + 1;
  const to = offset + pageSize;

  const hasPrev = offset > 0;
  const hasNext = total !== null ? to < total : pageSize > 0 && to === offset + pageSize;

  // Не показываем пагинацию если данных меньше страницы
  const multiPage = total !== null ? total > pageSize : pageSize > 0;
  if (!multiPage && offset === 0) return null;

  return (
    <nav
      aria-label="Навигация по страницам"
      className={`flex items-center justify-between font-display text-[11.5px] text-bg-9 tracking-wide ${className ?? ""}`}
    >
      {/* Диапазон строк */}
      <span>
        {pageSize === 0 ? (
          "Нет данных"
        ) : (
          <>
            Показано{" "}
            <span className="text-bg-11">
              {from}–{to}
            </span>
            {total !== null ? (
              <>
                {" "}
                из <span className="text-bg-11">{total}</span>
              </>
            ) : null}
          </>
        )}
      </span>

      {/* Кнопки навигации */}
      <div className="flex gap-2">
        <Button
          variant="secondary"
          size="sm"
          disabled={!hasPrev}
          onClick={onPrev}
          aria-label="Предыдущая страница"
        >
          ← Назад
        </Button>
        <Button
          variant="secondary"
          size="sm"
          disabled={!hasNext}
          onClick={onNext}
          aria-label="Следующая страница"
        >
          Вперёд →
        </Button>
      </div>
    </nav>
  );
}
