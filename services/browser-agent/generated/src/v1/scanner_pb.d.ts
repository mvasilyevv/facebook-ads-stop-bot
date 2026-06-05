// package: fb_agent.scanner.v1
// file: v1/scanner.proto

/* tslint:disable */
/* eslint-disable */

import * as jspb from "google-protobuf";

export class ListCampaignsRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): ListCampaignsRequest;

    hasPageId(): boolean;
    clearPageId(): void;
    getPageId(): string | undefined;
    setPageId(value: string): ListCampaignsRequest;
    getOwnerTag(): string;
    setOwnerTag(value: string): ListCampaignsRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): ListCampaignsRequest.AsObject;
    static toObject(includeInstance: boolean, msg: ListCampaignsRequest): ListCampaignsRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: ListCampaignsRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): ListCampaignsRequest;
    static deserializeBinaryFromReader(message: ListCampaignsRequest, reader: jspb.BinaryReader): ListCampaignsRequest;
}

export namespace ListCampaignsRequest {
    export type AsObject = {
        sessionId: string,
        pageId?: string,
        ownerTag: string,
    }
}

export class CampaignInfo extends jspb.Message { 
    getId(): string;
    setId(value: string): CampaignInfo;
    getName(): string;
    setName(value: string): CampaignInfo;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): CampaignInfo.AsObject;
    static toObject(includeInstance: boolean, msg: CampaignInfo): CampaignInfo.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: CampaignInfo, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): CampaignInfo;
    static deserializeBinaryFromReader(message: CampaignInfo, reader: jspb.BinaryReader): CampaignInfo;
}

export namespace CampaignInfo {
    export type AsObject = {
        id: string,
        name: string,
    }
}

export class ListCampaignsResponse extends jspb.Message { 
    clearCampaignsList(): void;
    getCampaignsList(): Array<CampaignInfo>;
    setCampaignsList(value: Array<CampaignInfo>): ListCampaignsResponse;
    addCampaigns(value?: CampaignInfo, index?: number): CampaignInfo;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): ListCampaignsResponse.AsObject;
    static toObject(includeInstance: boolean, msg: ListCampaignsResponse): ListCampaignsResponse.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: ListCampaignsResponse, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): ListCampaignsResponse;
    static deserializeBinaryFromReader(message: ListCampaignsResponse, reader: jspb.BinaryReader): ListCampaignsResponse;
}

export namespace ListCampaignsResponse {
    export type AsObject = {
        campaignsList: Array<CampaignInfo.AsObject>,
    }
}

export class RunScanCycleRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): RunScanCycleRequest;

    hasPageId(): boolean;
    clearPageId(): void;
    getPageId(): string | undefined;
    setPageId(value: string): RunScanCycleRequest;
    getMaxScrollPasses(): number;
    setMaxScrollPasses(value: number): RunScanCycleRequest;
    getDoRefresh(): boolean;
    setDoRefresh(value: boolean): RunScanCycleRequest;
    getResetScrollFirst(): boolean;
    setResetScrollFirst(value: boolean): RunScanCycleRequest;
    getSettleDelaySeconds(): number;
    setSettleDelaySeconds(value: number): RunScanCycleRequest;
    clearCampaignIdsList(): void;
    getCampaignIdsList(): Array<string>;
    setCampaignIdsList(value: Array<string>): RunScanCycleRequest;
    addCampaignIds(value: string, index?: number): string;
    getOwnerTag(): string;
    setOwnerTag(value: string): RunScanCycleRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): RunScanCycleRequest.AsObject;
    static toObject(includeInstance: boolean, msg: RunScanCycleRequest): RunScanCycleRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: RunScanCycleRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): RunScanCycleRequest;
    static deserializeBinaryFromReader(message: RunScanCycleRequest, reader: jspb.BinaryReader): RunScanCycleRequest;
}

export namespace RunScanCycleRequest {
    export type AsObject = {
        sessionId: string,
        pageId?: string,
        maxScrollPasses: number,
        doRefresh: boolean,
        resetScrollFirst: boolean,
        settleDelaySeconds: number,
        campaignIdsList: Array<string>,
        ownerTag: string,
    }
}

