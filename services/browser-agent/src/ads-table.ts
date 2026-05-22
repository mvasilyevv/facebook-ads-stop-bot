import type { Page, ElementHandle } from 'playwright';
import { humanWheelScroll } from './humanizer.js';
import { TOGGLE_SELECTOR } from './toggle-utils.js';
import type { ScrollMetrics } from './types.js';
import {
  buildAdsTableColumnWidthTargets,
  collectFoundValidationColumns,
  collectMissingValidationColumns,
  type ColumnWidthTarget,
  type HeaderSnapshot,
} from './ads-columns.js';

export { REQUIRED_COLUMNS } from './ads-columns.js';

export interface ColumnValidationResult {
  valid: boolean;
  missingColumns: string[];
  foundColumns: string[];
  errorMessage: string;
}

export interface ColumnWidthApplyResult {
  applied: boolean;
  matchedColumns: string[];
  missingColumns: string[];
  errorMessage: string;
  adjustedCells: number;
  totalWidthPx: number;
}

export interface ColumnWidthCaptureResult {
  captured: boolean;
  columnWidths: ColumnWidthTarget[];
  matchedColumns: string[];
  errorMessage: string;
  totalWidthPx: number;
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

/** Проверить наличие всех необходимых колонок в таблице Ads Manager. */
export async function validateAdsTableColumns(page: Page): Promise<ColumnValidationResult> {
  try {
    const safeHeaders = await collectVisibleHeaderSnapshots(page);
    const missingColumns = collectMissingValidationColumns(safeHeaders);
    const foundColumns = collectFoundValidationColumns(safeHeaders);

    return {
      valid: missingColumns.length === 0,
      missingColumns,
      foundColumns,
      errorMessage: missingColumns.length > 0
        ? `Отсутствуют колонки: ${missingColumns.join(', ')}`
        : '',
    };
  } catch (err: any) {
    return {
      valid: false,
      missingColumns: [],
      foundColumns: [],
      errorMessage: `Ошибка валидации колонок: ${err.message}`,
    };
  }
}

/** Снять текущую ручную ширину видимых и горизонтально доступных колонок Ads Manager. */
export async function captureAdsTableColumnWidths(page: Page): Promise<ColumnWidthCaptureResult> {
  const targets = buildAdsTableColumnWidthTargets();

  try {
    const SCROLL_SETTLE_MS = 180;
    const MAX_HORIZONTAL_PASSES = Math.max(8, targets.length + 4);
    const captured = new Map<string, ColumnWidthTarget>();

    async function collectVisibleWidths(): Promise<ColumnWidthTarget[]> {
      return page.evaluate((columnTargets: ColumnWidthTarget[]) => {
        function normalizeText(value: string | null | undefined): string {
          return String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
        }

        function readSurfaceKey(el: Element): string {
          const surface = el.getAttribute('data-surface') || '';
          const match = surface.match(/table_column_header:([^/]+)/);
          return match ? match[1] : '';
        }

        function targetMatches(target: ColumnWidthTarget, surfaceKey: string, text: string): boolean {
          const normalizedText = normalizeText(text);
          const titleMatches = Boolean(normalizedText) && normalizedText === normalizeText(target.title);
          if (target.surfaceKey !== surfaceKey) return titleMatches;
          if (!target.textNeedles?.length) return true;
          if (!normalizedText) return true;
          return titleMatches
            || target.textNeedles.some((needle) => normalizedText.includes(normalizeText(needle)));
        }

        function findColumnCell(headerNode: Element): HTMLElement | null {
          const directCell = headerNode.closest('._4lg0');
          if (directCell instanceof HTMLElement) {
            const rect = directCell.getBoundingClientRect();
            if (rect.width > 20 && rect.height > 20) return directCell;
          }

          let node: Element | null = headerNode.parentElement;
          while (node instanceof HTMLElement) {
            const rect = node.getBoundingClientRect();
            if (rect.width > 20 && rect.height > 20 && node.style.width) return node;
            node = node.parentElement;
          }
          return null;
        }

        function buildFallbackTarget(
          surfaceKey: string,
          text: string,
          index: number,
        ): ColumnWidthTarget | null {
          const normalizedText = normalizeText(text);
          const title = String(text || surfaceKey).replace(/\s+/g, ' ').trim();
          if (!surfaceKey && !normalizedText) return null;
          return {
            key: `custom:${surfaceKey || 'text'}:${normalizedText || index}`,
            title: title || surfaceKey,
            surfaceKey,
            textNeedles: normalizedText ? [normalizedText] : [],
            widthPx: 0,
          };
        }

        const result = new Map<string, ColumnWidthTarget>();
        const headerNodes = Array.from(
          document.querySelectorAll('[data-surface*="table_column_header:"]'),
        );

        for (const [index, headerNode] of headerNodes.entries()) {
          const surfaceKey = readSurfaceKey(headerNode);
          const text = headerNode.textContent || '';
          const target = columnTargets.find((item) => targetMatches(item, surfaceKey, text))
            || buildFallbackTarget(surfaceKey, text, index);
          if (!target || result.has(target.key)) continue;

          const cell = findColumnCell(headerNode);
          if (!(cell instanceof HTMLElement)) continue;
          const rect = cell.getBoundingClientRect();
          if (rect.width <= 20 || rect.height <= 20) continue;

          result.set(target.key, {
            key: target.key,
            title: target.title,
            surfaceKey: target.surfaceKey,
            textNeedles: target.textNeedles,
            widthPx: Math.round(rect.width),
          });
        }

        return Array.from(result.values());
      }, targets);
    }

    async function resetHorizontalScroll(): Promise<void> {
      await page.evaluate(() => {
        const seen = new Set<HTMLElement>();
        const scrollables: HTMLElement[] = [];

        function addScrollable(node: Node | null): void {
          if (!(node instanceof HTMLElement) || seen.has(node)) return;
          seen.add(node);
          if (node.clientWidth > 80 && node.scrollWidth - node.clientWidth > 8) scrollables.push(node);
        }

        const anchors = [
          document.querySelector('[data-surface*="table_column_header:"]'),
          document.querySelector('[data-surface*="table_row:"], ._1gda._2djg'),
          document.querySelector('[role="grid"]'),
          document.querySelector('[role="table"]'),
          document.querySelector('[aria-rowcount]'),
        ];

        for (const anchor of anchors) {
          for (let node: Node | null = anchor; node; node = (node as HTMLElement).parentElement) {
            addScrollable(node);
          }
        }

        for (const selector of ['[role="grid"]', '[role="table"]', '[aria-rowcount]']) {
          for (const node of document.querySelectorAll(selector)) addScrollable(node);
        }

        for (const node of scrollables) {
          node.scrollLeft = 0;
          node.dispatchEvent(new Event('scroll', { bubbles: true }));
        }
      });
    }

    async function scrollRight(): Promise<{ moved: boolean; atRight: boolean }> {
      return page.evaluate(() => {
        const seen = new Set<HTMLElement>();
        const scrollables: HTMLElement[] = [];

        function addScrollable(node: Node | null): void {
          if (!(node instanceof HTMLElement) || seen.has(node)) return;
          seen.add(node);
          if (node.clientWidth > 80 && node.scrollWidth - node.clientWidth > 8) scrollables.push(node);
        }

        const anchors = [
          document.querySelector('[data-surface*="table_column_header:"]'),
          document.querySelector('[data-surface*="table_row:"], ._1gda._2djg'),
          document.querySelector('[role="grid"]'),
          document.querySelector('[role="table"]'),
          document.querySelector('[aria-rowcount]'),
        ];

        for (const anchor of anchors) {
          for (let node: Node | null = anchor; node; node = (node as HTMLElement).parentElement) {
            addScrollable(node);
          }
        }

        for (const selector of ['[role="grid"]', '[role="table"]', '[aria-rowcount]']) {
          for (const node of document.querySelectorAll(selector)) addScrollable(node);
        }

        scrollables.sort((left, right) => {
          const leftMax = left.scrollWidth - left.clientWidth;
          const rightMax = right.scrollWidth - right.clientWidth;
          return rightMax - leftMax;
        });

        const scroller = scrollables[0] || null;
        if (!scroller) return { moved: false, atRight: true };

        const maxScrollLeft = Math.max(scroller.scrollWidth - scroller.clientWidth, 0);
        const prevScrollLeft = Math.max(scroller.scrollLeft, 0);
        const stepPx = Math.max(160, Math.round(scroller.clientWidth * 0.75));
        scroller.scrollLeft = Math.min(prevScrollLeft + stepPx, maxScrollLeft);
        scroller.dispatchEvent(new Event('scroll', { bubbles: true }));

        const nextScrollLeft = Math.max(scroller.scrollLeft, 0);
        return {
          moved: nextScrollLeft > prevScrollLeft + 2,
          atRight: maxScrollLeft <= 0 || nextScrollLeft >= maxScrollLeft - 4,
        };
      });
    }

    await resetHorizontalScroll();
    await page.waitForTimeout(SCROLL_SETTLE_MS);

    for (let pass = 0; pass < MAX_HORIZONTAL_PASSES; pass += 1) {
      for (const column of await collectVisibleWidths()) captured.set(column.key, column);
      if (captured.size >= targets.length) break;

      const scroll = await scrollRight();
      await page.waitForTimeout(SCROLL_SETTLE_MS);
      if (!scroll.moved || scroll.atRight) {
        for (const column of await collectVisibleWidths()) captured.set(column.key, column);
        break;
      }
    }

    await resetHorizontalScroll();
    await page.waitForTimeout(SCROLL_SETTLE_MS);

    const columnWidths = Array.from(captured.values());
    const totalWidthPx = columnWidths.reduce((total, column) => total + column.widthPx, 0);
    return {
      captured: columnWidths.length > 0,
      columnWidths,
      matchedColumns: columnWidths.map((column) => column.title),
      errorMessage: columnWidths.length > 0
        ? ''
        : 'Не найдены видимые заголовки таблицы Ads Manager для сохранения ширин',
      totalWidthPx,
    };
  } catch (err: any) {
    return {
      captured: false,
      columnWidths: [],
      matchedColumns: [],
      errorMessage: `Ошибка сохранения слепка ширины колонок: ${err.message}`,
      totalWidthPx: 0,
    };
  }
}

/** Применить сохранённый пресет ширины колонок Ads Manager без запуска сканирования. */
export async function applyAdsTableColumnWidthPreset(
  page: Page,
  savedTargets: ColumnWidthTarget[] = [],
): Promise<ColumnWidthApplyResult> {
  const targets = savedTargets.length > 0 ? savedTargets : buildAdsTableColumnWidthTargets();

  try {
    const SCROLL_SETTLE_MS = 180;
    const RESIZE_SETTLE_MS = 220;
    const SMALL_HORIZONTAL_SCROLL_PX = 80;
    const WIDTH_TOLERANCE_PX = 3;
    const MAX_HORIZONTAL_PASSES = Math.max(8, targets.length + 4);
    const totalWidthPx = targets.reduce((total, target) => total + target.widthPx, 0);
    const matchedColumns = new Set<string>();
    const matchedKeys = new Set<string>();
    const draggedKeys = new Set<string>();
    const adjustedKeys = new Set<string>();

    type ResizeCandidate = {
      key: string;
      title: string;
      widthPx: number;
      currentWidthPx: number;
      separatorX: number;
      separatorY: number;
      left: number;
      clipLeft: number;
      clipRight: number;
    };
    type ResizeCandidateScan = {
      candidates: ResizeCandidate[];
      blockedKeys: string[];
    };
    type ResizeOutcome = {
      matched: boolean;
      changed: boolean;
      needsScrollLeft: boolean;
      needsScrollRight: boolean;
    };
    type ScrollResult = {
      moved: boolean;
      atRight: boolean;
      scrollLeft: number;
      maxScrollLeft: number;
    };

    async function collectVisibleResizeState(): Promise<ResizeCandidateScan> {
      return page.evaluate((columnTargets: ColumnWidthTarget[]) => {
        function normalizeText(value: string | null | undefined): string {
          return String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
        }

        function readSurfaceKey(el: Element): string {
          const surface = el.getAttribute('data-surface') || '';
          const match = surface.match(/table_column_header:([^/]+)/);
          return match ? match[1] : '';
        }

        function targetMatches(target: ColumnWidthTarget, surfaceKey: string, text: string): boolean {
          const normalizedText = normalizeText(text);
          const titleMatches = Boolean(normalizedText) && normalizedText === normalizeText(target.title);
          if (target.surfaceKey !== surfaceKey) return titleMatches;
          if (!target.textNeedles?.length) return true;
          if (!normalizedText) return true;
          return titleMatches
            || target.textNeedles.some((needle) => normalizedText.includes(normalizeText(needle)));
        }

        function findColumnCell(headerNode: Element): HTMLElement | null {
          const directCell = headerNode.closest('._4lg0');
          if (directCell instanceof HTMLElement) {
            const rect = directCell.getBoundingClientRect();
            if (rect.width > 20 && rect.height > 20) return directCell;
          }

          let node: Element | null = headerNode.parentElement;
          while (node instanceof HTMLElement) {
            const rect = node.getBoundingClientRect();
            if (rect.width > 20 && rect.height > 20 && node.style.width) return node;
            node = node.parentElement;
          }
          return null;
        }

        function getClippingBounds(node: HTMLElement): {
          left: number;
          right: number;
          top: number;
          bottom: number;
        } {
          let left = 4;
          let right = window.innerWidth - 4;
          let top = 4;
          let bottom = window.innerHeight - 4;
          for (let parent = node.parentElement; parent; parent = parent.parentElement) {
            const style = getComputedStyle(parent);
            const clipsX = ['auto', 'hidden', 'scroll', 'clip'].includes(style.overflowX);
            const clipsY = ['auto', 'hidden', 'scroll', 'clip'].includes(style.overflowY);
            if (!clipsX && !clipsY) continue;

            const rect = parent.getBoundingClientRect();
            if (rect.width <= 0 || rect.height <= 0) continue;
            if (clipsX) {
              left = Math.max(left, rect.left + 2);
              right = Math.min(right, rect.right - 2);
            }
            if (clipsY) {
              top = Math.max(top, rect.top + 2);
              bottom = Math.min(bottom, rect.bottom - 2);
            }
          }
          return { left, right, top, bottom };
        }

        const candidates = new Map<string, ResizeCandidate>();
        const blockedKeys = new Set<string>();
        const headerNodes = Array.from(
          document.querySelectorAll('[data-surface*="table_column_header:"]'),
        );

        for (const headerNode of headerNodes) {
          const surfaceKey = readSurfaceKey(headerNode);
          const text = headerNode.textContent || '';
          const target = columnTargets.find((item) => targetMatches(item, surfaceKey, text));
          if (!target || candidates.has(target.key)) continue;

          const cell = findColumnCell(headerNode);
          if (!(cell instanceof HTMLElement)) continue;

          const cellRect = cell.getBoundingClientRect();
          if (cellRect.width <= 20 || cellRect.height <= 20) continue;

          const separator = cell.querySelector('._4lg9[role="separator"], [role="separator"]');
          if (!(separator instanceof HTMLElement)) {
            blockedKeys.add(target.key);
            continue;
          }

          const separatorRect = separator.getBoundingClientRect();
          const separatorX = separatorRect.left + separatorRect.width / 2;
          const separatorY = separatorRect.top + separatorRect.height / 2;
          const clipping = getClippingBounds(separator);
          if (
            separatorRect.width <= 0
            || separatorRect.height <= 0
            || separatorX < clipping.left
            || separatorX > clipping.right
            || separatorY < clipping.top
            || separatorY > clipping.bottom
          ) {
            blockedKeys.add(target.key);
            continue;
          }

          candidates.set(target.key, {
            key: target.key,
            title: target.title,
            widthPx: target.widthPx,
            currentWidthPx: cellRect.width,
            separatorX,
            separatorY,
            left: cellRect.left,
            clipLeft: clipping.left,
            clipRight: clipping.right,
          });
        }

        return {
          candidates: Array.from(candidates.values()).sort((left, right) => left.left - right.left),
          blockedKeys: Array.from(blockedKeys),
        };
      }, targets);
    }

    async function readVisibleColumnWidth(targetKey: string): Promise<number | null> {
      return page.evaluate((args: { columnTargets: ColumnWidthTarget[]; targetKey: string }) => {
        function normalizeText(value: string | null | undefined): string {
          return String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
        }

        function readSurfaceKey(el: Element): string {
          const surface = el.getAttribute('data-surface') || '';
          const match = surface.match(/table_column_header:([^/]+)/);
          return match ? match[1] : '';
        }

        function targetMatches(target: ColumnWidthTarget, surfaceKey: string, text: string): boolean {
          const normalizedText = normalizeText(text);
          const titleMatches = Boolean(normalizedText) && normalizedText === normalizeText(target.title);
          if (target.surfaceKey !== surfaceKey) return titleMatches;
          if (!target.textNeedles?.length) return true;
          if (!normalizedText) return true;
          return titleMatches
            || target.textNeedles.some((needle) => normalizedText.includes(normalizeText(needle)));
        }

        function findColumnCell(headerNode: Element): HTMLElement | null {
          const directCell = headerNode.closest('._4lg0');
          if (directCell instanceof HTMLElement) {
            const rect = directCell.getBoundingClientRect();
            if (rect.width > 20 && rect.height > 20) return directCell;
          }

          let node: Element | null = headerNode.parentElement;
          while (node instanceof HTMLElement) {
            const rect = node.getBoundingClientRect();
            if (rect.width > 20 && rect.height > 20 && node.style.width) return node;
            node = node.parentElement;
          }
          return null;
        }

        const target = args.columnTargets.find((item) => item.key === args.targetKey);
        if (!target) return null;
        const headerNodes = Array.from(
          document.querySelectorAll('[data-surface*="table_column_header:"]'),
        );
        for (const headerNode of headerNodes) {
          const surfaceKey = readSurfaceKey(headerNode);
          const text = headerNode.textContent || '';
          if (!targetMatches(target, surfaceKey, text)) continue;
          const cell = findColumnCell(headerNode);
          if (!(cell instanceof HTMLElement)) continue;
          const rect = cell.getBoundingClientRect();
          if (rect.width > 20 && rect.height > 20) return rect.width;
        }
        return null;
      }, { columnTargets: targets, targetKey });
    }

    async function resetHorizontalScroll(): Promise<void> {
      await page.evaluate(() => {
        const seen = new Set<HTMLElement>();
        const scrollables: HTMLElement[] = [];

        function addScrollable(node: Node | null): void {
          if (!(node instanceof HTMLElement) || seen.has(node)) return;
          seen.add(node);
          if (node.clientWidth > 80 && node.scrollWidth - node.clientWidth > 8) scrollables.push(node);
        }

        const anchors = [
          document.querySelector('[data-surface*="table_column_header:"]'),
          document.querySelector('[data-surface*="table_row:"], ._1gda._2djg'),
          document.querySelector('[role="grid"]'),
          document.querySelector('[role="table"]'),
          document.querySelector('[aria-rowcount]'),
        ];

        for (const anchor of anchors) {
          for (let node: Node | null = anchor; node; node = (node as HTMLElement).parentElement) {
            addScrollable(node);
          }
        }

        for (const selector of ['[role="grid"]', '[role="table"]', '[aria-rowcount]']) {
          for (const node of document.querySelectorAll(selector)) addScrollable(node);
        }

        for (const node of document.querySelectorAll('*')) addScrollable(node);

        for (const node of scrollables) {
          node.scrollLeft = 0;
          node.dispatchEvent(new Event('scroll', { bubbles: true }));
        }
      });
    }

    async function scrollRight(stepPx: number | null = null): Promise<ScrollResult> {
      return page.evaluate((requestedStepPx: number | null) => {
        const seen = new Set<HTMLElement>();
        const scrollables: HTMLElement[] = [];

        function addScrollable(node: Node | null): void {
          if (!(node instanceof HTMLElement) || seen.has(node)) return;
          seen.add(node);
          if (node.clientWidth > 80 && node.scrollWidth - node.clientWidth > 8) scrollables.push(node);
        }

        const anchors = [
          document.querySelector('[data-surface*="table_column_header:"]'),
          document.querySelector('[data-surface*="table_row:"], ._1gda._2djg'),
          document.querySelector('[role="grid"]'),
          document.querySelector('[role="table"]'),
          document.querySelector('[aria-rowcount]'),
        ];

        for (const anchor of anchors) {
          for (let node: Node | null = anchor; node; node = (node as HTMLElement).parentElement) {
            addScrollable(node);
          }
        }

        for (const selector of ['[role="grid"]', '[role="table"]', '[aria-rowcount]']) {
          for (const node of document.querySelectorAll(selector)) addScrollable(node);
        }

        for (const node of document.querySelectorAll('*')) addScrollable(node);

        scrollables.sort((left, right) => {
          const leftMax = left.scrollWidth - left.clientWidth;
          const rightMax = right.scrollWidth - right.clientWidth;
          return rightMax - leftMax;
        });

        const scroller = scrollables[0] || null;
        if (!scroller) return { moved: false, atRight: true, scrollLeft: 0, maxScrollLeft: 0 };

        const maxScrollLeft = Math.max(scroller.scrollWidth - scroller.clientWidth, 0);
        const prevScrollLeft = Math.max(scroller.scrollLeft, 0);
        const defaultStepPx = Math.max(160, Math.round(scroller.clientWidth * 0.75));
        const nextStepPx = requestedStepPx && requestedStepPx > 0 ? requestedStepPx : defaultStepPx;
        scroller.scrollLeft = Math.min(prevScrollLeft + nextStepPx, maxScrollLeft);
        scroller.dispatchEvent(new Event('scroll', { bubbles: true }));

        const nextScrollLeft = Math.max(scroller.scrollLeft, 0);
        return {
          moved: nextScrollLeft > prevScrollLeft + 2,
          atRight: maxScrollLeft <= 0 || nextScrollLeft >= maxScrollLeft - 4,
          scrollLeft: nextScrollLeft,
          maxScrollLeft,
        };
      }, stepPx);
    }

    async function scrollLeft(stepPx: number | null = null): Promise<ScrollResult> {
      return page.evaluate((requestedStepPx: number | null) => {
        const seen = new Set<HTMLElement>();
        const scrollables: HTMLElement[] = [];

        function addScrollable(node: Node | null): void {
          if (!(node instanceof HTMLElement) || seen.has(node)) return;
          seen.add(node);
          if (node.clientWidth > 80 && node.scrollWidth - node.clientWidth > 8) scrollables.push(node);
        }

        const anchors = [
          document.querySelector('[data-surface*="table_column_header:"]'),
          document.querySelector('[data-surface*="table_row:"], ._1gda._2djg'),
          document.querySelector('[role="grid"]'),
          document.querySelector('[role="table"]'),
          document.querySelector('[aria-rowcount]'),
        ];

        for (const anchor of anchors) {
          for (let node: Node | null = anchor; node; node = (node as HTMLElement).parentElement) {
            addScrollable(node);
          }
        }

        for (const selector of ['[role="grid"]', '[role="table"]', '[aria-rowcount]']) {
          for (const node of document.querySelectorAll(selector)) addScrollable(node);
        }

        for (const node of document.querySelectorAll('*')) addScrollable(node);

        scrollables.sort((left, right) => {
          const leftMax = left.scrollWidth - left.clientWidth;
          const rightMax = right.scrollWidth - right.clientWidth;
          return rightMax - leftMax;
        });

        const scroller = scrollables[0] || null;
        if (!scroller) return { moved: false, atRight: true, scrollLeft: 0, maxScrollLeft: 0 };

        const maxScrollLeft = Math.max(scroller.scrollWidth - scroller.clientWidth, 0);
        const prevScrollLeft = Math.max(scroller.scrollLeft, 0);
        const defaultStepPx = Math.max(160, Math.round(scroller.clientWidth * 0.75));
        const nextStepPx = requestedStepPx && requestedStepPx > 0 ? requestedStepPx : defaultStepPx;
        scroller.scrollLeft = Math.max(prevScrollLeft - nextStepPx, 0);
        scroller.dispatchEvent(new Event('scroll', { bubbles: true }));

        const nextScrollLeft = Math.max(scroller.scrollLeft, 0);
        return {
          moved: nextScrollLeft < prevScrollLeft - 2,
          atRight: maxScrollLeft <= 0 || nextScrollLeft >= maxScrollLeft - 4,
          scrollLeft: nextScrollLeft,
          maxScrollLeft,
        };
      }, stepPx);
    }

    function markMatched(candidate: ResizeCandidate): void {
      matchedColumns.add(candidate.title);
      matchedKeys.add(candidate.key);
      if (draggedKeys.has(candidate.key)) adjustedKeys.add(candidate.key);
    }

    async function dismissResizeObstructions(): Promise<void> {
      await page.keyboard.press('Escape').catch(() => undefined);
      await page.mouse.move(8, 8).catch(() => undefined);
      await page.waitForTimeout(80);
    }

    async function resizeVisibleCandidate(candidate: ResizeCandidate): Promise<ResizeOutcome> {
      const deltaPx = Math.round(candidate.widthPx - candidate.currentWidthPx);
      if (Math.abs(deltaPx) <= WIDTH_TOLERANCE_PX) {
        markMatched(candidate);
        return {
          matched: true,
          changed: false,
          needsScrollLeft: false,
          needsScrollRight: false,
        };
      }

      const viewport = page.viewportSize();
      const viewportWidth = viewport?.width ?? await page.evaluate(() => window.innerWidth);
      const minDragX = 6;
      const maxDragX = Math.max(minDragX + 2, viewportWidth - 6);
      let endX = candidate.separatorX + deltaPx;
      const clampedLeft = deltaPx < 0 && endX < minDragX;
      const clampedRight = deltaPx > 0 && endX > maxDragX;
      if (clampedLeft) endX = minDragX;
      if (clampedRight) endX = maxDragX;

      if (!Number.isFinite(endX) || Math.abs(endX - candidate.separatorX) < 2) {
        return {
          matched: false,
          changed: false,
          needsScrollLeft: clampedLeft,
          needsScrollRight: clampedRight,
        };
      }

      const steps = Math.max(3, Math.min(12, Math.ceil(Math.abs(deltaPx) / 18)));
      await dismissResizeObstructions();
      await page.mouse.move(candidate.separatorX, candidate.separatorY);
      await page.mouse.down();
      await page.mouse.move(endX, candidate.separatorY, { steps });
      await page.mouse.up();
      await dismissResizeObstructions();
      await page.waitForTimeout(RESIZE_SETTLE_MS);
      draggedKeys.add(candidate.key);

      const actualWidth = await readVisibleColumnWidth(candidate.key);
      const strictWidthMatched = actualWidth !== null
        && Math.abs(candidate.widthPx - actualWidth) <= WIDTH_TOLERANCE_PX;
      const dragWasFullyInsideViewport = !clampedLeft && !clampedRight;
      if (strictWidthMatched || (actualWidth === null && dragWasFullyInsideViewport)) {
        markMatched(candidate);
        return {
          matched: true,
          changed: true,
          needsScrollLeft: false,
          needsScrollRight: false,
        };
      }

      const previousGap = Math.abs(candidate.widthPx - candidate.currentWidthPx);
      const actualGap = actualWidth === null ? previousGap : Math.abs(candidate.widthPx - actualWidth);
      const widthImproved = actualWidth !== null && actualGap < previousGap - WIDTH_TOLERANCE_PX;
      if (dragWasFullyInsideViewport && !widthImproved) {
        return {
          matched: false,
          changed: false,
          needsScrollLeft: false,
          needsScrollRight: false,
        };
      }

      return {
        matched: false,
        changed: true,
        needsScrollLeft: clampedLeft
          && (actualWidth === null || actualWidth > candidate.widthPx + WIDTH_TOLERANCE_PX),
        needsScrollRight: clampedRight
          && (actualWidth === null || actualWidth < candidate.widthPx - WIDTH_TOLERANCE_PX),
      };
    }

    async function resizeVisibleColumns(): Promise<{
      needsSeparatorNudge: boolean;
      needsScrollLeft: boolean;
    }> {
      const blockedInPass = new Set<string>();
      for (let guard = 0; guard < targets.length + 2; guard += 1) {
        const scan = await collectVisibleResizeState();
        const candidate = scan.candidates.find((item) => (
          !matchedKeys.has(item.key) && !blockedInPass.has(item.key)
        ));
        if (!candidate) {
          return {
            needsSeparatorNudge: matchedKeys.size < targets.length
              && (
                scan.blockedKeys.some((key) => !matchedKeys.has(key))
                || blockedInPass.size > 0
              ),
            needsScrollLeft: false,
          };
        }

        const outcome = await resizeVisibleCandidate(candidate);
        if (outcome.matched) continue;
        if (outcome.needsScrollLeft) {
          return { needsSeparatorNudge: false, needsScrollLeft: true };
        }
        if (outcome.needsScrollRight) {
          return { needsSeparatorNudge: true, needsScrollLeft: false };
        }
        if (outcome.changed) continue;
        blockedInPass.add(candidate.key);
      }
      return { needsSeparatorNudge: false, needsScrollLeft: false };
    }

    await resetHorizontalScroll();
    await page.waitForTimeout(SCROLL_SETTLE_MS);

    for (let pass = 0; pass < MAX_HORIZONTAL_PASSES; pass += 1) {
      const resizeState = await resizeVisibleColumns();
      if (matchedKeys.size >= targets.length) break;

      if (resizeState.needsScrollLeft) {
        const backtrack = await scrollLeft();
        await page.waitForTimeout(SCROLL_SETTLE_MS);
        if (backtrack.moved) {
          await resizeVisibleColumns();
          if (matchedKeys.size >= targets.length) break;
          continue;
        }
      }

      if (resizeState.needsSeparatorNudge) {
        const matchedBeforeNudge = matchedKeys.size;
        const nudge = await scrollRight(SMALL_HORIZONTAL_SCROLL_PX);
        await page.waitForTimeout(SCROLL_SETTLE_MS);
        if (nudge.moved) {
          await resizeVisibleColumns();
          if (matchedKeys.size >= targets.length) break;
          if (matchedKeys.size > matchedBeforeNudge) continue;
        }
      }

      const scroll = await scrollRight();
      await page.waitForTimeout(SCROLL_SETTLE_MS);

      if (!scroll.moved || scroll.atRight) {
        await resizeVisibleColumns();
        break;
      }
    }

    await resetHorizontalScroll();
    await page.waitForTimeout(SCROLL_SETTLE_MS);

    const missingColumns = targets
      .filter((target) => !matchedKeys.has(target.key))
      .map((target) => target.title);

    const result = {
      applied: matchedKeys.size > 0 && missingColumns.length === 0,
      matchedColumns: Array.from(matchedColumns),
      missingColumns,
      errorMessage: matchedKeys.size === 0
        ? 'Не найдены видимые separator-элементы таблицы Ads Manager для автоширины'
        : missingColumns.length > 0
          ? `Не обработаны колонки автоширины: ${missingColumns.join(', ')}`
        : '',
      adjustedCells: adjustedKeys.size,
      totalWidthPx,
    };

    return {
      applied: Boolean(result.applied),
      matchedColumns: Array.isArray(result.matchedColumns) ? result.matchedColumns : [],
      missingColumns: Array.isArray(result.missingColumns) ? result.missingColumns : [],
      errorMessage: String(result.errorMessage || ''),
      adjustedCells: Number(result.adjustedCells) || 0,
      totalWidthPx: Number(result.totalWidthPx) || 0,
    };
  } catch (err: any) {
    return {
      applied: false,
      matchedColumns: [],
      missingColumns: [],
      errorMessage: `Ошибка применения автоширины колонок: ${err.message}`,
      adjustedCells: 0,
      totalWidthPx: 0,
    };
  }
}

function defaultScrollMetrics(moved = false): ScrollMetrics {
  return { found: false, scrollTop: 0, maxScrollTop: 0, atBottom: false, moved };
}

// Нормализуем snake_case из браузерного evaluate в camelCase, чтобы вызывающий код не дублировал это вручную.
function normalizeScrollMetrics(raw: any): ScrollMetrics | null {
  if (!raw || typeof raw !== 'object') return null;
  return {
    found: Boolean(raw.found),
    scrollTop: Number(raw.scroll_top) || 0,
    maxScrollTop: Number(raw.max_scroll_top) || 0,
    atBottom: Boolean(raw.at_bottom),
    moved: Boolean(raw.moved),
  };
}

function sameRowIds(left: string[], right: string[]): boolean {
  if (left.length !== right.length) return false;
  return left.every((id, index) => id === right[index]);
}

function mergeScrollMetrics(metrics: ScrollMetrics, patch: Partial<ScrollMetrics>): ScrollMetrics {
  return { ...metrics, ...patch };
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function getAdsTableScrollAnchor(page: Page): Promise<[number, number] | null> {
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
        if (!(node instanceof Element)) continue;
        const rect = node.getBoundingClientRect();
        if (rect.width < 40 || rect.height < 40) continue;
        return { x: rect.left + rect.width * 0.5, y: rect.top + rect.height * 0.5 };
      }
      return null;
    });
    if (typeof anchor === 'object' && anchor !== null && 'x' in anchor && 'y' in anchor) {
      return [Number(anchor.x), Number(anchor.y)];
    }
  } catch {
    // Игнорируем: anchor нужен только для более естественного скролла.
  }
  return null;
}

