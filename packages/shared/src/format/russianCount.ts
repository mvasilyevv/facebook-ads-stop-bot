// Склонение числительных на русском языке.
// Единственная реализация для веба и Telegram Mini App.
// Логика не меняется: скопирована из frontend/src/lib/utils/russianCount.ts.

/**
 * Возвращает подходящую словоформу для числительного.
 *  1 → one  (одна строка)
 * 2–4 → few  (две строки)
 * 5–20, 11–14 → many  (много строк)
 * Отрицательные числа склоняются по модулю.
 */
export function russianCountForm(count: number, one: string, few: string, many: string): string {
  const absolute = Math.abs(count);
  const lastTwo = absolute % 100;
  const last = absolute % 10;
  if (last === 1 && lastTwo !== 11) return one;
  if (last >= 2 && last <= 4 && (lastTwo < 12 || lastTwo > 14)) return few;
  return many;
}

/**
 * Форматирует число с русскими разделителями разрядов и подбирает словоформу.
 * Пример: formatRussianCount(1, "строка", "строки", "строк") → "1 строка"
 *          formatRussianCount(1000, "строка", "строки", "строк") → "1 000 строк"
 */
export function formatRussianCount(count: number, one: string, few: string, many: string): string {
  return `${count.toLocaleString("ru-RU")} ${russianCountForm(count, one, few, many)}`;
}

/**
 * Возвращает true, если число соответствует форме единственного числа
 * (т.е. russianCountForm вернул бы one).
 */
export function russianCountIsOne(count: number): boolean {
  const absolute = Math.abs(count);
  return absolute % 10 === 1 && absolute % 100 !== 11;
}