export class ScanCycleEvent extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): ScanCycleEvent;

    hasProgress(): boolean;
    clearProgress(): void;
    getProgress(): ScanProgress | undefined;
    setProgress(value?: ScanProgress): ScanCycleEvent;

    hasComplete(): boolean;
    clearComplete(): void;
    getComplete(): ScanComplete | undefined;
    setComplete(value?: ScanComplete): ScanCycleEvent;

    hasError(): boolean;
    clearError(): void;
    getError(): ScanError | undefined;
    setError(value?: ScanError): ScanCycleEvent;

    getEventCase(): ScanCycleEvent.EventCase;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): ScanCycleEvent.AsObject;
    static toObject(includeInstance: boolean, msg: ScanCycleEvent): ScanCycleEvent.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: ScanCycleEvent, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): ScanCycleEvent;
    static deserializeBinaryFromReader(message: ScanCycleEvent, reader: jspb.BinaryReader): ScanCycleEvent;
}

export namespace ScanCycleEvent {
    export type AsObject = {
        sessionId: string,
        progress?: ScanProgress.AsObject,
        complete?: ScanComplete.AsObject,
        error?: ScanError.AsObject,
    }

    export enum EventCase {
        EVENT_NOT_SET = 0,
        PROGRESS = 2,
        COMPLETE = 3,
        ERROR = 4,
    }

}

export class ScanProgress extends jspb.Message { 
    getPassNumber(): number;
    setPassNumber(value: number): ScanProgress;
    getRowsSoFar(): number;
    setRowsSoFar(value: number): ScanProgress;

    hasScrollMetrics(): boolean;
    clearScrollMetrics(): void;
    getScrollMetrics(): ScrollMetrics | undefined;
    setScrollMetrics(value?: ScrollMetrics): ScanProgress;
    clearNewRowsList(): void;
    getNewRowsList(): Array<ScannedAdRow>;
    setNewRowsList(value: Array<ScannedAdRow>): ScanProgress;
    addNewRows(value?: ScannedAdRow, index?: number): ScannedAdRow;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): ScanProgress.AsObject;
    static toObject(includeInstance: boolean, msg: ScanProgress): ScanProgress.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: ScanProgress, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): ScanProgress;
    static deserializeBinaryFromReader(message: ScanProgress, reader: jspb.BinaryReader): ScanProgress;
}

export namespace ScanProgress {
    export type AsObject = {
        passNumber: number,
        rowsSoFar: number,
        scrollMetrics?: ScrollMetrics.AsObject,
        newRowsList: Array<ScannedAdRow.AsObject>,
    }
}

export class ScanComplete extends jspb.Message { 
    clearAllRowsList(): void;
    getAllRowsList(): Array<ScannedAdRow>;
    setAllRowsList(value: Array<ScannedAdRow>): ScanComplete;
    addAllRows(value?: ScannedAdRow, index?: number): ScannedAdRow;
    getTotalPasses(): number;
    setTotalPasses(value: number): ScanComplete;
    getDurationSeconds(): number;
    setDurationSeconds(value: number): ScanComplete;
    clearDismissedModalsList(): void;
    getDismissedModalsList(): Array<string>;
    setDismissedModalsList(value: Array<string>): ScanComplete;
    addDismissedModals(value: string, index?: number): string;
    clearUnknownModalArtifactsList(): void;
    getUnknownModalArtifactsList(): Array<string>;
    setUnknownModalArtifactsList(value: Array<string>): ScanComplete;
    addUnknownModalArtifacts(value: string, index?: number): string;

    hasPhaseTimings(): boolean;
    clearPhaseTimings(): void;
    getPhaseTimings(): PhaseTimings | undefined;
    setPhaseTimings(value?: PhaseTimings): ScanComplete;
    clearPartialRowIdsList(): void;
    getPartialRowIdsList(): Array<string>;
    setPartialRowIdsList(value: Array<string>): ScanComplete;
    addPartialRowIds(value: string, index?: number): string;
    clearWarningsList(): void;
    getWarningsList(): Array<string>;
    setWarningsList(value: Array<string>): ScanComplete;
    addWarnings(value: string, index?: number): string;
    getEmptyReason(): string;
    setEmptyReason(value: string): ScanComplete;
    getRowsWithAllMetricsEmpty(): number;
    setRowsWithAllMetricsEmpty(value: number): ScanComplete;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): ScanComplete.AsObject;
    static toObject(includeInstance: boolean, msg: ScanComplete): ScanComplete.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: ScanComplete, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): ScanComplete;
    static deserializeBinaryFromReader(message: ScanComplete, reader: jspb.BinaryReader): ScanComplete;
}

