import type { Page } from 'playwright';
import type { ScannedAdRow } from './types.js';
import type { HeaderSnapshot, ParserColumnLayout } from './ads-columns.js';
import { buildParserColumnLayout } from './ads-columns.js';
import { humanClick } from './humanizer.js';

type RawAdFields = Record<string, string>;
type BrowserParseRowArgs = {
  layout: ParserColumnLayout[];
};
type RawExtractResult = {
  rows: RawAdFields[];
  // Поля, не дочитанные из-за того что cell.textContent пустой/мусорный — обычно когда
  // Ads Manager асинхронно подгружает значение конкретной ячейки и сейчас в DOM лежит
  // skeleton/spinner вместо данных. Раньше эти случаи кидали exception на весь цикл.
  loadingFields: { fbAdId: string; adName: string; fields: string[] }[];
  // Поля, у которых ячейки физически нет в DOM (виртуализация по горизонтали).
  missingFields: { fbAdId: string; adName: string; fields: string[] }[];
};

export type ParseAdsResult = {
  rows: ScannedAdRow[];
  /** fb_ad_id строк, у которых какие-то метрики не прочитались (loading-spinner или missing cell). */
  partialRowIds: string[];
};

type ParseRowsReader = (page: Page) => Promise<ParseAdsResult>;
type WaitForParsedAdsRowsOptions = {
  timeoutMs?: number;
  pollMs?: number;
  /**
   * Максимально допустимая доля partial-строк, при которой результат считается «хорошим»
   * и возвращается сразу. Если фактическая доля выше — функция продолжает поллить, пока
   * partial не упадёт или не истечёт timeoutMs. Защищает от снепшота, в котором половина
   * метрик ещё в spinner-загрузке. По умолчанию 0.1 (10%).
   */
  maxPartialRatio?: number;
  readRows?: ParseRowsReader;
  isCancelled?: () => boolean;
};

/** Нажать кнопку «Refresh» в Ads Manager. */
export async function refreshTable(page: Page): Promise<boolean> {
  try {
    const container = await page.$('[data-pagelet="AdsRefreshAndPublishButtons"]');
    if (!container) return false;

    const buttons = await container.$$('[role="button"]');
    for (const btn of buttons) {
      const text = await btn.innerText();
      if (text.includes('Обновить') || text.includes('Refresh') || text.includes('обновить')) {
        await humanClick(page, btn);
        return true;
      }
    }
  } catch {
    // Нам важно не ронять весь цикл сканирования из-за недоступной кнопки.
  }
  return false;
}

async function collectVisibleHeaderSnapshots(page: Page): Promise<HeaderSnapshot[]> {
  const headers = await page.evaluate(() => {
    return Array.from(document.querySelectorAll('[data-surface*="table_column_header:"]'))
      .map((el) => {
        const surface = el.getAttribute('data-surface') || '';
        const match = surface.match(/table_column_header:([^/]+)/);
        const rect = el.getBoundingClientRect();
        return {
          surfaceKey: match ? match[1] : '',
          text: (el.textContent || '').replace(/\s+/g, ' ').trim().toLowerCase(),
          left: rect.left,
        };
      })
      .filter((header) => header.surfaceKey || header.text);
  });
  return Array.isArray(headers) ? headers : [];
}

