/**
 * Гео из имени кампании/объявления — единая реализация для web и mini.
 *
 * История: web имел deriveGeo (KNOWN_GEOS + токенизация), mini — собственный
 * упрощённый extractGeo (regex по «| XX |»), которые расходились в результатах
 * («CR2_GH» web находил, mini — нет). Сведено сюда при дедупе (аудит 2026-06-09).
 */

// ISO-2 коды, реально встречающиеся в трафике (расширяемо). Регистр верхний.
export const KNOWN_GEOS = new Set([
  "PT", "BR", "UA", "DE", "IT", "ES", "FR", "NL", "PL", "GB", "GH", "NG",
  "US", "CA", "AU", "MX", "AR", "CL", "CO", "PE", "RO", "CZ", "GR", "TR",
  "IN", "ID", "PH", "TH", "VN", "ZA", "KE", "EG", "MA", "SA", "AE", "KZ",
]);

/**
 * Извлекает 2-буквенное гео из имён (кампания приоритетнее, затем ad/adset).
 * Стратегия: токен (split по разделителям), являющийся известным ISO-2 кодом,
 * ИЛИ начинающийся с пары заглавных известного гео (напр. «GH12» → GH).
 * Фолбэк — первые 2 буквы первого алфавитного токена. Ничего нет → «—».
 */
export function deriveGeoFromNames(...names: Array<string | null | undefined>): string {
  const source = names.filter(Boolean).join(" ");
  const tokens = source.split(/[\s|/_\-.,]+/).filter(Boolean);
  for (const t of tokens) {
    const up = t.toUpperCase();
    if (KNOWN_GEOS.has(up)) return up;
    const head = up.slice(0, 2);
    // «GH12», «UA7» — гео-код с приклеенным числом.
    if (/^[A-Z]{2}\d/.test(up) && KNOWN_GEOS.has(head)) return head;
  }
  // Фолбэк: первые две буквы первого алфавитного токена.
  const firstWord = tokens.find((t) => /[a-zA-Z]/.test(t));
  if (firstWord) {
    const letters = firstWord.replace(/[^a-zA-Z]/g, "").slice(0, 2).toUpperCase();
    if (letters.length === 2) return letters;
  }
  return "—";
}
