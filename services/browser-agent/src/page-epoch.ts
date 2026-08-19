// Эпоха страницы: счётчик навигаций главного фрейма.
//
// Money-мутация выполняется как page.evaluate(fetch) в живом SPA Ads Manager.
// Навигация посреди evaluate даёт «Execution context was destroyed» — ровно ту
// ошибку, что убила залив 19.08.2026. Она приходит уже ПОСЛЕ отправки, поэтому
// исход неотличим от потерянного ответа и требует ручной сверки.
//
// Эпоха позволяет обнаружить навигацию ДО отправки: снимок берётся, когда
// страница выбрана, и сверяется перед списанием гранта и синхронно перед самим
// page.evaluate. Изменившаяся эпоха — доказанный отказ без побочных эффектов.

import type { Page } from 'playwright';

const _epochs = new WeakMap<Page, number>();
const _watched = new WeakSet<Page>();

/** Навигация главного фрейма. Подкадры (iframe рекламных предпросмотров) не в счёт. */
function isMainFrameNavigation(page: Page, frame: unknown): boolean {
  try {
    return frame === page.mainFrame();
  } catch {
    return false;
  }
}

function bump(page: Page): void {
  _epochs.set(page, (_epochs.get(page) ?? 0) + 1);
}

/**
 * Начать наблюдение за страницей и вернуть текущую эпоху.
 *
 * Слушатели вешаются один раз на страницу: money-вызовов по одной вкладке много,
 * а Playwright не снимает их сам. Считаем ровно два события — запрос навигации
 * главного фрейма и domcontentloaded. framenavigated и load намеренно НЕ берём:
 * SPA Ads Manager генерирует их на внутренних переходах, которые контекст
 * исполнения не рушат, и ложный отказ money-вызова хуже пропущенной навигации.
 */
export function beginPageEpoch(page: Page): number {
  if (!_watched.has(page)) {
    _watched.add(page);
    try {
      page.on('request', (request: { isNavigationRequest?: () => boolean; frame?: () => unknown }) => {
        try {
          if (request.isNavigationRequest?.() !== true) return;
          if (!isMainFrameNavigation(page, request.frame?.())) return;
          bump(page);
        } catch {
          /* наблюдение не должно ронять операцию */
        }
      });
      page.on('domcontentloaded', () => bump(page));
    } catch {
      /* страница уже закрыта — эпоху проверит следующий вызов */
    }
  }
  return _epochs.get(page) ?? 0;
}

/** Текущая эпоха без установки наблюдения. */
export function pageEpoch(page: Page): number {
  return _epochs.get(page) ?? 0;
}

export class PageEpochChangedError extends Error {
  constructor(readonly expected: number, readonly observed: number) {
    super(
      'page_epoch_changed: money page navigated before dispatch'
      + ` (expected=${expected}, observed=${observed})`,
    );
    this.name = 'PageEpochChangedError';
  }
}

/**
 * Синхронная проверка: страница не навигировала с момента снимка.
 *
 * Синхронность существенна — между проверкой и page.evaluate не должно быть ни
 * одного await, иначе навигация успевает произойти в образовавшемся окне.
 */
export function assertPageEpochUnchanged(page: Page, expected: number): void {
  const observed = pageEpoch(page);
  if (observed !== expected) {
    throw new PageEpochChangedError(expected, observed);
  }
  let closed = false;
  try {
    closed = typeof page.isClosed === 'function' && page.isClosed();
  } catch {
    closed = true;
  }
  if (closed) {
    throw new PageEpochChangedError(expected, expected + 1);
  }
}

/** Только для тестов: забыть эпоху страницы. */
export function _forgetPageEpoch(page: Page): void {
  _epochs.delete(page);
  _watched.delete(page);
}
