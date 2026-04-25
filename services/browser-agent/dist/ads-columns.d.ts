export type ColumnValueKind = 'name' | 'text' | 'metric';
export interface ColumnSpec {
    key: string;
    title: string;
    surfaceKey: string;
    textNeedles?: string[];
    parserField?: string;
    valueKind?: ColumnValueKind;
    requiredForValidation?: boolean;
    requiredForParsing?: boolean;
    widthPx?: number;
}
export interface HeaderSnapshot {
    surfaceKey: string;
    text: string;
    left: number;
}
export interface ParserColumnLayout {
    headerIndex: number;
    key: string;
    title: string;
    fieldName: string;
    valueKind: ColumnValueKind;
}
export interface ColumnWidthTarget {
    key: string;
    title: string;
    surfaceKey: string;
    textNeedles?: string[];
    widthPx: number;
}
export declare const REQUIRED_COLUMNS: string[];
export declare function buildAdsTableColumnWidthTargets(): ColumnWidthTarget[];
export declare function normalizeVisibleHeaders(headers: HeaderSnapshot[]): HeaderSnapshot[];
export declare function collectFoundValidationColumns(headers: HeaderSnapshot[]): string[];
export declare function collectMissingValidationColumns(headers: HeaderSnapshot[]): string[];
export declare function buildParserColumnLayout(headers: HeaderSnapshot[]): {
    headerCount: number;
    layout: ParserColumnLayout[];
    missingColumns: string[];
};
//# sourceMappingURL=ads-columns.d.ts.map