async function extractRawRowsFromPage(
  page: Page,
  args: BrowserParseRowArgs,
): Promise<RawExtractResult> {
  return page.evaluate(({ layout }) => {
    const BUTTON_LABELS = new Set([
      'дублировать', 'редактировать', 'удалить', 'предпросмотр',
      'duplicate', 'edit', 'delete', 'preview',
      'открыть раскрывающееся меню', 'open dropdown menu',
      'выкл.', 'вкл.', 'off', 'on',
      '\u200b',
    ]);
    function normalizedText(value: unknown): string {
      return String(value || '').replace(/\s+/g, ' ').trim();
    }

    function isButtonLabel(text: string): boolean {
      const lower = normalizedText(text).toLowerCase();
      if (!lower) return true;
      if (BUTTON_LABELS.has(lower)) return true;
      if (lower.startsWith('активные объявления:')) return true;
      return false;
    }

    function textNodes(element: Element): string[] {
      const result: string[] = [];
      const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
      let node = walker.nextNode();
      while (node) {
        const text = normalizedText(node.textContent);
        if (text && !isButtonLabel(text)) result.push(text);
        node = walker.nextNode();
      }
      return result;
    }

    function getAdName(cell: Element): string {
      const tooltip = cell.querySelector('[data-tooltip-content]');
      const tooltipText = normalizedText(tooltip?.getAttribute('data-tooltip-content'));
      if (tooltipText && !isButtonLabel(tooltipText)) return tooltipText;

      const spans = cell.querySelectorAll('span._3dfi._3dfj');
      for (const span of spans) {
        const text = normalizedText(span.textContent);
        if (text && !isButtonLabel(text)) return text;
      }

      const texts = textNodes(cell);
      return texts.find((text) => !isButtonLabel(text)) || '';
    }

    function findAdNameCellIndex(cells: Element[]): number {
      for (let index = 0; index < cells.length; index += 1) {
        const text = getAdName(cells[index]);
        if (!text) continue;
        const lower = text.toLowerCase();
        if (BUTTON_LABELS.has(lower)) continue;
        if (/^(используется бюджет кампании|обработка|покупка на сайте)$/i.test(text)) continue;
        return index;
      }
      return -1;
    }

    function getFirstText(cell: Element): string {
      const texts = textNodes(cell);
      return texts[0] || '';
    }

    function getMetricText(cell: Element): string {
      const texts = textNodes(cell).filter((text) => text.length <= 40);
      if (!texts.length) return '';
      const withDigit = texts.find((text) => /\d/.test(text));
      if (withDigit) return withDigit;
      const dash = texts.find((text) => text === '—' || text === '-' || text === '--');
      if (dash) return dash;
      return texts.sort((left, right) => left.length - right.length)[0] || '';
    }

    // Facebook больше не кладет ID строки в data-surface, поэтому достаем objectID из React props/fiber.
    function walkReactValue(value: any, seen: Set<any>, depth: number): string {
      if (!value || depth > 7) return '';
      if (typeof value !== 'object' && typeof value !== 'function') return '';
      if (seen.has(value)) return '';
      seen.add(value);

      const direct = value.objectID || value.typedObjectID;
      if (typeof direct === 'string' && /^\d{10,}$/.test(direct)) return direct;
      if (typeof direct === 'number' && direct > 1000000000) return String(direct);

      const props = value.props;
      if (props && props !== value) {
        const fromProps = walkReactValue(props, seen, depth + 1);
        if (fromProps) return fromProps;
      }

      if (Array.isArray(value)) {
        for (const item of value) {
          const found = walkReactValue(item, seen, depth + 1);
          if (found) return found;
        }
        return '';
      }

      for (const key of Object.keys(value)) {
        if (key === 'return' || key === 'child' || key === 'sibling' || key === 'alternate') continue;
        const found = walkReactValue(value[key], seen, depth + 1);
        if (found) return found;
      }
      return '';
    }

    function getReactObjectId(element: Element): string {
      const nodes = [element, ...element.querySelectorAll('._4lg0, [role="switch"], input[type="checkbox"]')];
      for (const node of nodes) {
        for (const key of Object.getOwnPropertyNames(node)) {
          if (!key.startsWith('__reactProps') && !key.startsWith('__reactFiber')) continue;
          const found = walkReactValue((node as any)[key], new Set(), 0);
          if (found) return found;
        }
      }
      return '';
    }

    function visibleCells(row: Element): Element[] {
      return Array.from(row.querySelectorAll('._4lg0'))
        .filter((cell) => {
          const rect = cell.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0;
        })
        .sort((left, right) => left.getBoundingClientRect().left - right.getBoundingClientRect().left);
    }

    function getToggleAriaChecked(row: Element): string {
      const toggle = row.querySelector('[role="switch"]');
      return normalizedText(toggle?.getAttribute('aria-checked'));
    }

    const nameColumn = layout.find((column) => column.fieldName === 'ad_name');
    if (!nameColumn) {
      throw new Error('Не удалось определить колонку «Название объявления» для парсинга Ads Manager.');
    }

    const result: RawAdFields[] = [];
    const missingFields: { fbAdId: string; adName: string; fields: string[] }[] = [];
    const loadingFields: { fbAdId: string; adName: string; fields: string[] }[] = [];

    // Skeleton-loader Ads Manager: пока конкретная ячейка ждёт данные с бэкенда,
    // Facebook рендерит в ней <div role="progressbar" aria-busy="true"
    // aria-valuetext="Загрузка…">. Раньше парсер видел пустой textContent и кидал
    // exception на весь цикл — теперь мы помечаем такую строку как partial.
    function isCellLoading(cell: Element): boolean {
      if (cell.querySelector('[aria-busy="true"]')) return true;
      if (cell.querySelector('[role="progressbar"]')) return true;
      return false;
    }

    const rows = Array.from(document.querySelectorAll('._1gda._2djg'));

    for (const row of rows) {
      const rowRect = row.getBoundingClientRect();
      if (rowRect.width <= 0 || rowRect.height <= 0) continue;

      const cells = visibleCells(row);
      if (cells.length < 3) continue;

      const nameCellIndex = findAdNameCellIndex(cells);
      if (nameCellIndex < 0) continue;

      const nameCell = cells[nameCellIndex];
      if (!nameCell) continue;

      const adName = getAdName(nameCell);
      const fbAdId = getReactObjectId(row);
      if (!adName || !fbAdId) continue;

      const fields: RawAdFields = {
        _row_id: fbAdId,
        _toggle_aria_checked: getToggleAriaChecked(row),
        ad_name: adName,
      };

      // Получаем координаты left для всех ячеек в строке, чтобы повысить производительность сопоставления
      const cellsWithLeft = cells.map((cell) => ({
        cell,
        left: cell.getBoundingClientRect().left,
      }));

      const rowMissing: string[] = [];
      const rowLoading: string[] = [];
      for (const column of layout) {
        let cell: Element | undefined = undefined;

        // Если задана координата left колонки, сопоставляем ячейку по физической координате
        if (column.left !== undefined && typeof column.left === 'number') {
          let minDiff = Infinity;
          let bestCell: Element | undefined = undefined;

          for (const item of cellsWithLeft) {
            const diff = Math.abs(item.left - column.left);
            // Допуск 15px, так как минимальная ширина колонки в Ads Manager 40px
            if (diff < 15 && diff < minDiff) {
              minDiff = diff;
              bestCell = item.cell;
            }
          }
          cell = bestCell;
        }

        // Резервный расчет по относительному индексу (если left отсутствует или ячейка не нашлась по координате)
        if (!cell) {
          const relativeIndex = column.headerIndex - nameColumn.headerIndex;
          const cellIndex = nameCellIndex + relativeIndex;
          cell = cells[cellIndex];
        }

        if (!cell) {
          rowMissing.push(column.title);
          continue;
        }

        // Ячейка нашлась, но Facebook ещё не подгрузил значение — внутри spinner.
        // Не пишем пустую строку (на которой потом сломается проверка), пишем "" и
        // отмечаем строку как partial: observer сохранит остальные поля и пометит,
        // что эту строку нужно дочитать в следующем цикле.
        if (column.valueKind === 'metric' && isCellLoading(cell)) {
          rowLoading.push(column.title);
          fields[column.fieldName] = '';
          continue;
        }

        fields[column.fieldName] = column.valueKind === 'metric'
          ? getMetricText(cell)
          : column.valueKind === 'name'
            ? getAdName(cell)
            : getFirstText(cell);
      }

      if (rowMissing.length > 0) {
        missingFields.push({ fbAdId, adName, fields: rowMissing });
      }
      if (rowLoading.length > 0) {
        loadingFields.push({ fbAdId, adName, fields: rowLoading });
      }

      result.push(fields);
    }

    return { rows: result, missingFields, loadingFields };
  }, args);
}

