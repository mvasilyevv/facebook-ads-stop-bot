"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.loadKnownModals = loadKnownModals;
exports.dismissKnownModals = dismissKnownModals;
const fs = __importStar(require("fs"));
const path = __importStar(require("path"));
// --- Загрузка каталога ---
const KNOWN_MODALS_PATH = path.resolve(__dirname, '../data/known-modals.json');
function loadKnownModals() {
    const raw = fs.readFileSync(KNOWN_MODALS_PATH, 'utf-8');
    const parsed = JSON.parse(raw);
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
async function dismissKnownModals(page, options) {
    const knownModals = loadKnownModals();
    const artifactsDir = options?.artifactsDir ?? '.logs/modals';
    const dismissed = [];
    const unknown = [];
    // Идентификаторы (outerHTML[:200]) элементов, обработанных через detect_selector —
    // их нужно исключить из последующего сканирования role=dialog, чтобы не сохранить как unknown.
    const seen = new Set();
    // --- Шаг 0: обработка элементов по detect_selector (flyout'ы, в т.ч. с role=dialog) ---
    for (const modal of knownModals) {
        if (!modal.detect_selector)
            continue;
        let element = null;
        try {
            element = await page.$(modal.detect_selector);
        }
        catch {
            continue;
        }
        if (!element)
            continue;
        // Запоминаем outerHTML элемента, чтобы основной цикл не подобрал его как unknown.
        // Для FB jewel-flyout этот элемент висит в DOM всегда (даже закрытым с классом toggleTargetClosed),
        // и role="dialog" заставляет основной сканер обрабатывать его — нам нужно его проигнорировать.
        let isOpen = false;
        try {
            const info = await element.evaluate((el) => ({
                domId: el.outerHTML.slice(0, 200),
                classList: el.className || '',
                ariaHidden: el.getAttribute('aria-hidden'),
            }));
            seen.add(info.domId);
            // Считаем flyout «открытым» только если на нём НЕТ класса toggleTargetClosed
            // и aria-hidden != "true". Иначе click_outside не нужен.
            const closedByClass = typeof info.classList === 'string' && info.classList.includes('toggleTargetClosed');
            const closedByAria = info.ariaHidden === 'true';
            isOpen = !closedByClass && !closedByAria;
        }
        catch {
            // Не критично — продолжаем
        }
        if (isOpen && modal.dismiss_strategy === 'click_outside') {
            try {
                const selector = modal.dismiss_selector ?? 'body';
                await page.click(selector, { position: { x: 5, y: 200 }, force: true });
            }
            catch {
                // Если клик не удался — всё равно считаем известным, артефакт не сохраняем
            }
        }
        // Добавляем в dismissed только если flyout был реально открыт.
        // Иначе просто помечаем «seen», чтобы основной цикл не сохранил unknown-артефакт.
        if (isOpen) {
            dismissed.push({ id: modal.id, severity: modal.severity });
            console.log(`[modal-dismisser] Jewel/flyout «${modal.id}» закрыт (detect_selector=${modal.detect_selector})`);
        }
    }
    // Собираем уникальные элементы-диалоги по всем селекторам
    const dialogHandles = [];
    for (const selector of DIALOG_SELECTORS) {
        let handles = [];
        try {
            handles = await page.$$(selector);
        }
        catch {
            continue;
        }
        for (const handle of handles) {
            let domId;
            try {
                domId = await handle.evaluate((el) => {
                    // Уникальный идентификатор через outerHTML-хэш (кратко: берём первые 200 символов)
                    return el.outerHTML.slice(0, 200);
                });
            }
            catch {
                continue;
            }
            if (seen.has(domId))
                continue;
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
            outerHtml = await handle.evaluate((el) => el.outerHTML);
        }
        catch {
            // Диалог исчез пока мы его обрабатываем — пропускаем.
            continue;
        }
        // Пробуем сопоставить с известными модалками
        const matched = knownModals.find((modal) => modal.text_markers.some((marker) => innerText.toLowerCase().includes(marker.toLowerCase())));
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
            }
            catch (err) {
                console.error(`[modal-dismisser] Ошибка сохранения артефакта: ${err?.message}`);
            }
            continue;
        }
        // Ищем безопасную кнопку (запрещённые исключаем сначала)
        const safeButton = await findSafeButton(handle, matched);
        if (!safeButton) {
            console.warn(`[modal-dismisser] Диалог «${matched.id}» распознан, но безопасная кнопка не найдена`);
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
        }
        catch (err) {
            console.warn(`[modal-dismisser] Не удалось закрыть «${matched.id}»: ${err?.message}`);
        }
    }
    return { dismissed, unknown };
}
// --- Вспомогательная функция поиска безопасной кнопки ---
async function findSafeButton(dialogHandle, modal) {
    const buttonSelectors = 'button, [role="button"], [type="button"]';
    let buttons = [];
    try {
        buttons = await dialogHandle.$$(buttonSelectors);
    }
    catch {
        return null;
    }
    const forbidden = new Set(modal.forbidden_button_texts.map((t) => t.toLowerCase()));
    const candidates = [];
    for (const btn of buttons) {
        let text = '';
        try {
            text = ((await btn.innerText()) || '').trim();
        }
        catch {
            continue;
        }
        const lower = text.toLowerCase();
        // Пропускаем запрещённые кнопки
        if (forbidden.size > 0 && ([...forbidden].some((f) => lower === f || lower.includes(f)))) {
            continue;
        }
        // Проверяем совпадение с безопасными
        const exactMatch = modal.safe_button_texts.some((s) => s.toLowerCase() === lower);
        const containsMatch = modal.safe_button_texts.some((s) => lower.includes(s.toLowerCase()));
        if (exactMatch || containsMatch) {
            candidates.push({ handle: btn, text, exactMatch });
        }
    }
    if (candidates.length === 0)
        return null;
    // Приоритет: точное совпадение
    const exact = candidates.find((c) => c.exactMatch);
    return (exact ?? candidates[0]).handle;
}
//# sourceMappingURL=modal-dismisser.js.map