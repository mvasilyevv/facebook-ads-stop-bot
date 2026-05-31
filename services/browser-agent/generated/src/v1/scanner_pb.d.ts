// package: fb_agent.scanner.v1
// file: v1/scanner.proto

/* tslint:disable */
/* eslint-disable */

import * as jspb from "google-protobuf";

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

export class RefreshTableRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): RefreshTableRequest;

    hasPageId(): boolean;
    clearPageId(): void;
    getPageId(): string | undefined;
    setPageId(value: string): RefreshTableRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): RefreshTableRequest.AsObject;
    static toObject(includeInstance: boolean, msg: RefreshTableRequest): RefreshTableRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: RefreshTableRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): RefreshTableRequest;
    static deserializeBinaryFromReader(message: RefreshTableRequest, reader: jspb.BinaryReader): RefreshTableRequest;
}

export namespace RefreshTableRequest {
    export type AsObject = {
        sessionId: string,
        pageId?: string,
    }
}

export class RefreshTableResponse extends jspb.Message { 
    getRefreshed(): boolean;
    setRefreshed(value: boolean): RefreshTableResponse;
    getFallbackReload(): boolean;
    setFallbackReload(value: boolean): RefreshTableResponse;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): RefreshTableResponse.AsObject;
    static toObject(includeInstance: boolean, msg: RefreshTableResponse): RefreshTableResponse.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: RefreshTableResponse, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): RefreshTableResponse;
    static deserializeBinaryFromReader(message: RefreshTableResponse, reader: jspb.BinaryReader): RefreshTableResponse;
}

export namespace RefreshTableResponse {
    export type AsObject = {
        refreshed: boolean,
        fallbackReload: boolean,
    }
}

export class ParseVisibleRowsRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): ParseVisibleRowsRequest;

    hasPageId(): boolean;
    clearPageId(): void;
    getPageId(): string | undefined;
    setPageId(value: string): ParseVisibleRowsRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): ParseVisibleRowsRequest.AsObject;
    static toObject(includeInstance: boolean, msg: ParseVisibleRowsRequest): ParseVisibleRowsRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: ParseVisibleRowsRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): ParseVisibleRowsRequest;
    static deserializeBinaryFromReader(message: ParseVisibleRowsRequest, reader: jspb.BinaryReader): ParseVisibleRowsRequest;
}

export namespace ParseVisibleRowsRequest {
    export type AsObject = {
        sessionId: string,
        pageId?: string,
    }
}

export class ParseVisibleRowsResponse extends jspb.Message { 
    clearRowsList(): void;
    getRowsList(): Array<ScannedAdRow>;
    setRowsList(value: Array<ScannedAdRow>): ParseVisibleRowsResponse;
    addRows(value?: ScannedAdRow, index?: number): ScannedAdRow;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): ParseVisibleRowsResponse.AsObject;
    static toObject(includeInstance: boolean, msg: ParseVisibleRowsResponse): ParseVisibleRowsResponse.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: ParseVisibleRowsResponse, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): ParseVisibleRowsResponse;
    static deserializeBinaryFromReader(message: ParseVisibleRowsResponse, reader: jspb.BinaryReader): ParseVisibleRowsResponse;
}

export namespace ParseVisibleRowsResponse {
    export type AsObject = {
        rowsList: Array<ScannedAdRow.AsObject>,
    }
}

export class ScrollAndParseRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): ScrollAndParseRequest;

    hasPageId(): boolean;
    clearPageId(): void;
    getPageId(): string | undefined;
    setPageId(value: string): ScrollAndParseRequest;
    getScrollAmount(): number;
    setScrollAmount(value: number): ScrollAndParseRequest;
    getWaitForStable(): boolean;
    setWaitForStable(value: boolean): ScrollAndParseRequest;
    getStableTimeoutSeconds(): number;
    setStableTimeoutSeconds(value: number): ScrollAndParseRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): ScrollAndParseRequest.AsObject;
    static toObject(includeInstance: boolean, msg: ScrollAndParseRequest): ScrollAndParseRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: ScrollAndParseRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): ScrollAndParseRequest;
    static deserializeBinaryFromReader(message: ScrollAndParseRequest, reader: jspb.BinaryReader): ScrollAndParseRequest;
}

export namespace ScrollAndParseRequest {
    export type AsObject = {
        sessionId: string,
        pageId?: string,
        scrollAmount: number,
        waitForStable: boolean,
        stableTimeoutSeconds: number,
    }
}

