export declare function parseOwnerTags(raw: string | null | undefined): string[];
export declare function campaignMatchesOwner(campaignName: string, ownerTag: string | null | undefined): boolean;
export declare function resolveOwnerCampaignIds(campaigns: Array<{
    id: string;
    name?: string;
}>, ownerTag: string | null | undefined): string[];
//# sourceMappingURL=am-owner.d.ts.map