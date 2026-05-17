export interface BlockLookup {
    testid?: string;
    fiberRole?: string;
    aria?: string[];
    text?: string[];
}
export declare function findByTestId(testid: string, root?: ParentNode): Element | null;
export declare function findByAriaLabel(labels: string[], root?: ParentNode): Element | null;
export declare function findByFiberRole(role: string, root?: ParentNode): Element | null;
export declare function findByNormalizedText(texts: string[], root?: ParentNode): Element | null;
export declare function findBlock(spec: BlockLookup, root?: ParentNode): Element | null;
//# sourceMappingURL=locator.d.ts.map