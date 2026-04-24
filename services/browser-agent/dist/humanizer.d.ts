import type { Page, ElementHandle } from 'playwright';
import type { HumanProfile } from './types.js';
/** Сгенерировать HumanProfile со случайными параметрами (один раз на сессию). */
export declare function generateHumanProfile(): HumanProfile;
export declare function _resolveScrollAnchor(page: Page): Promise<[number, number] | null>;
export declare function humanMove(page: Page, targetX: number, targetY: number, options?: {
    currentPos?: [number, number];
    profile?: HumanProfile;
}): Promise<void>;
export declare function humanClick(page: Page, element: ElementHandle, options?: {
    doubleCheckPause?: boolean;
    profile?: HumanProfile;
}): Promise<void>;
export declare function humanScrollToFind(page: Page, selector: string, options?: {
    maxSteps?: number;
    stepPx?: number;
    profile?: HumanProfile;
}): Promise<ElementHandle | null>;
export declare function humanWheelScroll(page: Page, deltaY: number, options?: {
    anchor?: [number, number];
    moveBefore?: boolean;
    settleRange?: [number, number];
    driftXRange?: [number, number];
    driftYRange?: [number, number];
    profile?: HumanProfile;
}): Promise<[number, number]>;
//# sourceMappingURL=humanizer.d.ts.map