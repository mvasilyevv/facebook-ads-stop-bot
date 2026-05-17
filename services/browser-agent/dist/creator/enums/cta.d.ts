import type { LabelMap } from './index.js';
export declare const CallToAction: {
    readonly LEARN_MORE: "LEARN_MORE";
    readonly SIGN_UP: "SIGN_UP";
    readonly SHOP_NOW: "SHOP_NOW";
    readonly SUBSCRIBE: "SUBSCRIBE";
    readonly GET_OFFER: "GET_OFFER";
    readonly BOOK_TRAVEL: "BOOK_TRAVEL";
    readonly DOWNLOAD: "DOWNLOAD";
    readonly CONTACT_US: "CONTACT_US";
    readonly APPLY_NOW: "APPLY_NOW";
};
export type CallToAction = (typeof CallToAction)[keyof typeof CallToAction];
export declare const ctaLabels: LabelMap<CallToAction>;
//# sourceMappingURL=cta.d.ts.map