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

// ─── Выбор страны по имени (ISO-2 ↔ русское название) ─────────────────────────
//
// Гео в Meta API задаётся ISO-2 кодом («GH»), но в UI криптичный код легко спутать
// (GH=Гана vs GE=Грузия — опечатка в одну букву, незаметная без названия). Поэтому
// показываем русское название, а храним/шлём код. Имена берём из Intl.DisplayNames
// (не держим 250 строк руками), флаг — из кода, список кодов — полный ISO-3166-1.

/** Полный список ISO-3166-1 alpha-2 кодов (официально назначенные). */
export const ALL_ISO_COUNTRY_CODES: string[] = [
  "AD", "AE", "AF", "AG", "AI", "AL", "AM", "AO", "AQ", "AR", "AS", "AT", "AU", "AW", "AX", "AZ",
  "BA", "BB", "BD", "BE", "BF", "BG", "BH", "BI", "BJ", "BL", "BM", "BN", "BO", "BQ", "BR", "BS",
  "BT", "BV", "BW", "BY", "BZ", "CA", "CC", "CD", "CF", "CG", "CH", "CI", "CK", "CL", "CM", "CN",
  "CO", "CR", "CU", "CV", "CW", "CX", "CY", "CZ", "DE", "DJ", "DK", "DM", "DO", "DZ", "EC", "EE",
  "EG", "EH", "ER", "ES", "ET", "FI", "FJ", "FK", "FM", "FO", "FR", "GA", "GB", "GD", "GE", "GF",
  "GG", "GH", "GI", "GL", "GM", "GN", "GP", "GQ", "GR", "GS", "GT", "GU", "GW", "GY", "HK", "HM",
  "HN", "HR", "HT", "HU", "ID", "IE", "IL", "IM", "IN", "IO", "IQ", "IR", "IS", "IT", "JE", "JM",
  "JO", "JP", "KE", "KG", "KH", "KI", "KM", "KN", "KP", "KR", "KW", "KY", "KZ", "LA", "LB", "LC",
  "LI", "LK", "LR", "LS", "LT", "LU", "LV", "LY", "MA", "MC", "MD", "ME", "MF", "MG", "MH", "MK",
  "ML", "MM", "MN", "MO", "MP", "MQ", "MR", "MS", "MT", "MU", "MV", "MW", "MX", "MY", "MZ", "NA",
  "NC", "NE", "NF", "NG", "NI", "NL", "NO", "NP", "NR", "NU", "NZ", "OM", "PA", "PE", "PF", "PG",
  "PH", "PK", "PL", "PM", "PN", "PR", "PS", "PT", "PW", "PY", "QA", "RE", "RO", "RS", "RU", "RW",
  "SA", "SB", "SC", "SD", "SE", "SG", "SH", "SI", "SJ", "SK", "SL", "SM", "SN", "SO", "SR", "SS",
  "ST", "SV", "SX", "SY", "SZ", "TC", "TD", "TF", "TG", "TH", "TJ", "TK", "TL", "TM", "TN", "TO",
  "TR", "TT", "TV", "TW", "TZ", "UA", "UG", "UM", "US", "UY", "UZ", "VA", "VC", "VE", "VG", "VI",
  "VN", "VU", "WF", "WS", "YE", "YT", "ZA", "ZM", "ZW",
];

const _isoCodeSet = new Set(ALL_ISO_COUNTRY_CODES);

/** Опция страны для UI: код, русское имя, emoji-флаг. */
export interface CountryOption {
  code: string;
  name: string;
  flag: string;
}

// Ленивая инициализация Intl.DisplayNames('ru'). null — окружение без ICU-данных.
let _regionNames: Intl.DisplayNames | null | undefined;
function regionNamesRu(): Intl.DisplayNames | null {
  if (_regionNames !== undefined) return _regionNames;
  try {
    _regionNames = new Intl.DisplayNames(["ru"], { type: "region" });
  } catch {
    _regionNames = null;
  }
  return _regionNames;
}

/** Русское название страны по ISO-2 коду. Фолбэк — сам код (верхний регистр). */
export function countryNameRu(code: string): string {
  const up = code.trim().toUpperCase();
  // Неизвестный код не доверяем Intl (он резолвит ZZ → «неизвестный регион»).
  if (!_isoCodeSet.has(up)) return up;
  const dn = regionNamesRu();
  if (dn) {
    try {
      const name = dn.of(up);
      if (name && name !== up) return name;
    } catch {
      // невалидный код — отдадим сам код ниже
    }
  }
  return up;
}

/** Emoji-флаг страны из ISO-2 кода (regional indicator symbols). */
export function countryFlagEmoji(code: string): string {
  const up = code.trim().toUpperCase();
  if (!/^[A-Z]{2}$/.test(up)) return "🏳️";
  const base = 0x1f1e6; // 🇦
  return String.fromCodePoint(base + up.charCodeAt(0) - 65, base + up.charCodeAt(1) - 65);
}

/** Валиден ли ISO-2 код (есть в официальном списке). */
export function isValidCountryCode(code: string): boolean {
  return _isoCodeSet.has(code.trim().toUpperCase());
}

// Полный список опций кешируем (имена через Intl — считаем один раз).
let _allOptions: CountryOption[] | null = null;
function allCountryOptions(): CountryOption[] {
  if (_allOptions) return _allOptions;
  _allOptions = ALL_ISO_COUNTRY_CODES.map((code) => ({
    code,
    name: countryNameRu(code),
    flag: countryFlagEmoji(code),
  }));
  return _allOptions;
}

/**
 * Поиск стран по подстроке — совпадение по русскому имени ИЛИ по ISO-2 коду
 * (регистронезависимо). Пустой запрос → все (отсортировано по имени). exclude —
 * уже выбранные коды (исключаются). limit — максимум опций в выдаче.
 */
export function searchCountries(
  query: string,
  opts?: { exclude?: string[]; limit?: number },
): CountryOption[] {
  const q = query.trim().toLowerCase();
  const exclude = new Set((opts?.exclude ?? []).map((c) => c.toUpperCase()));
  const limit = opts?.limit ?? 50;

  const pool = allCountryOptions().filter((c) => !exclude.has(c.code));
  const matched = q
    ? pool.filter((c) => c.name.toLowerCase().includes(q) || c.code.toLowerCase().includes(q))
    : pool;

  // Релевантность: точный код → имя/код начинается с запроса → остальное; затем по имени.
  const rank = (c: CountryOption): number => {
    if (c.code.toLowerCase() === q) return 0;
    if (c.name.toLowerCase().startsWith(q) || c.code.toLowerCase().startsWith(q)) return 1;
    return 2;
  };
  const sorted = [...matched].sort(
    (a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name, "ru"),
  );
  return sorted.slice(0, limit);
}