export class ScrollAndParseResponse extends jspb.Message { 
    clearNewRowsList(): void;
    getNewRowsList(): Array<ScannedAdRow>;
    setNewRowsList(value: Array<ScannedAdRow>): ScrollAndParseResponse;
    addNewRows(value?: ScannedAdRow, index?: number): ScannedAdRow;

    hasScrollMetrics(): boolean;
    clearScrollMetrics(): void;
    getScrollMetrics(): ScrollMetrics | undefined;
    setScrollMetrics(value?: ScrollMetrics): ScrollAndParseResponse;
    getAtBottom(): boolean;
    setAtBottom(value: boolean): ScrollAndParseResponse;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): ScrollAndParseResponse.AsObject;
    static toObject(includeInstance: boolean, msg: ScrollAndParseResponse): ScrollAndParseResponse.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: ScrollAndParseResponse, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): ScrollAndParseResponse;
    static deserializeBinaryFromReader(message: ScrollAndParseResponse, reader: jspb.BinaryReader): ScrollAndParseResponse;
}

export namespace ScrollAndParseResponse {
    export type AsObject = {
        newRowsList: Array<ScannedAdRow.AsObject>,
        scrollMetrics?: ScrollMetrics.AsObject,
        atBottom: boolean,
    }
}

export class WaitForDomStableRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): WaitForDomStableRequest;

    hasPageId(): boolean;
    clearPageId(): void;
    getPageId(): string | undefined;
    setPageId(value: string): WaitForDomStableRequest;
    getTimeoutSeconds(): number;
    setTimeoutSeconds(value: number): WaitForDomStableRequest;
    getPollIntervalSeconds(): number;
    setPollIntervalSeconds(value: number): WaitForDomStableRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): WaitForDomStableRequest.AsObject;
    static toObject(includeInstance: boolean, msg: WaitForDomStableRequest): WaitForDomStableRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: WaitForDomStableRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): WaitForDomStableRequest;
    static deserializeBinaryFromReader(message: WaitForDomStableRequest, reader: jspb.BinaryReader): WaitForDomStableRequest;
}

export namespace WaitForDomStableRequest {
    export type AsObject = {
        sessionId: string,
        pageId?: string,
        timeoutSeconds: number,
        pollIntervalSeconds: number,
    }
}

export class WaitForDomStableResponse extends jspb.Message { 
    getStabilized(): boolean;
    setStabilized(value: boolean): WaitForDomStableResponse;
    getFinalRowCount(): number;
    setFinalRowCount(value: number): WaitForDomStableResponse;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): WaitForDomStableResponse.AsObject;
    static toObject(includeInstance: boolean, msg: WaitForDomStableResponse): WaitForDomStableResponse.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: WaitForDomStableResponse, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): WaitForDomStableResponse;
    static deserializeBinaryFromReader(message: WaitForDomStableResponse, reader: jspb.BinaryReader): WaitForDomStableResponse;
}

export namespace WaitForDomStableResponse {
    export type AsObject = {
        stabilized: boolean,
        finalRowCount: number,
    }
}

export class ResetScrollRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): ResetScrollRequest;

    hasPageId(): boolean;
    clearPageId(): void;
    getPageId(): string | undefined;
    setPageId(value: string): ResetScrollRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): ResetScrollRequest.AsObject;
    static toObject(includeInstance: boolean, msg: ResetScrollRequest): ResetScrollRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: ResetScrollRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): ResetScrollRequest;
    static deserializeBinaryFromReader(message: ResetScrollRequest, reader: jspb.BinaryReader): ResetScrollRequest;
}

export namespace ResetScrollRequest {
    export type AsObject = {
        sessionId: string,
        pageId?: string,
    }
}

export class ResetScrollResponse extends jspb.Message { 
    getContainersReset(): number;
    setContainersReset(value: number): ResetScrollResponse;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): ResetScrollResponse.AsObject;
    static toObject(includeInstance: boolean, msg: ResetScrollResponse): ResetScrollResponse.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: ResetScrollResponse, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): ResetScrollResponse;
    static deserializeBinaryFromReader(message: ResetScrollResponse, reader: jspb.BinaryReader): ResetScrollResponse;
}

export namespace ResetScrollResponse {
    export type AsObject = {
        containersReset: number,
    }
}