/** Распарсить все видимые строки из текущей страницы.
 *
 * Возвращает rows + partialRowIds. Партиал — это строки, у которых:
 *  - часть метрик пока в spinner-загрузке (Facebook ещё не отдал данные конкретно для этого объявления);
 *  - или часть ячеек не нашлась по координате/индексу (горизонтальная виртуализация).
 *
 * Эти случаи НЕ катастрофические — мы возвращаем строку с тем что прочиталось, observer
 * пишет snapshot, оценивает правила по доступным колонкам и помечает что часть данных
 * будет дочитана в следующем цикле. Throw остаётся ТОЛЬКО для одной катастрофы: в хедере
 * таблицы нет обязательных колонок (пользователь сам сломал layout Ads Manager).
 */
export async function parseAdsFromPage(page: Page): Promise<ParseAdsResult> {
  const headers = await collectVisibleHeaderSnapshots(page);
  const { layout, missingColumns } = buildParserColumnLayout(headers);
  if (missingColumns.length > 0) {
    throw new Error(
      `Не удалось распарсить таблицу Ads Manager: отсутствуют обязательные колонки: ${missingColumns.join(', ')}.`,
    );
  }

  const rawResult = await extractRawRowsFromPage(page, { layout });
  if (!rawResult || !Array.isArray(rawResult.rows)) return { rows: [], partialRowIds: [] };

  // Накапливаем fb_ad_id строк, которые мы не дочитали полностью.
  const partialSet = new Set<string>();
  for (const item of rawResult.loadingFields) {
    if (item.fbAdId) partialSet.add(item.fbAdId);
  }
  for (const item of rawResult.missingFields) {
    if (item.fbAdId) partialSet.add(item.fbAdId);
  }

  const rows = rawResult.rows
    .map((fields) => buildRowFromFields(fields))
    .filter((row): row is ScannedAdRow => row !== null);

  return { rows, partialRowIds: Array.from(partialSet) };
}