export namespace ScanComplete {
    export type AsObject = {
        allRowsList: Array<ScannedAdRow.AsObject>,
        totalPasses: number,
        durationSeconds: number,
        dismissedModalsList: Array<string>,
        unknownModalArtifactsList: Array<string>,
        phaseTimings?: PhaseTimings.AsObject,
        partialRowIdsList: Array<string>,
        warningsList: Array<string>,
        emptyReason: string,
        rowsWithAllMetricsEmpty: number,
    }
}

export class PhaseTimings extends jspb.Message { 
    getRefreshMs(): number;
    setRefreshMs(value: number): PhaseTimings;
    getFirstRowMs(): number;
    setFirstRowMs(value: number): PhaseTimings;
    getScrollMs(): number;
    setScrollMs(value: number): PhaseTimings;
    getParseMs(): number;
    setParseMs(value: number): PhaseTimings;
    getTotalMs(): number;
    setTotalMs(value: number): PhaseTimings;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): PhaseTimings.AsObject;
    static toObject(includeInstance: boolean, msg: PhaseTimings): PhaseTimings.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: PhaseTimings, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): PhaseTimings;
    static deserializeBinaryFromReader(message: PhaseTimings, reader: jspb.BinaryReader): PhaseTimings;
}

export namespace PhaseTimings {
    export type AsObject = {
        refreshMs: number,
        firstRowMs: number,
        scrollMs: number,
        parseMs: number,
        totalMs: number,
    }
}

export class ScanError extends jspb.Message { 
    getMessage(): string;
    setMessage(value: string): ScanError;
    getRecoverable(): boolean;
    setRecoverable(value: boolean): ScanError;
    getAttempt(): number;
    setAttempt(value: number): ScanError;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): ScanError.AsObject;
    static toObject(includeInstance: boolean, msg: ScanError): ScanError.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: ScanError, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): ScanError;
    static deserializeBinaryFromReader(message: ScanError, reader: jspb.BinaryReader): ScanError;
}

export namespace ScanError {
    export type AsObject = {
        message: string,
        recoverable: boolean,
        attempt: number,
    }
}

export class ScrollMetrics extends jspb.Message { 
    getFound(): boolean;
    setFound(value: boolean): ScrollMetrics;
    getScrollTop(): number;
    setScrollTop(value: number): ScrollMetrics;
    getMaxScrollTop(): number;
    setMaxScrollTop(value: number): ScrollMetrics;
    getAtBottom(): boolean;
    setAtBottom(value: boolean): ScrollMetrics;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): ScrollMetrics.AsObject;
    static toObject(includeInstance: boolean, msg: ScrollMetrics): ScrollMetrics.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: ScrollMetrics, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): ScrollMetrics;
    static deserializeBinaryFromReader(message: ScrollMetrics, reader: jspb.BinaryReader): ScrollMetrics;
}

export namespace ScrollMetrics {
    export type AsObject = {
        found: boolean,
        scrollTop: number,
        maxScrollTop: number,
        atBottom: boolean,
    }
}