export class GetScrollMetricsRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): GetScrollMetricsRequest;

    hasPageId(): boolean;
    clearPageId(): void;
    getPageId(): string | undefined;
    setPageId(value: string): GetScrollMetricsRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): GetScrollMetricsRequest.AsObject;
    static toObject(includeInstance: boolean, msg: GetScrollMetricsRequest): GetScrollMetricsRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: GetScrollMetricsRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): GetScrollMetricsRequest;
    static deserializeBinaryFromReader(message: GetScrollMetricsRequest, reader: jspb.BinaryReader): GetScrollMetricsRequest;
}

export namespace GetScrollMetricsRequest {
    export type AsObject = {
        sessionId: string,
        pageId?: string,
    }
}

export class GetScrollMetricsResponse extends jspb.Message { 

    hasMetrics(): boolean;
    clearMetrics(): void;
    getMetrics(): ScrollMetrics | undefined;
    setMetrics(value?: ScrollMetrics): GetScrollMetricsResponse;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): GetScrollMetricsResponse.AsObject;
    static toObject(includeInstance: boolean, msg: GetScrollMetricsResponse): GetScrollMetricsResponse.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: GetScrollMetricsResponse, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): GetScrollMetricsResponse;
    static deserializeBinaryFromReader(message: GetScrollMetricsResponse, reader: jspb.BinaryReader): GetScrollMetricsResponse;
}

export namespace GetScrollMetricsResponse {
    export type AsObject = {
        metrics?: ScrollMetrics.AsObject,
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

export class GetVisibleRowIdsRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): GetVisibleRowIdsRequest;

    hasPageId(): boolean;
    clearPageId(): void;
    getPageId(): string | undefined;
    setPageId(value: string): GetVisibleRowIdsRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): GetVisibleRowIdsRequest.AsObject;
    static toObject(includeInstance: boolean, msg: GetVisibleRowIdsRequest): GetVisibleRowIdsRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: GetVisibleRowIdsRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): GetVisibleRowIdsRequest;
    static deserializeBinaryFromReader(message: GetVisibleRowIdsRequest, reader: jspb.BinaryReader): GetVisibleRowIdsRequest;
}

export namespace GetVisibleRowIdsRequest {
    export type AsObject = {
        sessionId: string,
        pageId?: string,
    }
}

export class GetVisibleRowIdsResponse extends jspb.Message { 
    clearRowIdsList(): void;
    getRowIdsList(): Array<string>;
    setRowIdsList(value: Array<string>): GetVisibleRowIdsResponse;
    addRowIds(value: string, index?: number): string;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): GetVisibleRowIdsResponse.AsObject;
    static toObject(includeInstance: boolean, msg: GetVisibleRowIdsResponse): GetVisibleRowIdsResponse.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: GetVisibleRowIdsResponse, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): GetVisibleRowIdsResponse;
    static deserializeBinaryFromReader(message: GetVisibleRowIdsResponse, reader: jspb.BinaryReader): GetVisibleRowIdsResponse;
}

export namespace GetVisibleRowIdsResponse {
    export type AsObject = {
        rowIdsList: Array<string>,
    }
}

export class FindToggleCellRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): FindToggleCellRequest;

    hasPageId(): boolean;
    clearPageId(): void;
    getPageId(): string | undefined;
    setPageId(value: string): FindToggleCellRequest;
    getFbAdId(): string;
    setFbAdId(value: string): FindToggleCellRequest;
    getMaxScrollPasses(): number;
    setMaxScrollPasses(value: number): FindToggleCellRequest;
    getResetToTop(): boolean;
    setResetToTop(value: boolean): FindToggleCellRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): FindToggleCellRequest.AsObject;
    static toObject(includeInstance: boolean, msg: FindToggleCellRequest): FindToggleCellRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: FindToggleCellRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): FindToggleCellRequest;
    static deserializeBinaryFromReader(message: FindToggleCellRequest, reader: jspb.BinaryReader): FindToggleCellRequest;
}

export namespace FindToggleCellRequest {
    export type AsObject = {
        sessionId: string,
        pageId?: string,
        fbAdId: string,
        maxScrollPasses: number,
        resetToTop: boolean,
    }
}

export class FindToggleCellResponse extends jspb.Message { 
    getFound(): boolean;
    setFound(value: boolean): FindToggleCellResponse;
    getCellX(): number;
    setCellX(value: number): FindToggleCellResponse;
    getCellY(): number;
    setCellY(value: number): FindToggleCellResponse;
    getAriaChecked(): string;
    setAriaChecked(value: string): FindToggleCellResponse;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): FindToggleCellResponse.AsObject;
    static toObject(includeInstance: boolean, msg: FindToggleCellResponse): FindToggleCellResponse.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: FindToggleCellResponse, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): FindToggleCellResponse;
    static deserializeBinaryFromReader(message: FindToggleCellResponse, reader: jspb.BinaryReader): FindToggleCellResponse;
}