export async function resetAdsTableScroll(page: Page): Promise<number> {
  let changed = 0;
  try {
    const result = await page.evaluate(() => {
      const seen = new Set<HTMLElement>();
      const scrollables: HTMLElement[] = [];

      const addScrollable = (node: Node) => {
        if (!(node instanceof HTMLElement) || seen.has(node)) return;
        seen.add(node);
        if (node.clientHeight > 40 && node.clientWidth > 40 && node.scrollHeight - node.clientHeight > 40) {
          scrollables.push(node);
        }
      };

      const firstRowCell = document.querySelector('[data-surface*="table_row:"], ._1gda._2djg');
      for (let node: Node | null = firstRowCell; node; node = node.parentElement) {
        addScrollable(node);
      }

      for (const selector of ['[role="grid"]', '[role="table"]', '[aria-rowcount]']) {
        for (const node of document.querySelectorAll(selector)) {
          addScrollable(node);
        }
      }

      const docScroller = document.scrollingElement;
      if (docScroller instanceof HTMLElement) addScrollable(docScroller);

      let changed = 0;
      for (const node of scrollables) {
        const hadOffset = node.scrollTop > 0;
        if (typeof node.scrollTo === 'function') {
          node.scrollTo({ top: 0, left: 0, behavior: 'auto' });
        }
        node.scrollTop = 0;
        if (hadOffset) changed += 1;
      }

      window.scrollTo(0, 0);
      return changed;
    });
    changed = typeof result === 'number' ? result : 0;
  } catch {
    return changed;
  }

  const anchor = await getAdsTableScrollAnchor(page);
  if (!anchor) return changed;

  let previousIds = await getVisibleAdsTableRowIds(page);
  if (previousIds.length === 0) return changed;

  // В виртуальной таблице Meta обычный scrollTop часто остается нулевым, поэтому докручиваем вверх wheel-событиями.
  for (let i = 0; i < 20; i++) {
    try {
      await page.mouse.move(anchor[0], anchor[1]);
      await page.mouse.wheel(0, -900);
      await sleep(140);
    } catch {
      break;
    }

    const currentIds = await getVisibleAdsTableRowIds(page);
    if (currentIds.length === 0 || sameRowIds(previousIds, currentIds)) break;

    changed += 1;
    previousIds = currentIds;
  }

  return changed;
}

