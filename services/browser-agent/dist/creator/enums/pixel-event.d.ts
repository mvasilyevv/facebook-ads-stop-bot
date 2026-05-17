import type { LabelMap } from './index.js';
export declare const PixelEvent: {
    readonly PURCHASE: "PURCHASE";
    readonly LEAD: "LEAD";
    readonly COMPLETE_REGISTRATION: "COMPLETE_REGISTRATION";
    readonly ADD_TO_CART: "ADD_TO_CART";
    readonly INITIATE_CHECKOUT: "INITIATE_CHECKOUT";
    readonly SUBSCRIBE: "SUBSCRIBE";
    readonly ADD_PAYMENT_INFO: "ADD_PAYMENT_INFO";
    readonly CONTACT: "CONTACT";
    readonly SEARCH: "SEARCH";
    readonly VIEW_CONTENT: "VIEW_CONTENT";
};
export type PixelEvent = (typeof PixelEvent)[keyof typeof PixelEvent];
export declare const pixelEventLabels: LabelMap<PixelEvent>;
//# sourceMappingURL=pixel-event.d.ts.map