export namespace FindToggleCellResponse {
    export type AsObject = {
        found: boolean,
        cellX: number,
        cellY: number,
        ariaChecked: string,
    }
}

export class ReadToggleStateRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): ReadToggleStateRequest;

    hasPageId(): boolean;
    clearPageId(): void;
    getPageId(): string | undefined;
    setPageId(value: string): ReadToggleStateRequest;
    getFbAdId(): string;
    setFbAdId(value: string): ReadToggleStateRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): ReadToggleStateRequest.AsObject;
    static toObject(includeInstance: boolean, msg: ReadToggleStateRequest): ReadToggleStateRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: ReadToggleStateRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): ReadToggleStateRequest;
    static deserializeBinaryFromReader(message: ReadToggleStateRequest, reader: jspb.BinaryReader): ReadToggleStateRequest;
}

export namespace ReadToggleStateRequest {
    export type AsObject = {
        sessionId: string,
        pageId?: string,
        fbAdId: string,
    }
}

export class ReadToggleStateResponse extends jspb.Message { 
    getFound(): boolean;
    setFound(value: boolean): ReadToggleStateResponse;
    getAriaChecked(): string;
    setAriaChecked(value: string): ReadToggleStateResponse;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): ReadToggleStateResponse.AsObject;
    static toObject(includeInstance: boolean, msg: ReadToggleStateResponse): ReadToggleStateResponse.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: ReadToggleStateResponse, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): ReadToggleStateResponse;
    static deserializeBinaryFromReader(message: ReadToggleStateResponse, reader: jspb.BinaryReader): ReadToggleStateResponse;
}

export namespace ReadToggleStateResponse {
    export type AsObject = {
        found: boolean,
        ariaChecked: string,
    }
}

export class ToggleAdRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): ToggleAdRequest;

    hasPageId(): boolean;
    clearPageId(): void;
    getPageId(): string | undefined;
    setPageId(value: string): ToggleAdRequest;
    getFbAdId(): string;
    setFbAdId(value: string): ToggleAdRequest;
    getTargetState(): boolean;
    setTargetState(value: boolean): ToggleAdRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): ToggleAdRequest.AsObject;
    static toObject(includeInstance: boolean, msg: ToggleAdRequest): ToggleAdRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: ToggleAdRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): ToggleAdRequest;
    static deserializeBinaryFromReader(message: ToggleAdRequest, reader: jspb.BinaryReader): ToggleAdRequest;
}

export namespace ToggleAdRequest {
    export type AsObject = {
        sessionId: string,
        pageId?: string,
        fbAdId: string,
        targetState: boolean,
    }
}

export class ToggleAdResponse extends jspb.Message { 
    getSuccess(): boolean;
    setSuccess(value: boolean): ToggleAdResponse;
    getFinalState(): string;
    setFinalState(value: string): ToggleAdResponse;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): ToggleAdResponse.AsObject;
    static toObject(includeInstance: boolean, msg: ToggleAdResponse): ToggleAdResponse.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: ToggleAdResponse, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): ToggleAdResponse;
    static deserializeBinaryFromReader(message: ToggleAdResponse, reader: jspb.BinaryReader): ToggleAdResponse;
}

export namespace ToggleAdResponse {
    export type AsObject = {
        success: boolean,
        finalState: string,
    }
}

export class HumanMoveRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): HumanMoveRequest;

    hasPageId(): boolean;
    clearPageId(): void;
    getPageId(): string | undefined;
    setPageId(value: string): HumanMoveRequest;
    getTargetX(): number;
    setTargetX(value: number): HumanMoveRequest;
    getTargetY(): number;
    setTargetY(value: number): HumanMoveRequest;

    hasProfile(): boolean;
    clearProfile(): void;
    getProfile(): HumanProfile | undefined;
    setProfile(value?: HumanProfile): HumanMoveRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): HumanMoveRequest.AsObject;
    static toObject(includeInstance: boolean, msg: HumanMoveRequest): HumanMoveRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: HumanMoveRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): HumanMoveRequest;
    static deserializeBinaryFromReader(message: HumanMoveRequest, reader: jspb.BinaryReader): HumanMoveRequest;
}