/** Дождаться, пока Meta вернет строки после краткого пустого состояния таблицы.
 *
 * Adaptive wait: возвращает результат СРАЗУ если доля partial-строк низкая (< maxPartialRatio).
 * Если partial много (Facebook ещё подгружает метрики для большинства строк), продолжает
 * поллить страницу до тех пор пока:
 *   а) доля partial не упадёт ниже порога — возвращаем,
 *   б) не истечёт timeoutMs — возвращаем best-so-far результат (с наименьшим partial).
 *
 * Это защищает от ситуации, когда мы успели прочитать таблицу в плохой момент: spinner'ы
 * в большинстве ячеек дают snapshot с почти-пустыми метриками, по которому правила
 * не сработают. Ждём 1-5 секунд — Facebook успевает дозаполнить, snapshot становится
 * репрезентативным.
 */
export async function waitForParsedAdsRows(
  page: Page,
  options: WaitForParsedAdsRowsOptions = {},
): Promise<ParseAdsResult> {
  const timeoutMs = options.timeoutMs ?? 10_000;
  const pollMs = options.pollMs ?? 500;
  const maxPartialRatio = options.maxPartialRatio ?? 0.1;
  const readRows = options.readRows ?? parseAdsFromPage;
  const deadline = Date.now() + timeoutMs;
  let lastError: unknown = null;
  // Лучший результат на случай timeout: тот, где доля partial минимальная.
  let bestResult: ParseAdsResult | null = null;
  let bestPartialCount = Infinity;

  while (true) {
    if (options.isCancelled?.()) {
      return bestResult ?? { rows: [], partialRowIds: [] };
    }

    try {
      const result = await readRows(page);
      if (result.rows.length > 0) {
        const partialRatio = result.partialRowIds.length / result.rows.length;
        // Хороший результат — возвращаем немедленно.
        if (partialRatio <= maxPartialRatio) return result;
        // Иначе запоминаем как fallback если он лучше предыдущего.
        if (result.partialRowIds.length < bestPartialCount) {
          bestResult = result;
          bestPartialCount = result.partialRowIds.length;
        }
      }
    } catch (err) {
      // Сохраняем последнюю ошибку (например, отсутствуют обязательные колонки в хедере)
      lastError = err;
    }

    if (options.isCancelled?.()) {
      return bestResult ?? { rows: [], partialRowIds: [] };
    }

    const remainingMs = deadline - Date.now();
    if (remainingMs <= 0) {
      // Timeout: отдаём то что собрали (лучшее по partial), либо ошибку, либо пусто.
      if (bestResult) return bestResult;
      if (lastError !== null) throw lastError;
      return { rows: [], partialRowIds: [] };
    }

    await sleep(Math.min(pollMs, remainingMs));
  }
}