export class ScannedAdRow extends jspb.Message { 
    getFbAdId(): string;
    setFbAdId(value: string): ScannedAdRow;
    getCampaignName(): string;
    setCampaignName(value: string): ScannedAdRow;
    getAdsetName(): string;
    setAdsetName(value: string): ScannedAdRow;
    getAdName(): string;
    setAdName(value: string): ScannedAdRow;
    getDeliveryStatus(): string;
    setDeliveryStatus(value: string): ScannedAdRow;
    getSpend(): string;
    setSpend(value: string): ScannedAdRow;
    getBudget(): string;
    setBudget(value: string): ScannedAdRow;
    getReach(): number;
    setReach(value: number): ScannedAdRow;
    getImpressions(): number;
    setImpressions(value: number): ScannedAdRow;
    getClicks(): number;
    setClicks(value: number): ScannedAdRow;
    getCpc(): string;
    setCpc(value: string): ScannedAdRow;
    getCtr(): string;
    setCtr(value: string): ScannedAdRow;
    getOutboundClicks(): number;
    setOutboundClicks(value: number): ScannedAdRow;
    getOutboundCtr(): string;
    setOutboundCtr(value: string): ScannedAdRow;
    getLandingPageViews(): number;
    setLandingPageViews(value: number): ScannedAdRow;
    getCostPerLandingPageView(): string;
    setCostPerLandingPageView(value: string): ScannedAdRow;
    getCostPerResult(): string;
    setCostPerResult(value: string): ScannedAdRow;
    getCpm(): string;
    setCpm(value: string): ScannedAdRow;
    getFrequency(): string;
    setFrequency(value: string): ScannedAdRow;
    getLeads(): number;
    setLeads(value: number): ScannedAdRow;
    getCostPerLead(): string;
    setCostPerLead(value: string): ScannedAdRow;
    getRegistrations(): number;
    setRegistrations(value: number): ScannedAdRow;
    getCostPerRegistration(): string;
    setCostPerRegistration(value: string): ScannedAdRow;
    getDeposits(): number;
    setDeposits(value: number): ScannedAdRow;
    getResolvedOfferCode(): string;
    setResolvedOfferCode(value: string): ScannedAdRow;
    getCampaignId(): string;
    setCampaignId(value: string): ScannedAdRow;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): ScannedAdRow.AsObject;
    static toObject(includeInstance: boolean, msg: ScannedAdRow): ScannedAdRow.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: ScannedAdRow, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): ScannedAdRow;
    static deserializeBinaryFromReader(message: ScannedAdRow, reader: jspb.BinaryReader): ScannedAdRow;
}

export namespace ScannedAdRow {
    export type AsObject = {
        fbAdId: string,
        campaignName: string,
        adsetName: string,
        adName: string,
        deliveryStatus: string,
        spend: string,
        budget: string,
        reach: number,
        impressions: number,
        clicks: number,
        cpc: string,
        ctr: string,
        outboundClicks: number,
        outboundCtr: string,
        landingPageViews: number,
        costPerLandingPageView: string,
        costPerResult: string,
        cpm: string,
        frequency: string,
        leads: number,
        costPerLead: string,
        registrations: number,
        costPerRegistration: string,
        deposits: number,
        resolvedOfferCode: string,
        campaignId: string,
    }
}

export class HardReloadPageRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): HardReloadPageRequest;

    hasPageId(): boolean;
    clearPageId(): void;
    getPageId(): string | undefined;
    setPageId(value: string): HardReloadPageRequest;
    getBypassCache(): boolean;
    setBypassCache(value: boolean): HardReloadPageRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): HardReloadPageRequest.AsObject;
    static toObject(includeInstance: boolean, msg: HardReloadPageRequest): HardReloadPageRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: HardReloadPageRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): HardReloadPageRequest;
    static deserializeBinaryFromReader(message: HardReloadPageRequest, reader: jspb.BinaryReader): HardReloadPageRequest;
}

export namespace HardReloadPageRequest {
    export type AsObject = {
        sessionId: string,
        pageId?: string,
        bypassCache: boolean,
    }
}

export class HardReloadPageResponse extends jspb.Message { 
    getSuccess(): boolean;
    setSuccess(value: boolean): HardReloadPageResponse;
    getErrorMessage(): string;
    setErrorMessage(value: string): HardReloadPageResponse;
    getReloadMs(): number;
    setReloadMs(value: number): HardReloadPageResponse;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): HardReloadPageResponse.AsObject;
    static toObject(includeInstance: boolean, msg: HardReloadPageResponse): HardReloadPageResponse.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: HardReloadPageResponse, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): HardReloadPageResponse;
    static deserializeBinaryFromReader(message: HardReloadPageResponse, reader: jspb.BinaryReader): HardReloadPageResponse;
}

export namespace HardReloadPageResponse {
    export type AsObject = {
        success: boolean,
        errorMessage: string,
        reloadMs: number,
    }
}
