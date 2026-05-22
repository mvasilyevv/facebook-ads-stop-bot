"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.refreshTable = refreshTable;
exports.parseAdsFromPage = parseAdsFromPage;
exports.waitForParsedAdsRows = waitForParsedAdsRows;
exports.detectLogicalDeliveryStatus = detectLogicalDeliveryStatus;
exports.parseIntValue = parseIntValue;
exports.parseMoney = parseMoney;
exports.parseMoneyOrNull = parseMoneyOrNull;
exports.parseDecimalOrNull = parseDecimalOrNull;
exports.normalizeNumericText = normalizeNumericText;
exports.countEmptyMetricsRows = countEmptyMetricsRows;
exports.findPartialRows = findPartialRows;
const ads_columns_js_1 = require("./ads-columns.js");
const humanizer_js_1 = require("./humanizer.js");
/** Нажать кнопку «Refresh» в Ads Manager. */
async function refreshTable(page) {
    try {
        const container = await page.$('[data-pagelet="AdsRefreshAndPublishButtons"]');
        if (!container)
            return false;
        const buttons = await container.$$('[role="button"]');
        for (const btn of buttons) {
            const text = await btn.innerText();
            if (text.includes('Обновить') || text.includes('Refresh') || text.includes('обновить')) {
                await (0, humanizer_js_1.humanClick)(page, btn);
                return true;
            }
        }
    }
    catch {
        // Нам важно не ронять весь цикл сканирования из-за недоступной кнопки.
    }
    return false;
}
async function collectVisibleHeaderSnapshots(page) {
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
async function extractRawRowsFromPage(page, args) {
    return page.evaluate(({ layout }) => {
        const BUTTON_LABELS = new Set([
            'дублировать', 'редактировать', 'удалить', 'предпросмотр',
            'duplicate', 'edit', 'delete', 'preview',
            'открыть раскрывающееся меню', 'open dropdown menu',
            'выкл.', 'вкл.', 'off', 'on',
            '\u200b',
        ]);
        function normalizedText(value) {
            return String(value || '').replace(/\s+/g, ' ').trim();
        }
        function isButtonLabel(text) {
            const lower = normalizedText(text).toLowerCase();
            if (!lower)
                return true;
            if (BUTTON_LABELS.has(lower))
                return true;
            if (lower.startsWith('активные объявления:'))
                return true;
            return false;
        }
        function textNodes(element) {
            const result = [];
            const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
            let node = walker.nextNode();
            while (node) {
                const text = normalizedText(node.textContent);
                if (text && !isButtonLabel(text))
                    result.push(text);
                node = walker.nextNode();
            }
            return result;
        }
        function getAdName(cell) {
            const tooltip = cell.querySelector('[data-tooltip-content]');
            const tooltipText = normalizedText(tooltip?.getAttribute('data-tooltip-content'));
            if (tooltipText && !isButtonLabel(tooltipText))
                return tooltipText;
            const spans = cell.querySelectorAll('span._3dfi._3dfj');
            for (const span of spans) {
                const text = normalizedText(span.textContent);
                if (text && !isButtonLabel(text))
                    return text;
            }
            const texts = textNodes(cell);
            return texts.find((text) => !isButtonLabel(text)) || '';
        }
        function findAdNameCellIndex(cells) {
            for (let index = 0; index < cells.length; index += 1) {
                const text = getAdName(cells[index]);
                if (!text)
                    continue;
                const lower = text.toLowerCase();
                if (BUTTON_LABELS.has(lower))
                    continue;
                if (/^(используется бюджет кампании|обработка|покупка на сайте)$/i.test(text))
                    continue;
                return index;
            }
            return -1;
        }
        function getFirstText(cell) {
            const texts = textNodes(cell);
            return texts[0] || '';
        }
        function getMetricText(cell) {
            const texts = textNodes(cell).filter((text) => text.length <= 40);
            if (!texts.length)
                return '';
            const withDigit = texts.find((text) => /\d/.test(text));
            if (withDigit)
                return withDigit;
            const dash = texts.find((text) => text === '—' || text === '-' || text === '--');
            if (dash)
                return dash;
            return texts.sort((left, right) => left.length - right.length)[0] || '';
        }
        // Facebook больше не кладет ID строки в data-surface, поэтому достаем objectID из React props/fiber.
        function walkReactValue(value, seen, depth) {
            if (!value || depth > 7)
                return '';
            if (typeof value !== 'object' && typeof value !== 'function')
                return '';
            if (seen.has(value))
                return '';
            seen.add(value);
            const direct = value.objectID || value.typedObjectID;
            if (typeof direct === 'string' && /^\d{10,}$/.test(direct))
                return direct;
            if (typeof direct === 'number' && direct > 1000000000)
                return String(direct);
            const props = value.props;
            if (props && props !== value) {
                const fromProps = walkReactValue(props, seen, depth + 1);
                if (fromProps)
                    return fromProps;
            }
            if (Array.isArray(value)) {
                for (const item of value) {
                    const found = walkReactValue(item, seen, depth + 1);
                    if (found)
                        return found;
                }
                return '';
            }
            for (const key of Object.keys(value)) {
                if (key === 'return' || key === 'child' || key === 'sibling' || key === 'alternate')
                    continue;
                const found = walkReactValue(value[key], seen, depth + 1);
                if (found)
                    return found;
            }
            return '';
        }
        function getReactObjectId(element) {
            const nodes = [element, ...element.querySelectorAll('._4lg0, [role="switch"], input[type="checkbox"]')];
            for (const node of nodes) {
                for (const key of Object.getOwnPropertyNames(node)) {
                    if (!key.startsWith('__reactProps') && !key.startsWith('__reactFiber'))
                        continue;
                    const found = walkReactValue(node[key], new Set(), 0);
                    if (found)
                        return found;
                }
            }
            return '';
        }
        function visibleCells(row) {
            return Array.from(row.querySelectorAll('._4lg0'))
                .filter((cell) => {
                const rect = cell.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            })
                .sort((left, right) => left.getBoundingClientRect().left - right.getBoundingClientRect().left);
        }
        function getToggleAriaChecked(row) {
            const toggle = row.querySelector('[role="switch"]');
            return normalizedText(toggle?.getAttribute('aria-checked'));
        }
        const nameColumn = layout.find((column) => column.fieldName === 'ad_name');
        if (!nameColumn) {
            throw new Error('Не удалось определить колонку «Название объявления» для парсинга Ads Manager.');
        }
        const result = [];
        const missingFields = [];
        const rows = Array.from(document.querySelectorAll('._1gda._2djg'));
        for (const row of rows) {
            const rowRect = row.getBoundingClientRect();
            if (rowRect.width <= 0 || rowRect.height <= 0)
                continue;
            const cells = visibleCells(row);
            if (cells.length < 3)
                continue;
            const nameCellIndex = findAdNameCellIndex(cells);
            if (nameCellIndex < 0)
                continue;
            const nameCell = cells[nameCellIndex];
            if (!nameCell)
                continue;
            const adName = getAdName(nameCell);
            const fbAdId = getReactObjectId(row);
            if (!adName || !fbAdId)
                continue;
            const fields = {
                _row_id: fbAdId,
                _toggle_aria_checked: getToggleAriaChecked(row),
                ad_name: adName,
            };
            // Получаем координаты left для всех ячеек в строке, чтобы повысить производительность сопоставления
            const cellsWithLeft = cells.map((cell) => ({
                cell,
                left: cell.getBoundingClientRect().left,
            }));
            const rowMissing = [];
            for (const column of layout) {
                let cell = undefined;
                // Если задана координата left колонки, сопоставляем ячейку по физической координате
                if (column.left !== undefined && typeof column.left === 'number') {
                    let minDiff = Infinity;
                    let bestCell = undefined;
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
                fields[column.fieldName] = column.valueKind === 'metric'
                    ? getMetricText(cell)
                    : column.valueKind === 'name'
                        ? getAdName(cell)
                        : getFirstText(cell);
            }
            if (rowMissing.length > 0) {
                missingFields.push({ fbAdId, adName, fields: rowMissing });
            }
            result.push(fields);
        }
        return { rows: result, missingFields };
    }, args);
}
/** Распарсить все видимые строки из текущей страницы. */
async function parseAdsFromPage(page) {
    const headers = await collectVisibleHeaderSnapshots(page);
    const { layout, missingColumns } = (0, ads_columns_js_1.buildParserColumnLayout)(headers);
    if (missingColumns.length > 0) {
        throw new Error(`Не удалось распарсить таблицу Ads Manager: отсутствуют обязательные колонки: ${missingColumns.join(', ')}.`);
    }
    const rawResult = await extractRawRowsFromPage(page, { layout });
    if (!rawResult || !Array.isArray(rawResult.rows))
        return [];
    // Проверяем, что ключевые числовые метрики для всех строк успешно загружены (не равны пустой строке "")
    const coreMetrics = ['spend', 'impressions', 'reach'];
    for (const fields of rawResult.rows) {
        for (const column of layout) {
            if (coreMetrics.includes(column.fieldName)) {
                const val = fields[column.fieldName];
                if (val === '') {
                    throw new Error(`Данные таблицы Ads Manager еще не загружены: пустая ячейка в ключевой колонке "${column.title}" для объявления "${fields.ad_name || 'Неизвестно'}"`);
                }
            }
        }
    }
    if (rawResult.missingFields.length > 0) {
        const missingSet = new Set();
        for (const item of rawResult.missingFields) {
            for (const fieldTitle of item.fields)
                missingSet.add(fieldTitle);
        }
        const sample = rawResult.missingFields
            .slice(0, 3)
            .map((item) => `${item.adName} (${item.fbAdId}): ${item.fields.join(', ')}`)
            .join('; ');
        throw new Error(`Не удалось распарсить колонки Ads Manager: ${Array.from(missingSet).join(', ')}. Примеры строк: ${sample}.`);
    }
    return rawResult.rows
        .map((fields) => buildRowFromFields(fields))
        .filter((row) => row !== null);
}
/** Дождаться, пока Meta вернет строки после краткого пустого состояния таблицы. */
async function waitForParsedAdsRows(page, options = {}) {
    const timeoutMs = options.timeoutMs ?? 6_000;
    const pollMs = options.pollMs ?? 300;
    const readRows = options.readRows ?? parseAdsFromPage;
    const deadline = Date.now() + timeoutMs;
    let lastError = null;
    while (true) {
        // Мгновенно выходим, если сканирование было отменено
        if (options.isCancelled?.()) {
            return [];
        }
        try {
            const rows = await readRows(page);
            if (rows.length > 0)
                return rows;
        }
        catch (err) {
            // Сохраняем последнюю ошибку парсинга колонок (например, если колонка CPM ещё не прогрузилась)
            lastError = err;
        }
        // Повторно проверяем флаг отмены
        if (options.isCancelled?.()) {
            return [];
        }
        const remainingMs = deadline - Date.now();
        if (remainingMs <= 0) {
            if (lastError !== null) {
                throw lastError;
            }
            return [];
        }
        await sleep(Math.min(pollMs, remainingMs));
    }
}
function buildRowFromFields(fields) {
    const fbAdId = cleanCell(fields['_row_id'] || '');
    const adName = cleanCell(fields['ad_name'] || '');
    if (!fbAdId || !adName || fbAdId.length < 10)
        return null;
    return {
        fb_ad_id: fbAdId,
        campaign_name: cleanCell(fields['campaign_name'] || ''),
        adset_name: cleanCell(fields['adset_name'] || ''),
        ad_name: adName,
        delivery_status: detectLogicalDeliveryStatus(fields['delivery_status'] || '', fields['_toggle_aria_checked'] || ''),
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
function cleanCell(text) {
    const value = (text || '').replace(/\u200b/g, '').replace(/\s+/g, ' ').trim();
    if (!value || value === '—' || value === '-')
        return '';
    const activeAdsMatch = value.match(/^(\d+)\s*(?:Active ads|Активные объявления):\s*\d+$/i);
    if (activeAdsMatch)
        return activeAdsMatch[1];
    return value;
}
function detectLogicalDeliveryStatus(text, toggleAriaChecked) {
    const toggleValue = cleanCell(toggleAriaChecked);
    if (toggleValue === 'false') {
        return 'OFF';
    }
    return detectDeliveryStatus(text);
}
function detectDeliveryStatus(text) {
    const value = cleanCell(text);
    if (!value)
        return 'UNKNOWN';
    const lower = value.toLowerCase();
    // Нормализуем локализованные статусы Ads Manager в стабильные коды,
    // чтобы Python-воркеры не зависели от языка текущего профиля Vision.
    if (lower.includes('off') ||
        lower.includes('выключ') ||
        lower.includes('вимкнен') ||
        lower.includes('disabled')) {
        return 'OFF';
    }
    if (lower.includes('not delivering') ||
        lower.includes('не достав') ||
        lower.includes('не показ') ||
        lower.includes('показ кампани') ||
        lower.includes('показ кампан') ||
        lower.includes('delivery stopped')) {
        return 'NOT_DELIVERING';
    }
    if (lower.includes('active') || lower.includes('актив')) {
        return 'ACTIVE';
    }
    if (lower.includes('processing') ||
        lower.includes('обработ') ||
        lower.includes('обробк')) {
        return 'PROCESSING';
    }
    if (lower.includes('review') ||
        lower.includes('рассмотр') ||
        lower.includes('розгляд')) {
        return 'IN_REVIEW';
    }
    return value;
}
function parseIntValue(text) {
    const normalized = normalizeNumericText(text);
    if (!normalized)
        return 0;
    return Number.parseInt(normalized, 10) || 0;
}
function parseMoney(text) {
    return normalizeNumericText(text) ?? '0';
}
function parseMoneyOrNull(text) {
    const value = cleanCell(text);
    if (!value || value === '--')
        return null;
    return normalizeNumericText(value);
}
function parseDecimalOrNull(text) {
    const value = cleanCell(text);
    if (!value || value === '--')
        return null;
    return normalizeNumericText(value);
}
function normalizeNumericText(text) {
    const value = cleanCell(text);
    if (!value)
        return null;
    const token = value
        .replace(/[\u00a0\u202f]/g, ' ')
        .match(/-?\d[\d\s.,']*/)?.[0]
        ?.trim();
    if (!token)
        return null;
    const negative = token.startsWith('-');
    let raw = token
        .replace(/-/g, '')
        .replace(/[\s']/g, '')
        .replace(/^[.,]+|[.,]+$/g, '');
    if (!raw || !/\d/.test(raw))
        return null;
    const commaIndex = raw.lastIndexOf(',');
    const dotIndex = raw.lastIndexOf('.');
    let decimalSeparator = '';
    if (commaIndex >= 0 && dotIndex >= 0) {
        decimalSeparator = commaIndex > dotIndex ? ',' : '.';
    }
    else if (commaIndex >= 0 || dotIndex >= 0) {
        const separator = commaIndex >= 0 ? ',' : '.';
        const parts = raw.split(separator);
        const fraction = parts[parts.length - 1] || '';
        const integer = parts.slice(0, -1).join('');
        if (parts.length === 2 && fraction.length > 0 && fraction.length <= 2) {
            decimalSeparator = separator;
        }
        else if (parts.length === 2 && fraction.length === 3 && integer.length > 3) {
            decimalSeparator = separator;
        }
    }
    if (decimalSeparator) {
        const separatorIndex = raw.lastIndexOf(decimalSeparator);
        const integerPart = raw.slice(0, separatorIndex).replace(/[.,]/g, '');
        const fractionPart = raw.slice(separatorIndex + 1).replace(/[.,]/g, '');
        raw = `${integerPart}.${fractionPart}`;
    }
    else {
        raw = raw.replace(/[.,]/g, '');
    }
    if (!raw || raw === '.')
        return null;
    return `${negative ? '-' : ''}${raw}`;
}
function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}
// --- Helpers для детекции STALE_DATA и partial-строк ---
const EMPTY_METRIC_PLACEHOLDERS = new Set(['', '—', '-', '–', 'N/A']);
function isEmptyMetric(value) {
    if (value === null || value === undefined)
        return true;
    if (typeof value === 'number')
        return value === 0;
    return EMPTY_METRIC_PLACEHOLDERS.has(value.trim());
}
/**
 * Строки, у которых все критические метрики (impressions/spend/cpm/cpc/ctr) пустые.
 * Используется для детекции STALE_DATA в observer.
 */
function countEmptyMetricsRows(rows) {
    let count = 0;
    for (const row of rows) {
        const allEmpty = isEmptyMetric(row.impressions) &&
            isEmptyMetric(row.spend) &&
            isEmptyMetric(row.cpm) &&
            isEmptyMetric(row.cpc) &&
            isEmptyMetric(row.ctr);
        if (allEmpty)
            count += 1;
    }
    return count;
}
/**
 * fb_ad_id строк, у которых пустые обязательные текстовые поля
 * (ad_name / campaign_name) — индикатор, что парсер не дочитал ячейки.
 */
function findPartialRows(rows) {
    const partial = [];
    for (const row of rows) {
        if (!row.fb_ad_id)
            continue;
        if (!row.ad_name || !row.campaign_name) {
            partial.push(row.fb_ad_id);
        }
    }
    return partial;
}
//# sourceMappingURL=parser.js.map