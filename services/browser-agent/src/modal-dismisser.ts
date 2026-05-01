import * as fs from 'fs';
import * as path from 'path';
import type { Page } from 'playwright';

// --- Типы ---

export interface KnownModal {
  id: string;
  severity: 'normal' | 'high';
  text_markers: string[];
  safe_button_texts: string[];
  forbidden_button_texts: string[];
}

interface KnownModalsFile {
  modals: KnownModal[];
}

export interface DismissedEntry {
  id: string;
  severity: 'normal' | 'high';
}

export interface UnknownEntry {
  screenshotPath: string;
  htmlPath: string;
  summary: string;
}

export interface DismissResult {
  dismissed: DismissedEntry[];
  unknown: UnknownEntry[];
}

// --- Загрузка каталога ---

const KNOWN_MODALS_PATH = path.resolve(__dirname, '../data/known-modals.json');

export function loadKnownModals(): KnownModal[] {
  const raw = fs.readFileSync(KNOWN_MODALS_PATH, 'utf-8');
  const parsed: KnownModalsFile = JSON.parse(raw);
  return parsed.modals;
}

// --- Основная функция ---

const DIALOG_SELECTORS = [
  '[role="dialog"]',
  '[role="alertdialog"]',
  // Туры и онбординг — встречаются без role=dialog
  '[data-testid*="tooltip"]',
  '[data-testid*="onboarding"]',
];

/**
 * Находит открытые диалоги на странице, сопоставляет с каталогом известных модалок,
 * кликает безопасную кнопку или сохраняет артефакт для неизвестных диалогов.
 */
export async function dismissKnownModals(
  page: Page,
  options?: { artifactsDir?: string },
): Promise<DismissResult> {
  const knownModals = loadKnownModals();
  const artifactsDir = options?.artifactsDir ?? '.logs/modals';
  const dismissed: DismissedEntry[] = [];
  const unknown: UnknownEntry[] = [];

  // Собираем уникальные элементы-диалоги по всем селекторам
  const dialogHandles: any[] = [];
  const seen = new Set<string>();

  for (const selector of DIALOG_SELECTORS) {
    let handles: any[] = [];
    try {
      handles = await page.$$(selector);
    } catch {
      continue;
    }
    for (const handle of handles) {
      let domId: string;
      try {
        domId = await handle.evaluate((el: Element) => {
          // Уникальный идентификатор через outerHTML-хэш (кратко: берём первые 200 символов)
          return el.outerHTML.slice(0, 200);
        });
      } catch {
        continue;
      }
      if (seen.has(domId)) continue;
      seen.add(domId);
      dialogHandles.push(handle);
    }
  }

  if (dialogHandles.length === 0) {
    return { dismissed, unknown };
  }

  let unknownCounter = 0;

  for (const handle of dialogHandles) {
    let innerText = '';
    let outerHtml = '';
    try {
      innerText = await handle.innerText();
      outerHtml = await handle.evaluate((el: Element) => el.outerHTML);
    } catch {
      // Диалог исчез пока мы его обрабатываем — пропускаем.
      continue;
    }

    // Пробуем сопоставить с известными модалками
    const matched = knownModals.find((modal) =>
      modal.text_markers.some((marker) =>
        innerText.toLowerCase().includes(marker.toLowerCase()),
      ),
    );

    if (!matched) {
      // Неизвестный диалог — сохраняем артефакт
      try {
        fs.mkdirSync(artifactsDir, { recursive: true });
        const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
        unknownCounter++;
        const base = path.join(artifactsDir, `${timestamp}-${unknownCounter}`);
        const screenshotPath = `${base}.png`;
        const htmlPath = `${base}.html`;

        await page.screenshot({ path: screenshotPath, fullPage: false }).catch(() => {
          // screenshot может упасть в headless-тестах — не блокируем
        });
        fs.writeFileSync(htmlPath, outerHtml, 'utf-8');

        unknown.push({
          screenshotPath,
          htmlPath,
          summary: innerText.slice(0, 200).replace(/\n+/g, ' ').trim(),
        });
        console.warn(`[modal-dismisser] Неизвестный диалог, артефакт: ${base}`);
      } catch (err: any) {
        console.error(`[modal-dismisser] Ошибка сохранения артефакта: ${err?.message}`);
      }
      continue;
    }

    // Ищем безопасную кнопку (запрещённые исключаем сначала)
    const safeButton = await findSafeButton(handle, matched);

    if (!safeButton) {
      console.warn(
        `[modal-dismisser] Диалог «${matched.id}» распознан, но безопасная кнопка не найдена`,
      );
      continue;
    }

    try {
      await safeButton.click();
      // Ждём исчезновения диалога ≤2 секунд
      await handle
        .waitForElementState('hidden', { timeout: 2000 })
        .catch(() => undefined);
      dismissed.push({ id: matched.id, severity: matched.severity });
      console.log(`[modal-dismisser] Диалог «${matched.id}» закрыт (severity=${matched.severity})`);
    } catch (err: any) {
      console.warn(`[modal-dismisser] Не удалось закрыть «${matched.id}»: ${err?.message}`);
    }
  }

  return { dismissed, unknown };
}

// --- Вспомогательная функция поиска безопасной кнопки ---

async function findSafeButton(
  dialogHandle: any,
  modal: KnownModal,
): Promise<any | null> {
  const buttonSelectors = 'button, [role="button"], [type="button"]';
  let buttons: any[] = [];
  try {
    buttons = await dialogHandle.$$(buttonSelectors);
  } catch {
    return null;
  }

  const forbidden = new Set(modal.forbidden_button_texts.map((t) => t.toLowerCase()));

  interface Candidate {
    handle: any;
    text: string;
    exactMatch: boolean;
  }

  const candidates: Candidate[] = [];

  for (const btn of buttons) {
    let text = '';
    try {
      text = ((await btn.innerText()) || '').trim();
    } catch {
      continue;
    }

    const lower = text.toLowerCase();

    // Пропускаем запрещённые кнопки
    if (forbidden.size > 0 && (
      [...forbidden].some((f) => lower === f || lower.includes(f))
    )) {
      continue;
    }

    // Проверяем совпадение с безопасными
    const exactMatch = modal.safe_button_texts.some((s) => s.toLowerCase() === lower);
    const containsMatch = modal.safe_button_texts.some((s) => lower.includes(s.toLowerCase()));

    if (exactMatch || containsMatch) {
      candidates.push({ handle: btn, text, exactMatch });
    }
  }

  if (candidates.length === 0) return null;

  // Приоритет: точное совпадение
  const exact = candidates.find((c) => c.exactMatch);
  return (exact ?? candidates[0]).handle;
}
