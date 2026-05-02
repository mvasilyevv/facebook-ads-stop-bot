/**
 * Общие утилиты для форматирования дат и времени.
 */

const TIME_FORMAT_OPTIONS = {
  day: '2-digit',
  month: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
};

/** Форматирует timestamp в "ДД.ММ, ЧЧ:ММ" */
export function formatTime(ts) {
  if (!ts) return '---';
  try {
    return new Date(ts).toLocaleString('ru-RU', TIME_FORMAT_OPTIONS);
  } catch {
    return '---';
  }
}

/** Относительное время: "2 мин назад", "1 ч назад" */
export function timeAgo(ts) {
  if (!ts) return '';
  const diffMs = Date.now() - new Date(ts).getTime();
  if (diffMs < 0) return 'только что';
  const minutes = Math.floor(diffMs / 60_000);
  if (minutes < 1) return 'только что';
  if (minutes < 60) return `${minutes} мин назад`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} ч назад`;
  const days = Math.floor(hours / 24);
  return `${days} дн назад`;
}
