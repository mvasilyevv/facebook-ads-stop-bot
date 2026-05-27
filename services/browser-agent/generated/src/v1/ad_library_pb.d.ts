// package: fb_agent.ad_library.v1
// file: v1/ad_library.proto

/* tslint:disable */
/* eslint-disable */

import * as jspb from "google-protobuf";

export class SearchAdsRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): SearchAdsRequest;
    getCountry(): string;
    setCountry(value: string): SearchAdsRequest;
    getQuery(): string;
    setQuery(value: string): SearchAdsRequest;
    getActiveStatus(): string;
    setActiveStatus(value: string): SearchAdsRequest;
    getAdType(): string;
    setAdType(value: string): SearchAdsRequest;

    hasTimeoutMs(): boolean;
    clearTimeoutMs(): void;
    getTimeoutMs(): number | undefined;
    setTimeoutMs(value: number): SearchAdsRequest;
    getSearchType(): string;
    setSearchType(value: string): SearchAdsRequest;

    hasMaxPages(): boolean;
    clearMaxPages(): void;
    getMaxPages(): number | undefined;
    setMaxPages(value: number): SearchAdsRequest;

    hasPageSize(): boolean;
    clearPageSize(): void;
    getPageSize(): number | undefined;
    setPageSize(value: number): SearchAdsRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): SearchAdsRequest.AsObject;
    static toObject(includeInstance: boolean, msg: SearchAdsRequest): SearchAdsRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: SearchAdsRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): SearchAdsRequest;
    static deserializeBinaryFromReader(message: SearchAdsRequest, reader: jspb.BinaryReader): SearchAdsRequest;
}

export namespace SearchAdsRequest {
    export type AsObject = {
        sessionId: string,
        country: string,
        query: string,
        activeStatus: string,
        adType: string,
        timeoutMs?: number,
        searchType: string,
        maxPages?: number,
        pageSize?: number,
    }
}

export class SearchAdsResponse extends jspb.Message { 
    getAdCount(): number;
    setAdCount(value: number): SearchAdsResponse;
    getAdsJson(): string;
    setAdsJson(value: string): SearchAdsResponse;
    getDurationMs(): number;
    setDurationMs(value: number): SearchAdsResponse;
    getPagesFetched(): number;
    setPagesFetched(value: number): SearchAdsResponse;

    hasError(): boolean;
    clearError(): void;
    getError(): AdLibraryError | undefined;
    setError(value?: AdLibraryError): SearchAdsResponse;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): SearchAdsResponse.AsObject;
    static toObject(includeInstance: boolean, msg: SearchAdsResponse): SearchAdsResponse.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: SearchAdsResponse, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): SearchAdsResponse;
    static deserializeBinaryFromReader(message: SearchAdsResponse, reader: jspb.BinaryReader): SearchAdsResponse;
}

export namespace SearchAdsResponse {
    export type AsObject = {
        adCount: number,
        adsJson: string,
        durationMs: number,
        pagesFetched: number,
        error?: AdLibraryError.AsObject,
    }
}

export class AdLibraryError extends jspb.Message { 
    getCode(): number;
    setCode(value: number): AdLibraryError;
    getType(): string;
    setType(value: string): AdLibraryError;
    getMessage(): string;
    setMessage(value: string): AdLibraryError;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): AdLibraryError.AsObject;
    static toObject(includeInstance: boolean, msg: AdLibraryError): AdLibraryError.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: AdLibraryError, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): AdLibraryError;
    static deserializeBinaryFromReader(message: AdLibraryError, reader: jspb.BinaryReader): AdLibraryError;
}

export namespace AdLibraryError {
    export type AsObject = {
        code: number,
        type: string,
        message: string,
    }
}

export class SearchAdsBatchRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): SearchAdsBatchRequest;
    getCountry(): string;
    setCountry(value: string): SearchAdsBatchRequest;
    clearQueriesList(): void;
    getQueriesList(): Array<string>;
    setQueriesList(value: Array<string>): SearchAdsBatchRequest;
    addQueries(value: string, index?: number): string;
    getActiveStatus(): string;
    setActiveStatus(value: string): SearchAdsBatchRequest;
    getAdType(): string;
    setAdType(value: string): SearchAdsBatchRequest;

    hasPerQueryTimeoutMs(): boolean;
    clearPerQueryTimeoutMs(): void;
    getPerQueryTimeoutMs(): number | undefined;
    setPerQueryTimeoutMs(value: number): SearchAdsBatchRequest;
    getSearchType(): string;
    setSearchType(value: string): SearchAdsBatchRequest;

    hasMaxPages(): boolean;
    clearMaxPages(): void;
    getMaxPages(): number | undefined;
    setMaxPages(value: number): SearchAdsBatchRequest;

    hasPageSize(): boolean;
    clearPageSize(): void;
    getPageSize(): number | undefined;
    setPageSize(value: number): SearchAdsBatchRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): SearchAdsBatchRequest.AsObject;
    static toObject(includeInstance: boolean, msg: SearchAdsBatchRequest): SearchAdsBatchRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: SearchAdsBatchRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): SearchAdsBatchRequest;
    static deserializeBinaryFromReader(message: SearchAdsBatchRequest, reader: jspb.BinaryReader): SearchAdsBatchRequest;
}

