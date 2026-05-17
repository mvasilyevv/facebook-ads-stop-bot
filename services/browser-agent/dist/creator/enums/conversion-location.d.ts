import type { LabelMap } from './index.js';
export declare const ConversionLocation: {
    readonly WEBSITE: "WEBSITE";
    readonly WEBSITE_AND_CALLS: "WEBSITE_AND_CALLS";
    readonly APP: "APP";
    readonly MESSENGER: "MESSENGER";
};
export type ConversionLocation = (typeof ConversionLocation)[keyof typeof ConversionLocation];
export declare const conversionLocationLabels: LabelMap<ConversionLocation>;
//# sourceMappingURL=conversion-location.d.ts.map