export async function getAdsTableScrollMetrics(page: Page): Promise<ScrollMetrics> {
  try {
    const metrics = await page.evaluate(() => {
      const seen = new Set<HTMLElement>();
      const scrollables: HTMLElement[] = [];

      const addScrollable = (node: Node) => {
        if (!(node instanceof HTMLElement) || seen.has(node)) return;
        seen.add(node);
        const maxScrollTop = node.scrollHeight - node.clientHeight;
        if (node.clientHeight > 40 && node.clientWidth > 40 && maxScrollTop > 40) scrollables.push(node);
      };

      const firstRowCell = document.querySelector('[data-surface*="table_row:"], ._1gda._2djg');
      for (let node: Node | null = firstRowCell; node; node = node.parentElement) {
        addScrollable(node);
      }

      for (const selector of ['[role="grid"]', '[role="table"]', '[aria-rowcount]']) {
        for (const node of document.querySelectorAll(selector)) {
          addScrollable(node);
        }
      }

      const docScroller = document.scrollingElement;
      if (docScroller instanceof HTMLElement) addScrollable(docScroller);

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
  } catch {
    return defaultScrollMetrics();
  }
}

export async function getVisibleAdsTableRowIds(page: Page): Promise<string[]> {
  try {
    const rowIds = await page.evaluate(() => {
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

      function reactObjectId(element: Element): string {
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

      const ids = new Set<string>();
      const rowRe = /table_row:(\d+)/;
      for (const node of document.querySelectorAll('[data-surface*="table_row:"]')) {
        const surface = node.getAttribute('data-surface') || '';
        const match = rowRe.exec(surface);
        if (match) ids.add(match[1]);
      }
      for (const row of document.querySelectorAll('._1gda._2djg')) {
        const id = reactObjectId(row);
        if (id) ids.add(id);
      }
      return Array.from(ids);
    });
    if (!Array.isArray(rowIds)) return [];
    return rowIds.filter((id): id is string => typeof id === 'string' && id.length > 0);
  } catch {
    return [];
  }
}

export function toggleCellSelector(fbAdId: string): string {
  return `[data-surface*="table_row:${fbAdId}"][data-surface*="forObjectType(toggle"]`;
}

export async function findToggleCellInDom(page: Page, fbAdId: string): Promise<ElementHandle | null> {
  const dataSurfaceCell = await page.$(toggleCellSelector(fbAdId));
  if (dataSurfaceCell) return dataSurfaceCell;

  const handle = await page.evaluateHandle((args) => {
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

    function reactObjectId(element: Element): string {
      const nodes = [element, ...element.querySelectorAll(`._4lg0, ${args.toggleSelector}`)];
      for (const node of nodes) {
        for (const key of Object.getOwnPropertyNames(node)) {
          if (!key.startsWith('__reactProps') && !key.startsWith('__reactFiber')) continue;
          const found = walkReactValue((node as any)[key], new Set(), 0);
          if (found) return found;
        }
      }
      return '';
    }

    for (const row of document.querySelectorAll('._1gda._2djg')) {
      if (reactObjectId(row) !== args.fbAdId) continue;
      const switchNode = row.querySelector(args.toggleSelector);
      return switchNode?.closest('._4lg0') || switchNode || row;
    }
    return null;
  }, { fbAdId, toggleSelector: TOGGLE_SELECTOR });

  const element = handle.asElement();
  if (!element) {
    await handle.dispose();
    return null;
  }
  return element;
}

export async function readToggleAriaChecked(page: Page, fbAdId: string): Promise<string> {
  return page.evaluate((args) => {
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

    function reactObjectId(element: Element): string {
      const nodes = [element, ...element.querySelectorAll(`._4lg0, ${args.toggleSelector}`)];
      for (const node of nodes) {
        for (const key of Object.getOwnPropertyNames(node)) {
          if (!key.startsWith('__reactProps') && !key.startsWith('__reactFiber')) continue;
          const found = walkReactValue((node as any)[key], new Set(), 0);
          if (found) return found;
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
    if (!container) return 'not_found';
    const toggle = container.matches(args.toggleSelector)
      ? container
      : container.querySelector(args.toggleSelector);
    if (!toggle) return 'no_toggle';
    const ariaChecked = toggle.getAttribute('aria-checked');
    if (ariaChecked !== null) return ariaChecked;
    if (toggle instanceof HTMLInputElement && toggle.type === 'checkbox') {
      return toggle.checked ? 'true' : 'false';
    }
    return 'null';
  }, { selector: toggleCellSelector(fbAdId), fbAdId, toggleSelector: TOGGLE_SELECTOR });
}

export async function findToggleCellWithTableScan(
  page: Page,
  fbAdId: string,
  options?: {
    resetToTop?: boolean;
    maxScrollPasses?: number;
    stepPx?: number;
    fallbackMaxSteps?: number;
    isCancelled?: () => boolean;
  },
): Promise<ElementHandle | null> {
  const selector = toggleCellSelector(fbAdId);
  const resetToTop = options?.resetToTop ?? true;
  const maxScrollPasses = options?.maxScrollPasses ?? 60;
  const stepPx = options?.stepPx ?? 220;
  const fallbackMaxSteps = options?.fallbackMaxSteps ?? 12;

  let foundCell = await findToggleCellInDom(page, fbAdId);
  if (foundCell) return foundCell;

  if (options?.isCancelled?.()) return null;

  if (resetToTop) await resetAdsTableScroll(page);

  foundCell = await findToggleCellInDom(page, fbAdId);
  if (foundCell) return foundCell;

  let metricsSeen = false;
  let stalledPasses = 0;

  for (let i = 0; i < maxScrollPasses; i++) {
    if (options?.isCancelled?.()) return null;

    const scrollBefore = await getAdsTableScrollMetrics(page);
    metricsSeen = metricsSeen || scrollBefore.found;

    const scrollAfter = await scrollAdsTableDown(page, stepPx, options?.isCancelled);
    metricsSeen = metricsSeen || scrollAfter.found;

    foundCell = await findToggleCellInDom(page, fbAdId);
    if (foundCell) return foundCell;

    if (options?.isCancelled?.()) return null;

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

  if (options?.isCancelled?.()) return null;

  if (metricsSeen) return null;

  // Последний запасной вариант нужен для старых DOM-структур.
  return humanScrollToFindFallback(page, selector, fallbackMaxSteps, stepPx, options?.isCancelled);
}

async function humanScrollToFindFallback(
  page: Page,
  selector: string,
  maxSteps: number,
  stepPx: number,
  isCancelled?: () => boolean,
): Promise<ElementHandle | null> {
  for (let i = 0; i < maxSteps; i++) {
    if (isCancelled?.()) return null;
    const el = await page.$(selector);
    if (el) return el;
    await page.mouse.wheel(0, stepPx);
    await new Promise((r) => setTimeout(r, rand(0.3, 0.8) * 1000));
  }
  return null;
}

export async function scrollAdsTableDown(
  page: Page,
  stepPx?: number,
  isCancelled?: () => boolean,
): Promise<ScrollMetrics> {
  if (isCancelled?.()) {
    return defaultScrollMetrics(false);
  }
  const deltaY = stepPx ?? randInt(160, 260);
  const before = await getAdsTableScrollMetrics(page);
  const beforeIds = await getVisibleAdsTableRowIds(page);
  if (before.found && before.atBottom && beforeIds.length === 0) return { ...before, moved: false };

  const anchor = await getAdsTableScrollAnchor(page);
  if (anchor) {
    try {
      await humanWheelScroll(page, deltaY, {
        anchor,
        moveBefore: false,
        settleRange: [0.18, 0.35],
        driftXRange: [-8, 8],
        driftYRange: [-6, 6],
      });
    } catch {
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
      const seen = new Set<HTMLElement>();
      const scrollables: HTMLElement[] = [];

      const addScrollable = (node: Node) => {
        if (!(node instanceof HTMLElement) || seen.has(node)) return;
        seen.add(node);
        const maxScrollTop = node.scrollHeight - node.clientHeight;
        if (node.clientHeight > 40 && node.clientWidth > 40 && maxScrollTop > 40) scrollables.push(node);
      };

      const firstRowCell = document.querySelector('[data-surface*="table_row:"], ._1gda._2djg');
      for (let node: Node | null = firstRowCell; node; node = node.parentElement) {
        addScrollable(node);
      }

      for (const selector of ['[role="grid"]', '[role="table"]', '[aria-rowcount]']) {
        for (const node of document.querySelectorAll(selector)) {
          addScrollable(node);
        }
      }

      const docScroller = document.scrollingElement;
      if (docScroller instanceof HTMLElement) addScrollable(docScroller);

      if (!scrollables.length) return null;

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
  } catch {
    // Возвращаем пустые метрики, чтобы вызывающий код мог остановить fallback.
  }

  return after.found ? { ...after, moved: false } : defaultScrollMetrics(false);
}

function rand(min: number, max: number): number {
  return Math.random() * (max - min) + min;
}

function randInt(min: number, max: number): number {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}