export namespace HumanMoveRequest {
    export type AsObject = {
        sessionId: string,
        pageId?: string,
        targetX: number,
        targetY: number,
        profile?: HumanProfile.AsObject,
    }
}

export class HumanMoveResponse extends jspb.Message { 

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): HumanMoveResponse.AsObject;
    static toObject(includeInstance: boolean, msg: HumanMoveResponse): HumanMoveResponse.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: HumanMoveResponse, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): HumanMoveResponse;
    static deserializeBinaryFromReader(message: HumanMoveResponse, reader: jspb.BinaryReader): HumanMoveResponse;
}

export namespace HumanMoveResponse {
    export type AsObject = {
    }
}

export class HumanClickRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): HumanClickRequest;

    hasPageId(): boolean;
    clearPageId(): void;
    getPageId(): string | undefined;
    setPageId(value: string): HumanClickRequest;
    getX(): number;
    setX(value: number): HumanClickRequest;
    getY(): number;
    setY(value: number): HumanClickRequest;
    getDoubleCheckPause(): boolean;
    setDoubleCheckPause(value: boolean): HumanClickRequest;

    hasProfile(): boolean;
    clearProfile(): void;
    getProfile(): HumanProfile | undefined;
    setProfile(value?: HumanProfile): HumanClickRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): HumanClickRequest.AsObject;
    static toObject(includeInstance: boolean, msg: HumanClickRequest): HumanClickRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: HumanClickRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): HumanClickRequest;
    static deserializeBinaryFromReader(message: HumanClickRequest, reader: jspb.BinaryReader): HumanClickRequest;
}

export namespace HumanClickRequest {
    export type AsObject = {
        sessionId: string,
        pageId?: string,
        x: number,
        y: number,
        doubleCheckPause: boolean,
        profile?: HumanProfile.AsObject,
    }
}

export class HumanClickResponse extends jspb.Message { 

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): HumanClickResponse.AsObject;
    static toObject(includeInstance: boolean, msg: HumanClickResponse): HumanClickResponse.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: HumanClickResponse, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): HumanClickResponse;
    static deserializeBinaryFromReader(message: HumanClickResponse, reader: jspb.BinaryReader): HumanClickResponse;
}

export namespace HumanClickResponse {
    export type AsObject = {
    }
}

export class HumanWheelScrollRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): HumanWheelScrollRequest;

    hasPageId(): boolean;
    clearPageId(): void;
    getPageId(): string | undefined;
    setPageId(value: string): HumanWheelScrollRequest;
    getDeltaY(): number;
    setDeltaY(value: number): HumanWheelScrollRequest;

    hasAnchorX(): boolean;
    clearAnchorX(): void;
    getAnchorX(): number | undefined;
    setAnchorX(value: number): HumanWheelScrollRequest;

    hasAnchorY(): boolean;
    clearAnchorY(): void;
    getAnchorY(): number | undefined;
    setAnchorY(value: number): HumanWheelScrollRequest;

    hasProfile(): boolean;
    clearProfile(): void;
    getProfile(): HumanProfile | undefined;
    setProfile(value?: HumanProfile): HumanWheelScrollRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): HumanWheelScrollRequest.AsObject;
    static toObject(includeInstance: boolean, msg: HumanWheelScrollRequest): HumanWheelScrollRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: HumanWheelScrollRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): HumanWheelScrollRequest;
    static deserializeBinaryFromReader(message: HumanWheelScrollRequest, reader: jspb.BinaryReader): HumanWheelScrollRequest;
}

export namespace HumanWheelScrollRequest {
    export type AsObject = {
        sessionId: string,
        pageId?: string,
        deltaY: number,
        anchorX?: number,
        anchorY?: number,
        profile?: HumanProfile.AsObject,
    }
}

export class HumanWheelScrollResponse extends jspb.Message { 
    getFinalX(): number;
    setFinalX(value: number): HumanWheelScrollResponse;
    getFinalY(): number;
    setFinalY(value: number): HumanWheelScrollResponse;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): HumanWheelScrollResponse.AsObject;
    static toObject(includeInstance: boolean, msg: HumanWheelScrollResponse): HumanWheelScrollResponse.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: HumanWheelScrollResponse, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): HumanWheelScrollResponse;
    static deserializeBinaryFromReader(message: HumanWheelScrollResponse, reader: jspb.BinaryReader): HumanWheelScrollResponse;
}