function buildRowFromFields(fields: RawAdFields): ScannedAdRow | null {
  const fbAdId = cleanCell(fields['_row_id'] || '');
  const adName = cleanCell(fields['ad_name'] || '');
  if (!fbAdId || !adName || fbAdId.length < 10) return null;

  return {
    fb_ad_id: fbAdId,
    campaign_name: cleanCell(fields['campaign_name'] || ''),
    adset_name: cleanCell(fields['adset_name'] || ''),
    ad_name: adName,
    delivery_status: detectLogicalDeliveryStatus(
      fields['delivery_status'] || '',
      fields['_toggle_aria_checked'] || '',
    ),
    spend: parseMoney(fields['spend'] || '0'),
    budget: cleanCell(fields['budget'] || ''),
    reach: parseIntValue(fields['reach']),
    impressions: parseIntValue(fields['impressions']),
    clicks: parseIntValue(fields['clicks']),
    cpc: parseMoneyOrNull(fields['cpc']),
    ctr: parseDecimalOrNull(fields['ctr']),
    outbound_clicks: parseIntValue(fields['outbound_clicks']),
    outbound_ctr: parseDecimalOrNull(fields['outbound_ctr']),
    landing_page_views: parseIntValue(fields['landing_page_views']),
    cost_per_landing_page_view: parseMoneyOrNull(fields['cost_per_landing_page_view']),
    cost_per_result: parseMoneyOrNull(fields['cost_per_result']),
    cpm: parseMoneyOrNull(fields['cpm']),
    frequency: parseDecimalOrNull(fields['frequency']),
    leads: parseIntValue(fields['leads']),
    cost_per_lead: parseMoneyOrNull(fields['cost_per_lead']),
    registrations: parseIntValue(fields['registrations']),
    cost_per_registration: parseMoneyOrNull(fields['cost_per_registration']),
    deposits: parseIntValue(fields['deposits']),
    resolved_offer_code: null,
  };
}

function cleanCell(text?: string): string {
  const value = (text || '').replace(/\u200b/g, '').replace(/\s+/g, ' ').trim();
  if (!value || value === '—' || value === '-') return '';
  const activeAdsMatch = value.match(/^(\d+)\s*(?:Active ads|Активные объявления):\s*\d+$/i);
  if (activeAdsMatch) return activeAdsMatch[1];
  return value;
}

export function detectLogicalDeliveryStatus(text?: string, toggleAriaChecked?: string): string {
  const toggleValue = cleanCell(toggleAriaChecked);
  if (toggleValue === 'false') {
    return 'OFF';
  }
  return detectDeliveryStatus(text);
}

function detectDeliveryStatus(text?: string): string {
  const value = cleanCell(text);
  if (!value) return 'UNKNOWN';
  const lower = value.toLowerCase();

  // Нормализуем локализованные статусы Ads Manager в стабильные коды,
  // чтобы Python-воркеры не зависели от языка текущего профиля Vision.
  if (
    lower.includes('off') ||
    lower.includes('выключ') ||
    lower.includes('вимкнен') ||
    lower.includes('disabled')
  ) {
    return 'OFF';
  }

  if (
    lower.includes('not delivering') ||
    lower.includes('не достав') ||
    lower.includes('не показ') ||
    lower.includes('показ кампани') ||
    lower.includes('показ кампан') ||
    lower.includes('delivery stopped')
  ) {
    return 'NOT_DELIVERING';
  }

  if (lower.includes('active') || lower.includes('актив')) {
    return 'ACTIVE';
  }

  if (
    lower.includes('processing') ||
    lower.includes('обработ') ||
    lower.includes('обробк')
  ) {
    return 'PROCESSING';
  }

  if (
    lower.includes('review') ||
    lower.includes('рассмотр') ||
    lower.includes('розгляд')
  ) {
    return 'IN_REVIEW';
  }

  return value;
}

export function parseIntValue(text?: string): number {
  const normalized = normalizeNumericText(text);
  if (!normalized) return 0;
  return Number.parseInt(normalized, 10) || 0;
}

export function parseMoney(text?: string): string {
  return normalizeNumericText(text) ?? '0';
}

export function parseMoneyOrNull(text?: string): string | null {
  const value = cleanCell(text);
  if (!value || value === '--') return null;
  return normalizeNumericText(value);
}

export function parseDecimalOrNull(text?: string): string | null {
  const value = cleanCell(text);
  if (!value || value === '--') return null;
  return normalizeNumericText(value);
}

