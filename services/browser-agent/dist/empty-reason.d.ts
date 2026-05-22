export type EmptyReason = 'table_not_found' | 'filter_excludes_all' | 'no_active_ads';
export interface EmptyReasonInput {
    hasTableHeader: boolean;
    hasFilterChips: boolean;
    rowCount: number;
}
export declare function detectEmptyReason(input: EmptyReasonInput): EmptyReason | null;
//# sourceMappingURL=empty-reason.d.ts.map