export namespace HumanWheelScrollResponse {
    export type AsObject = {
        finalX: number,
        finalY: number,
    }
}

export class WaitForToggleConfirmationRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): WaitForToggleConfirmationRequest;

    hasPageId(): boolean;
    clearPageId(): void;
    getPageId(): string | undefined;
    setPageId(value: string): WaitForToggleConfirmationRequest;
    getFbAdId(): string;
    setFbAdId(value: string): WaitForToggleConfirmationRequest;
    getExpectedChecked(): string;
    setExpectedChecked(value: string): WaitForToggleConfirmationRequest;
    getRequiredReads(): number;
    setRequiredReads(value: number): WaitForToggleConfirmationRequest;
    clearPollDelaysSecondsList(): void;
    getPollDelaysSecondsList(): Array<number>;
    setPollDelaysSecondsList(value: Array<number>): WaitForToggleConfirmationRequest;
    addPollDelaysSeconds(value: number, index?: number): number;
    getMaxScrollPassesRestore(): number;
    setMaxScrollPassesRestore(value: number): WaitForToggleConfirmationRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): WaitForToggleConfirmationRequest.AsObject;
    static toObject(includeInstance: boolean, msg: WaitForToggleConfirmationRequest): WaitForToggleConfirmationRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: WaitForToggleConfirmationRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): WaitForToggleConfirmationRequest;
    static deserializeBinaryFromReader(message: WaitForToggleConfirmationRequest, reader: jspb.BinaryReader): WaitForToggleConfirmationRequest;
}

export namespace WaitForToggleConfirmationRequest {
    export type AsObject = {
        sessionId: string,
        pageId?: string,
        fbAdId: string,
        expectedChecked: string,
        requiredReads: number,
        pollDelaysSecondsList: Array<number>,
        maxScrollPassesRestore: number,
    }
}

export class WaitForToggleConfirmationResponse extends jspb.Message { 
    getSuccess(): boolean;
    setSuccess(value: boolean): WaitForToggleConfirmationResponse;
    getMessage(): string;
    setMessage(value: string): WaitForToggleConfirmationResponse;
    getFinalAriaChecked(): string;
    setFinalAriaChecked(value: string): WaitForToggleConfirmationResponse;
    getReadsMatched(): number;
    setReadsMatched(value: number): WaitForToggleConfirmationResponse;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): WaitForToggleConfirmationResponse.AsObject;
    static toObject(includeInstance: boolean, msg: WaitForToggleConfirmationResponse): WaitForToggleConfirmationResponse.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: WaitForToggleConfirmationResponse, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): WaitForToggleConfirmationResponse;
    static deserializeBinaryFromReader(message: WaitForToggleConfirmationResponse, reader: jspb.BinaryReader): WaitForToggleConfirmationResponse;
}

export namespace WaitForToggleConfirmationResponse {
    export type AsObject = {
        success: boolean,
        message: string,
        finalAriaChecked: string,
        readsMatched: number,
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

export class HumanProfile extends jspb.Message { 
    getSpeedFactor(): number;
    setSpeedFactor(value: number): HumanProfile;
    getJitterFactor(): number;
    setJitterFactor(value: number): HumanProfile;
    getPauseFactor(): number;
    setPauseFactor(value: number): HumanProfile;
    getOvershootChance(): number;
    setOvershootChance(value: number): HumanProfile;
    getIdleChance(): number;
    setIdleChance(value: number): HumanProfile;
    getIdleDurationMin(): number;
    setIdleDurationMin(value: number): HumanProfile;
    getIdleDurationMax(): number;
    setIdleDurationMax(value: number): HumanProfile;
    getBezierStepsMin(): number;
    setBezierStepsMin(value: number): HumanProfile;
    getBezierStepsMax(): number;
    setBezierStepsMax(value: number): HumanProfile;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): HumanProfile.AsObject;
    static toObject(includeInstance: boolean, msg: HumanProfile): HumanProfile.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: HumanProfile, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): HumanProfile;
    static deserializeBinaryFromReader(message: HumanProfile, reader: jspb.BinaryReader): HumanProfile;
}

export namespace HumanProfile {
    export type AsObject = {
        speedFactor: number,
        jitterFactor: number,
        pauseFactor: number,
        overshootChance: number,
        idleChance: number,
        idleDurationMin: number,
        idleDurationMax: number,
        bezierStepsMin: number,
        bezierStepsMax: number,
    }
}
