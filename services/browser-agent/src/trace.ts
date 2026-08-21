// Структурный след money-пути браузерного слоя.
//
// Зачем: через browser-agent идут все мутации Meta, и при разборе живого
// инцидента 19.08.2026 в его логах за два часа не нашлось ни одной строки —
// восстановить, что именно ушло в кабинет, было нечем (#174). Здесь пишется
// не дамп, а структура события: кто, куда, чем закончилось и на какой странице.
//
// Границы (инвариант проекта): наружу не уходят токен, куки, содержимое
// страницы и сырые тексты исключений. Причина отказа пишется кодом, а не
// сообщением; адрес — без query и fragment, потому что именно там живут
// access_token и параметры запроса.

import { TOKEN_PATTERN_SOURCE } from './meta-api/client.js';

/**
 * Исход внешней операции глазами браузерного слоя.
 *
 * `CONFIRMED` — запрос ушёл и Meta ответила: исход известен, каким бы он ни был.
 * `REJECTED` — доказано, что наружу ничего не ушло.
 * `UNKNOWN` — внешняя граница пересечена, а ответа нет.
 *
 * Браузерный слой не судит о бизнес-успехе: `CONFIRMED` здесь означает только
 * что ответ получен, а не что кампания создана.
 */
export type MoneyPathOutcome = 'CONFIRMED' | 'REJECTED' | 'UNKNOWN';

export interface MetaCallTrace {
  /** Имя RPC, а не внутреннего обработчика. */
  rpc: string;
  /** Кабинет операции; пустая строка, если вызов не про кабинет. */
  act: string;
  method: string;
  /** Путь Graph без query и fragment. */
  endpoint: string;
  /** Money-вызов или обычное чтение. */
  money: boolean;
  session: string;
  role: string;
  outcome: MoneyPathOutcome;
  durationMs: number;
  /** HTTP-код ответа Meta; отсутствует, когда ответа не было. */
  statusCode?: number;
  /** Код причины отказа — из словаря причин, не текст исключения. */
  reason?: string;
}

export interface PageNavTrace {
  session: string;
  role: string;
  act: string;
  kind: 'goto' | 'reload';
  /** origin и путь целевого адреса, без query и fragment. */
  url: string;
  /** Кто навигировал: operation, heal, scan. */
  by: string;
}

const TOKEN_PATTERN = new RegExp(TOKEN_PATTERN_SOURCE, 'g');

function redactToken(value: string): string {
  return value.replace(TOKEN_PATTERN, '<token>');
}

/**
 * Путь Graph-вызова без query и fragment.
 *
 * Query отрезается целиком, а не выборочно: перечень безопасных параметров
 * пришлось бы держать в согласии с Meta, и первый же новый параметр с токеном
 * утёк бы молча.
 */
export function traceSafeEndpoint(raw: string): string {
  const value = String(raw ?? '');
  const cut = value.search(/[?#]/);
  return redactToken(cut === -1 ? value : value.slice(0, cut));
}

/** Адрес страницы: origin и путь, без query и fragment. */
export function traceSafeUrl(raw: string): string {
  const value = String(raw ?? '');
  try {
    const parsed = new URL(value);
    // У схем без хоста (about:, data:) origin равен строке "null" — тогда
    // адрес собирается из схемы, иначе в лог уходит «nullblank».
    const head = parsed.origin === 'null' ? parsed.protocol : parsed.origin;
    return redactToken(`${head}${parsed.pathname}`);
  } catch {
    return traceSafeEndpoint(value);
  }
}

function emit(event: string, payload: Record<string, unknown>): void {
  const line: Record<string, unknown> = { evt: event, ts: new Date().toISOString() };
  for (const [key, value] of Object.entries(payload)) {
    if (value !== undefined) line[key] = value;
  }
  console.log(`[trace] ${JSON.stringify(line)}`);
}

/** Запись о контролируемом вызове Meta. Ровно одна на вызов. */
export function traceMetaCall(record: MetaCallTrace): void {
  emit('meta_call', {
    rpc: record.rpc,
    act: record.act,
    method: record.method,
    endpoint: traceSafeEndpoint(record.endpoint),
    money: record.money,
    session: record.session,
    role: record.role,
    outcome: record.outcome,
    duration_ms: record.durationMs,
    status_code: record.statusCode,
    reason: record.reason,
  });
}

/** Запись о навигации страницы кабинета: именно навигация убила залив 19.08. */
export function tracePageNav(record: PageNavTrace): void {
  emit('page_nav', {
    session: record.session,
    role: record.role,
    act: record.act,
    kind: record.kind,
    url: traceSafeUrl(record.url),
    by: record.by,
  });
}
