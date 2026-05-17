export declare const IdleRange: {
    readonly SHORT: readonly [80, 250];
    readonly BETWEEN_STEPS: readonly [600, 2500];
    readonly BETWEEN_SCENES: readonly [3000, 8000];
    readonly TYPING: readonly [40, 180];
    readonly TYPING_BURST_PAUSE: readonly [200, 800];
};
export type IdleRangeKey = readonly [number, number];
export declare function humanIdle(range: IdleRangeKey): Promise<void>;
export declare function humanClick(el: Element): Promise<void>;
export declare function humanType(el: HTMLInputElement | HTMLTextAreaElement, text: string): Promise<void>;
export declare function humanScroll(el: Element, deltaY: number): Promise<void>;
//# sourceMappingURL=humanizer.d.ts.map