export namespace SearchAdsBatchRequest {
    export type AsObject = {
        sessionId: string,
        country: string,
        queriesList: Array<string>,
        activeStatus: string,
        adType: string,
        perQueryTimeoutMs?: number,
        searchType: string,
        maxPages?: number,
        pageSize?: number,
    }
}

export class SearchAdsBatchResponse extends jspb.Message { 
    clearResultsList(): void;
    getResultsList(): Array<QueryResult>;
    setResultsList(value: Array<QueryResult>): SearchAdsBatchResponse;
    addResults(value?: QueryResult, index?: number): QueryResult;
    getTotalDurationMs(): number;
    setTotalDurationMs(value: number): SearchAdsBatchResponse;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): SearchAdsBatchResponse.AsObject;
    static toObject(includeInstance: boolean, msg: SearchAdsBatchResponse): SearchAdsBatchResponse.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: SearchAdsBatchResponse, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): SearchAdsBatchResponse;
    static deserializeBinaryFromReader(message: SearchAdsBatchResponse, reader: jspb.BinaryReader): SearchAdsBatchResponse;
}

export namespace SearchAdsBatchResponse {
    export type AsObject = {
        resultsList: Array<QueryResult.AsObject>,
        totalDurationMs: number,
    }
}

export class QueryResult extends jspb.Message { 
    getQuery(): string;
    setQuery(value: string): QueryResult;
    getAdCount(): number;
    setAdCount(value: number): QueryResult;
    getAdsJson(): string;
    setAdsJson(value: string): QueryResult;
    getDurationMs(): number;
    setDurationMs(value: number): QueryResult;
    getPagesFetched(): number;
    setPagesFetched(value: number): QueryResult;

    hasError(): boolean;
    clearError(): void;
    getError(): AdLibraryError | undefined;
    setError(value?: AdLibraryError): QueryResult;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): QueryResult.AsObject;
    static toObject(includeInstance: boolean, msg: QueryResult): QueryResult.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: QueryResult, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): QueryResult;
    static deserializeBinaryFromReader(message: QueryResult, reader: jspb.BinaryReader): QueryResult;
}

export namespace QueryResult {
    export type AsObject = {
        query: string,
        adCount: number,
        adsJson: string,
        durationMs: number,
        pagesFetched: number,
        error?: AdLibraryError.AsObject,
    }
}

export class CheckAdLibraryHealthRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): CheckAdLibraryHealthRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): CheckAdLibraryHealthRequest.AsObject;
    static toObject(includeInstance: boolean, msg: CheckAdLibraryHealthRequest): CheckAdLibraryHealthRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: CheckAdLibraryHealthRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): CheckAdLibraryHealthRequest;
    static deserializeBinaryFromReader(message: CheckAdLibraryHealthRequest, reader: jspb.BinaryReader): CheckAdLibraryHealthRequest;
}

export namespace CheckAdLibraryHealthRequest {
    export type AsObject = {
        sessionId: string,
    }
}

export class CheckAdLibraryHealthResponse extends jspb.Message { 
    getHealthy(): boolean;
    setHealthy(value: boolean): CheckAdLibraryHealthResponse;
    getDetail(): string;
    setDetail(value: string): CheckAdLibraryHealthResponse;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): CheckAdLibraryHealthResponse.AsObject;
    static toObject(includeInstance: boolean, msg: CheckAdLibraryHealthResponse): CheckAdLibraryHealthResponse.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: CheckAdLibraryHealthResponse, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): CheckAdLibraryHealthResponse;
    static deserializeBinaryFromReader(message: CheckAdLibraryHealthResponse, reader: jspb.BinaryReader): CheckAdLibraryHealthResponse;
}

export namespace CheckAdLibraryHealthResponse {
    export type AsObject = {
        healthy: boolean,
        detail: string,
    }
}