export function normalizeNumericText(text?: string): string | null {
  const value = cleanCell(text);
  if (!value) return null;

  const token = value
    .replace(/[\u00a0\u202f]/g, ' ')
    .match(/-?\d[\d\s.,']*/)?.[0]
    ?.trim();
  if (!token) return null;

  const negative = token.startsWith('-');
  let raw = token
    .replace(/-/g, '')
    .replace(/[\s']/g, '')
    .replace(/^[.,]+|[.,]+$/g, '');
  if (!raw || !/\d/.test(raw)) return null;

  const commaIndex = raw.lastIndexOf(',');
  const dotIndex = raw.lastIndexOf('.');
  let decimalSeparator = '';

  if (commaIndex >= 0 && dotIndex >= 0) {
    decimalSeparator = commaIndex > dotIndex ? ',' : '.';
  } else if (commaIndex >= 0 || dotIndex >= 0) {
    const separator = commaIndex >= 0 ? ',' : '.';
    const parts = raw.split(separator);
    const fraction = parts[parts.length - 1] || '';
    const integer = parts.slice(0, -1).join('');
    if (parts.length === 2 && fraction.length > 0 && fraction.length <= 2) {
      decimalSeparator = separator;
    } else if (parts.length === 2 && fraction.length === 3 && integer.length > 3) {
      decimalSeparator = separator;
    }
  }

  if (decimalSeparator) {
    const separatorIndex = raw.lastIndexOf(decimalSeparator);
    const integerPart = raw.slice(0, separatorIndex).replace(/[.,]/g, '');
    const fractionPart = raw.slice(separatorIndex + 1).replace(/[.,]/g, '');
    raw = `${integerPart}.${fractionPart}`;
  } else {
    raw = raw.replace(/[.,]/g, '');
  }

  if (!raw || raw === '.') return null;
  return `${negative ? '-' : ''}${raw}`;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// --- Helpers для детекции STALE_DATA и partial-строк ---

const EMPTY_METRIC_PLACEHOLDERS = new Set(['', '—', '-', '–', 'N/A']);

function isEmptyMetric(value: string | number | null | undefined): boolean {
  if (value === null || value === undefined) return true;
  if (typeof value === 'number') return value === 0;
  return EMPTY_METRIC_PLACEHOLDERS.has(value.trim());
}

/**
 * Строки, у которых все критические метрики (impressions/spend/cpm/cpc/ctr) пустые.
 * Используется для детекции STALE_DATA в observer.
 */
export function countEmptyMetricsRows(rows: ScannedAdRow[]): number {
  let count = 0;
  for (const row of rows) {
    const allEmpty =
      isEmptyMetric(row.impressions) &&
      isEmptyMetric(row.spend) &&
      isEmptyMetric(row.cpm) &&
      isEmptyMetric(row.cpc) &&
      isEmptyMetric(row.ctr);
    if (allEmpty) count += 1;
  }
  return count;
}

/**
 * fb_ad_id строк, у которых пустые обязательные текстовые поля
 * (ad_name / campaign_name) — индикатор, что парсер не дочитал ячейки.
 */
export function findPartialRows(rows: ScannedAdRow[]): string[] {
  const partial: string[] = [];
  for (const row of rows) {
    if (!row.fb_ad_id) continue;
    if (!row.ad_name || !row.campaign_name) {
      partial.push(row.fb_ad_id);
    }
  }
  return partial;
}

/**
 * Возвращает реальное количество объявлений в таблице Ads Manager из footer-строки
 * «Результаты, число объявлений: N». Это источник истины: позволяет понять, что
 * наш скан недосканил (allRows.length < total). Возвращает null, если строка
 * не нашлась/не распарсилась.
 */
export async function getAdsTableTotalCount(page: Page): Promise<number | null> {
  try {
    const value = await page.evaluate(() => {
      // У footer-строки уникальный data-surface */total_count, она единственная такая.
      const el = document.querySelector('[data-surface*="total_count"]');
      if (!el) return null;
      const text = (el.textContent || '').replace(/\s+/g, ' ');
      // Разные локали: «Результаты, число объявлений: 42», "Results, number of ads: 42",
      // "Total ad count: 42" и т.п. Берём первое число длиной 1-6 в строке.
      const match = text.match(/(\d{1,6})/);
      return match ? Number(match[1]) : null;
    });
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return null;
    return value;
  } catch {
    return null;
  }
}
