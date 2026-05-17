import { BaseStep } from '../base.js';
export type TreeRole = 'ad' | 'adset';
export interface DuplicateInput {
    sourceName: string;
    newName: string;
}
export interface RenameInput {
    from: string;
    to: string;
}
export declare function createDuplicateStep(name: string, role: TreeRole): new () => BaseStep<DuplicateInput, void>;
export declare function createRenameStep(name: string, role: TreeRole): new () => BaseStep<RenameInput, void>;
//# sourceMappingURL=tree-actions.d.ts.map