"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.getAdsTableScrollAnchor = getAdsTableScrollAnchor;
exports.resetAdsTableScroll = resetAdsTableScroll;
exports.getAdsTableScrollMetrics = getAdsTableScrollMetrics;
exports.getVisibleAdsTableRowIds = getVisibleAdsTableRowIds;
exports.toggleCellSelector = toggleCellSelector;
exports.findToggleCellInDom = findToggleCellInDom;
exports.readToggleAriaChecked = readToggleAriaChecked;
exports.findToggleCellWithTableScan = findToggleCellWithTableScan;
exports.scrollAdsTableDown = scrollAdsTableDown;
const humanizer_js_1 = require("./humanizer.js");
const toggle_utils_js_1 = require("./toggle-utils.js");
function defaultScrollMetrics(moved = false) {
    return { found: false, scrollTop: 0, maxScrollTop: 0, atBottom: false, moved };
}
// Нормализуем snake_case из браузерного evaluate в camelCase, чтобы вызывающий код не дублировал это вручную.
function normalizeScrollMetrics(raw) {
    if (!raw || typeof raw !== 'object')
        return null;
    return {
        found: Boolean(raw.found),
        scrollTop: Number(raw.scroll_top) || 0,
        maxScrollTop: Number(raw.max_scroll_top) || 0,
        atBottom: Boolean(raw.at_bottom),
        moved: Boolean(raw.moved),
    };
}
function sameRowIds(left, right) {
    if (left.length !== right.length)
        return false;
    return left.every((id, index) => id === right[index]);
}
function mergeScrollMetrics(metrics, patch) {
    return { ...metrics, ...patch };
}
function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}
async function getAdsTableScrollAnchor(page) {
    try {
        const anchor = await page.evaluate(() => {
            const selectors = [
                '[role="grid"]',
                '[role="table"]',
                '[aria-rowcount]',
                '._1gda._2djg',
                '[data-surface*="table_row:"]',
            ];
            for (const selector of selectors) {
                const node = document.querySelector(selector);
                if (!(node instanceof Element))
                    continue;
                const rect = node.getBoundingClientRect();
                if (rect.width < 40 || rect.height < 40)
                    continue;
                return { x: rect.left + rect.width * 0.5, y: rect.top + rect.height * 0.5 };
            }
            return null;
        });
        if (typeof anchor === 'object' && anchor !== null && 'x' in anchor && 'y' in anchor) {
            return [Number(anchor.x), Number(anchor.y)];
        }
    }
    catch {
        // Игнорируем: anchor нужен только для более естественного скролла.
    }
    return null;
}
async function resetAdsTableScroll(page) {
    let changed = 0;
    try {
        const result = await page.evaluate(() => {
            const seen = new Set();
            const scrollables = [];
            const addScrollable = (node) => {
                if (!(node instanceof HTMLElement) || seen.has(node))
                    return;
                seen.add(node);
                if (node.clientHeight > 40 && node.clientWidth > 40 && node.scrollHeight - node.clientHeight > 40) {
                    scrollables.push(node);
                }
            };
            const firstRowCell = document.querySelector('[data-surface*="table_row:"], ._1gda._2djg');
            for (let node = firstRowCell; node; node = node.parentElement) {
                addScrollable(node);
            }
            for (const selector of ['[role="grid"]', '[role="table"]', '[aria-rowcount]']) {
                for (const node of document.querySelectorAll(selector)) {
                    addScrollable(node);
                }
            }
            const docScroller = document.scrollingElement;
            if (docScroller instanceof HTMLElement)
                addScrollable(docScroller);
            let changed = 0;
            for (const node of scrollables) {
                const hadOffset = node.scrollTop > 0;
                if (typeof node.scrollTo === 'function') {
                    node.scrollTo({ top: 0, left: 0, behavior: 'auto' });
                }
                node.scrollTop = 0;
                if (hadOffset)
                    changed += 1;
            }
            window.scrollTo(0, 0);
            return changed;
        });
        changed = typeof result === 'number' ? result : 0;
    }
    catch {
        return changed;
    }
    const anchor = await getAdsTableScrollAnchor(page);
    if (!anchor)
        return changed;
    let previousIds = await getVisibleAdsTableRowIds(page);
    if (previousIds.length === 0)
        return changed;
    // В виртуальной таблице Meta обычный scrollTop часто остается нулевым, поэтому докручиваем вверх wheel-событиями.
    for (let i = 0; i < 20; i++) {
        try {
            await page.mouse.move(anchor[0], anchor[1]);
            await page.mouse.wheel(0, -900);
            await sleep(140);
        }
        catch {
            break;
        }
        const currentIds = await getVisibleAdsTableRowIds(page);
        if (currentIds.length === 0 || sameRowIds(previousIds, currentIds))
            break;
        changed += 1;
        previousIds = currentIds;
    }
    return changed;
}
async function getAdsTableScrollMetrics(page) {
    try {
        const metrics = await page.evaluate(() => {
            const seen = new Set();
            const scrollables = [];
            const addScrollable = (node) => {
                if (!(node instanceof HTMLElement) || seen.has(node))
                    return;
                seen.add(node);
                const maxScrollTop = node.scrollHeight - node.clientHeight;
                if (node.clientHeight > 40 && node.clientWidth > 40 && maxScrollTop > 40)
                    scrollables.push(node);
            };
            const firstRowCell = document.querySelector('[data-surface*="table_row:"], ._1gda._2djg');
            for (let node = firstRowCell; node; node = node.parentElement) {
                addScrollable(node);
            }
            for (const selector of ['[role="grid"]', '[role="table"]', '[aria-rowcount]']) {
                for (const node of document.querySelectorAll(selector)) {
                    addScrollable(node);
                }
            }
            const docScroller = document.scrollingElement;
            if (docScroller instanceof HTMLElement)
                addScrollable(docScroller);
            if (!scrollables.length) {
                const rows = document.querySelectorAll('[data-surface*="table_row:"], ._1gda._2djg');
                if (rows.length > 0) {
                    // Если строки есть, но нормального scroll-контейнера нет, дно неизвестно: движение проверяем по смене row ID.
                    return {
                        found: true,
                        scroll_top: 0,
                        max_scroll_top: 0,
                        at_bottom: false,
                        moved: false,
                    };
                }
                return null;
            }
            scrollables.sort((a, b) => {
                const aMax = a.scrollHeight - a.clientHeight;
                const bMax = b.scrollHeight - b.clientHeight;
                return bMax - aMax;
            });
            const node = scrollables[0];
            const maxScrollTop = Math.max(node.scrollHeight - node.clientHeight, 0);
            const scrollTop = Math.max(node.scrollTop, 0);
            return {
                found: true,
                scroll_top: scrollTop,
                max_scroll_top: maxScrollTop,
                at_bottom: maxScrollTop <= 0 ? true : scrollTop >= maxScrollTop - 4,
                moved: false,
            };
        });
        return normalizeScrollMetrics(metrics) || defaultScrollMetrics();
    }
    catch {
        return defaultScrollMetrics();
    }
}
async function getVisibleAdsTableRowIds(page) {
    try {
        const rowIds = await page.evaluate(() => {
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
            function reactObjectId(element) {
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
            const ids = new Set();
            const rowRe = /table_row:(\d+)/;
            for (const node of document.querySelectorAll('[data-surface*="table_row:"]')) {
                const surface = node.getAttribute('data-surface') || '';
                const match = rowRe.exec(surface);
                if (match)
                    ids.add(match[1]);
            }
            for (const row of document.querySelectorAll('._1gda._2djg')) {
                const id = reactObjectId(row);
                if (id)
                    ids.add(id);
            }
            return Array.from(ids);
        });
        if (!Array.isArray(rowIds))
            return [];
        return rowIds.filter((id) => typeof id === 'string' && id.length > 0);
    }
    catch {
        return [];
    }
}
function toggleCellSelector(fbAdId) {
    return `[data-surface*="table_row:${fbAdId}"][data-surface*="forObjectType(toggle"]`;
}
async function findToggleCellInDom(page, fbAdId) {
    const dataSurfaceCell = await page.$(toggleCellSelector(fbAdId));
    if (dataSurfaceCell)
        return dataSurfaceCell;
    const handle = await page.evaluateHandle((args) => {
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
        function reactObjectId(element) {
            const nodes = [element, ...element.querySelectorAll(`._4lg0, ${args.toggleSelector}`)];
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
        for (const row of document.querySelectorAll('._1gda._2djg')) {
            if (reactObjectId(row) !== args.fbAdId)
                continue;
            const switchNode = row.querySelector(args.toggleSelector);
            return switchNode?.closest('._4lg0') || switchNode || row;
        }
        return null;
    }, { fbAdId, toggleSelector: toggle_utils_js_1.TOGGLE_SELECTOR });
    const element = handle.asElement();
    if (!element) {
        await handle.dispose();
        return null;
    }
    return element;
}
async function readToggleAriaChecked(page, fbAdId) {
    return page.evaluate((args) => {
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
        function reactObjectId(element) {
            const nodes = [element, ...element.querySelectorAll(`._4lg0, ${args.toggleSelector}`)];
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
        let container = document.querySelector(args.selector);
        if (!container) {
            for (const row of document.querySelectorAll('._1gda._2djg')) {
                if (reactObjectId(row) === args.fbAdId) {
                    container = row;
                    break;
                }
            }
        }
        if (!container)
            return 'not_found';
        const toggle = container.matches(args.toggleSelector)
            ? container
            : container.querySelector(args.toggleSelector);
        if (!toggle)
            return 'no_toggle';
        const ariaChecked = toggle.getAttribute('aria-checked');
        if (ariaChecked !== null)
            return ariaChecked;
        if (toggle instanceof HTMLInputElement && toggle.type === 'checkbox') {
            return toggle.checked ? 'true' : 'false';
        }
        return 'null';
    }, { selector: toggleCellSelector(fbAdId), fbAdId, toggleSelector: toggle_utils_js_1.TOGGLE_SELECTOR });
}
async function findToggleCellWithTableScan(page, fbAdId, options) {
    const selector = toggleCellSelector(fbAdId);
    const resetToTop = options?.resetToTop ?? true;
    const maxScrollPasses = options?.maxScrollPasses ?? 60;
    const stepPx = options?.stepPx ?? 220;
    const fallbackMaxSteps = options?.fallbackMaxSteps ?? 12;
    let foundCell = await findToggleCellInDom(page, fbAdId);
    if (foundCell)
        return foundCell;
    if (options?.isCancelled?.())
        return null;
    if (resetToTop)
        await resetAdsTableScroll(page);
    foundCell = await findToggleCellInDom(page, fbAdId);
    if (foundCell)
        return foundCell;
    let metricsSeen = false;
    let stalledPasses = 0;
    for (let i = 0; i < maxScrollPasses; i++) {
        if (options?.isCancelled?.())
            return null;
        const scrollBefore = await getAdsTableScrollMetrics(page);
        metricsSeen = metricsSeen || scrollBefore.found;
        const scrollAfter = await scrollAdsTableDown(page, stepPx, options?.isCancelled);
        metricsSeen = metricsSeen || scrollAfter.found;
        foundCell = await findToggleCellInDom(page, fbAdId);
        if (foundCell)
            return foundCell;
        if (options?.isCancelled?.())
            return null;
        if (scrollAfter.moved) {
            stalledPasses = 0;
            continue;
        }
        stalledPasses++;
        // Даём DOM время отрендерить новые строки перед тем как считать скролл застрявшим.
        if (stalledPasses < 4) {
            await new Promise((r) => setTimeout(r, 400));
            continue;
        }
        break;
    }
    if (options?.isCancelled?.())
        return null;
    if (metricsSeen)
        return null;
    // Последний запасной вариант нужен для старых DOM-структур.
    return humanScrollToFindFallback(page, selector, fallbackMaxSteps, stepPx, options?.isCancelled);
}
async function humanScrollToFindFallback(page, selector, maxSteps, stepPx, isCancelled) {
    for (let i = 0; i < maxSteps; i++) {
        if (isCancelled?.())
            return null;
        const el = await page.$(selector);
        if (el)
            return el;
        await page.mouse.wheel(0, stepPx);
        await new Promise((r) => setTimeout(r, rand(0.3, 0.8) * 1000));
    }
    return null;
}
async function scrollAdsTableDown(page, stepPx, isCancelled) {
    if (isCancelled?.()) {
        return defaultScrollMetrics(false);
    }
    const deltaY = stepPx ?? randInt(160, 260);
    const before = await getAdsTableScrollMetrics(page);
    const beforeIds = await getVisibleAdsTableRowIds(page);
    if (before.found && before.atBottom && beforeIds.length === 0)
        return { ...before, moved: false };
    const anchor = await getAdsTableScrollAnchor(page);
    if (anchor) {
        try {
            await (0, humanizer_js_1.humanWheelScroll)(page, deltaY, {
                anchor,
                moveBefore: false,
                settleRange: [0.18, 0.35],
                driftXRange: [-8, 8],
                driftYRange: [-6, 6],
            });
        }
        catch {
            // Если wheel не сработал, ниже пробуем прямой scrollBy.
        }
    }
    const after = await getAdsTableScrollMetrics(page);
    const afterIds = await getVisibleAdsTableRowIds(page);
    // Главный сигнал виртуального скролла — смена видимого набора объявлений, даже если scrollTop не изменился.
    const idsMoved = beforeIds.length > 0 && afterIds.length > 0 && !sameRowIds(beforeIds, afterIds);
    if (idsMoved) {
        return mergeScrollMetrics(after, {
            found: after.found || before.found || afterIds.length > 0,
            atBottom: false,
            moved: true,
        });
    }
    if (before.found && after.found && after.scrollTop - before.scrollTop > 4) {
        return { ...after, moved: true };
    }
    // Запасной вариант: прямой JS scrollBy по найденному контейнеру.
    try {
        const fallback = await page.evaluate((delta) => {
            const seen = new Set();
            const scrollables = [];
            const addScrollable = (node) => {
                if (!(node instanceof HTMLElement) || seen.has(node))
                    return;
                seen.add(node);
                const maxScrollTop = node.scrollHeight - node.clientHeight;
                if (node.clientHeight > 40 && node.clientWidth > 40 && maxScrollTop > 40)
                    scrollables.push(node);
            };
            const firstRowCell = document.querySelector('[data-surface*="table_row:"], ._1gda._2djg');
            for (let node = firstRowCell; node; node = node.parentElement) {
                addScrollable(node);
            }
            for (const selector of ['[role="grid"]', '[role="table"]', '[aria-rowcount]']) {
                for (const node of document.querySelectorAll(selector)) {
                    addScrollable(node);
                }
            }
            const docScroller = document.scrollingElement;
            if (docScroller instanceof HTMLElement)
                addScrollable(docScroller);
            if (!scrollables.length)
                return null;
            scrollables.sort((a, b) => {
                const aMax = a.scrollHeight - a.clientHeight;
                const bMax = b.scrollHeight - b.clientHeight;
                return bMax - aMax;
            });
            const node = scrollables[0];
            const maxScrollTop = Math.max(node.scrollHeight - node.clientHeight, 0);
            const prevTop = Math.max(node.scrollTop, 0);
            if (typeof node.scrollBy === 'function') {
                node.scrollBy({ top: delta, left: 0, behavior: 'auto' });
            }
            node.scrollTop = Math.min(prevTop + delta, maxScrollTop);
            const nextTop = Math.max(node.scrollTop, 0);
            return {
                found: true,
                scroll_top: nextTop,
                max_scroll_top: maxScrollTop,
                at_bottom: maxScrollTop <= 0 ? true : nextTop >= maxScrollTop - 4,
                moved: Math.abs(nextTop - prevTop) > 4,
            };
        }, deltaY);
        const fallbackMetrics = normalizeScrollMetrics(fallback);
        const fallbackIds = await getVisibleAdsTableRowIds(page);
        const fallbackIdsMoved = beforeIds.length > 0 && fallbackIds.length > 0 && !sameRowIds(beforeIds, fallbackIds);
        if (fallbackMetrics) {
            return mergeScrollMetrics(fallbackMetrics, {
                found: fallbackMetrics.found || fallbackIds.length > 0,
                atBottom: fallbackIdsMoved ? false : fallbackMetrics.atBottom,
                moved: fallbackMetrics.moved || fallbackIdsMoved,
            });
        }
    }
    catch {
        // Возвращаем пустые метрики, чтобы вызывающий код мог остановить fallback.
    }
    return after.found ? { ...after, moved: false } : defaultScrollMetrics(false);
}
function rand(min, max) {
    return Math.random() * (max - min) + min;
}
function randInt(min, max) {
    return Math.floor(Math.random() * (max - min + 1)) + min;
}
//# sourceMappingURL=ads-table.js.map