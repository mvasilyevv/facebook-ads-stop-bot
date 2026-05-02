import type { Page } from 'playwright';
export interface KnownModal {
    id: string;
    severity: 'normal' | 'high';
    text_markers: string[];
    safe_button_texts: string[];
    forbidden_button_texts: string[];
    detect_selector?: string;
    dismiss_strategy?: 'click_outside' | 'button';
    dismiss_selector?: string;
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
export declare function loadKnownModals(): KnownModal[];
/**
 * Находит открытые диалоги на странице, сопоставляет с каталогом известных модалок,
 * кликает безопасную кнопку или сохраняет артефакт для неизвестных диалогов.
 */
export declare function dismissKnownModals(page: Page, options?: {
    artifactsDir?: string;
}): Promise<DismissResult>;
//# sourceMappingURL=modal-dismisser.d.ts.map