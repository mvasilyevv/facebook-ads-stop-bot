import type { BlockLookup } from '../../locator.js';
export type LabelMap<T extends string> = Record<T, {
    ru: string[];
    en: string[];
}>;
export declare function resolveLabelToEnum<T extends string>(label: string, labels: LabelMap<T>): T | null;
export interface DropdownSpec<T extends string> {
    block: BlockLookup;
    labels: LabelMap<T>;
}
export declare function readSelectedValue<T extends string>(spec: DropdownSpec<T>): T | null;
export declare function selectValue<T extends string>(spec: DropdownSpec<T>, target: T): Promise<void>;
//# sourceMappingURL=select-from-dropdown.d.ts.map