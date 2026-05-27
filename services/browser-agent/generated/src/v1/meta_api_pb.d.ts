// package: fb_agent.meta_api.v1
// file: v1/meta_api.proto

/* tslint:disable */
/* eslint-disable */

import * as jspb from "google-protobuf";

export class ExecuteGraphCallRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): ExecuteGraphCallRequest;
    getMethod(): string;
    setMethod(value: string): ExecuteGraphCallRequest;
    getEndpoint(): string;
    setEndpoint(value: string): ExecuteGraphCallRequest;

    getQueryParamsMap(): jspb.Map<string, string>;
    clearQueryParamsMap(): void;
    getBodyJson(): string;
    setBodyJson(value: string): ExecuteGraphCallRequest;

    hasTimeoutMs(): boolean;
    clearTimeoutMs(): void;
    getTimeoutMs(): number | undefined;
    setTimeoutMs(value: number): ExecuteGraphCallRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): ExecuteGraphCallRequest.AsObject;
    static toObject(includeInstance: boolean, msg: ExecuteGraphCallRequest): ExecuteGraphCallRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: ExecuteGraphCallRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): ExecuteGraphCallRequest;
    static deserializeBinaryFromReader(message: ExecuteGraphCallRequest, reader: jspb.BinaryReader): ExecuteGraphCallRequest;
}

export namespace ExecuteGraphCallRequest {
    export type AsObject = {
        sessionId: string,
        method: string,
        endpoint: string,

        queryParamsMap: Array<[string, string]>,
        bodyJson: string,
        timeoutMs?: number,
    }
}

export class ExecuteGraphCallResponse extends jspb.Message { 
    getStatusCode(): number;
    setStatusCode(value: number): ExecuteGraphCallResponse;
    getResponseJson(): string;
    setResponseJson(value: string): ExecuteGraphCallResponse;
    getDurationMs(): number;
    setDurationMs(value: number): ExecuteGraphCallResponse;

    hasError(): boolean;
    clearError(): void;
    getError(): GraphApiError | undefined;
    setError(value?: GraphApiError): ExecuteGraphCallResponse;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): ExecuteGraphCallResponse.AsObject;
    static toObject(includeInstance: boolean, msg: ExecuteGraphCallResponse): ExecuteGraphCallResponse.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: ExecuteGraphCallResponse, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): ExecuteGraphCallResponse;
    static deserializeBinaryFromReader(message: ExecuteGraphCallResponse, reader: jspb.BinaryReader): ExecuteGraphCallResponse;
}

export namespace ExecuteGraphCallResponse {
    export type AsObject = {
        statusCode: number,
        responseJson: string,
        durationMs: number,
        error?: GraphApiError.AsObject,
    }
}

export class GraphApiError extends jspb.Message { 
    getCode(): number;
    setCode(value: number): GraphApiError;
    getSubcode(): number;
    setSubcode(value: number): GraphApiError;
    getType(): string;
    setType(value: string): GraphApiError;
    getMessage(): string;
    setMessage(value: string): GraphApiError;
    getFbtraceId(): string;
    setFbtraceId(value: string): GraphApiError;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): GraphApiError.AsObject;
    static toObject(includeInstance: boolean, msg: GraphApiError): GraphApiError.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: GraphApiError, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): GraphApiError;
    static deserializeBinaryFromReader(message: GraphApiError, reader: jspb.BinaryReader): GraphApiError;
}

export namespace GraphApiError {
    export type AsObject = {
        code: number,
        subcode: number,
        type: string,
        message: string,
        fbtraceId: string,
    }
}

export class CheckMetaApiHealthRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): CheckMetaApiHealthRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): CheckMetaApiHealthRequest.AsObject;
    static toObject(includeInstance: boolean, msg: CheckMetaApiHealthRequest): CheckMetaApiHealthRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: CheckMetaApiHealthRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): CheckMetaApiHealthRequest;
    static deserializeBinaryFromReader(message: CheckMetaApiHealthRequest, reader: jspb.BinaryReader): CheckMetaApiHealthRequest;
}

export namespace CheckMetaApiHealthRequest {
    export type AsObject = {
        sessionId: string,
    }
}

export class CheckMetaApiHealthResponse extends jspb.Message { 
    getHealthy(): boolean;
    setHealthy(value: boolean): CheckMetaApiHealthResponse;
    getCurrentUrl(): string;
    setCurrentUrl(value: string): CheckMetaApiHealthResponse;
    getTokenPresent(): boolean;
    setTokenPresent(value: boolean): CheckMetaApiHealthResponse;
    getTokenLength(): number;
    setTokenLength(value: number): CheckMetaApiHealthResponse;
    getDetail(): string;
    setDetail(value: string): CheckMetaApiHealthResponse;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): CheckMetaApiHealthResponse.AsObject;
    static toObject(includeInstance: boolean, msg: CheckMetaApiHealthResponse): CheckMetaApiHealthResponse.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: CheckMetaApiHealthResponse, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): CheckMetaApiHealthResponse;
    static deserializeBinaryFromReader(message: CheckMetaApiHealthResponse, reader: jspb.BinaryReader): CheckMetaApiHealthResponse;
}

export namespace CheckMetaApiHealthResponse {
    export type AsObject = {
        healthy: boolean,
        currentUrl: string,
        tokenPresent: boolean,
        tokenLength: number,
        detail: string,
    }
}

export class UploadImageRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): UploadImageRequest;
    getAdAccountId(): string;
    setAdAccountId(value: string): UploadImageRequest;
    getFilename(): string;
    setFilename(value: string): UploadImageRequest;
    getContentType(): string;
    setContentType(value: string): UploadImageRequest;
    getFileBytes(): Uint8Array | string;
    getFileBytes_asU8(): Uint8Array;
    getFileBytes_asB64(): string;
    setFileBytes(value: Uint8Array | string): UploadImageRequest;
    getImageUrl(): string;
    setImageUrl(value: string): UploadImageRequest;
    getName(): string;
    setName(value: string): UploadImageRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): UploadImageRequest.AsObject;
    static toObject(includeInstance: boolean, msg: UploadImageRequest): UploadImageRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: UploadImageRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): UploadImageRequest;
    static deserializeBinaryFromReader(message: UploadImageRequest, reader: jspb.BinaryReader): UploadImageRequest;
}

export namespace UploadImageRequest {
    export type AsObject = {
        sessionId: string,
        adAccountId: string,
        filename: string,
        contentType: string,
        fileBytes: Uint8Array | string,
        imageUrl: string,
        name: string,
    }
}

export class UploadImageResponse extends jspb.Message { 
    getImageHash(): string;
    setImageHash(value: string): UploadImageResponse;
    getOk(): boolean;
    setOk(value: boolean): UploadImageResponse;
    getError(): string;
    setError(value: string): UploadImageResponse;
    getUrl(): string;
    setUrl(value: string): UploadImageResponse;
    getDurationMs(): number;
    setDurationMs(value: number): UploadImageResponse;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): UploadImageResponse.AsObject;
    static toObject(includeInstance: boolean, msg: UploadImageResponse): UploadImageResponse.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: UploadImageResponse, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): UploadImageResponse;
    static deserializeBinaryFromReader(message: UploadImageResponse, reader: jspb.BinaryReader): UploadImageResponse;
}

export namespace UploadImageResponse {
    export type AsObject = {
        imageHash: string,
        ok: boolean,
        error: string,
        url: string,
        durationMs: number,
    }
}

export class UploadVideoChunk extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): UploadVideoChunk;
    getAdAccountId(): string;
    setAdAccountId(value: string): UploadVideoChunk;
    getFilename(): string;
    setFilename(value: string): UploadVideoChunk;
    getFileSize(): number;
    setFileSize(value: number): UploadVideoChunk;
    getChunkBytes(): Uint8Array | string;
    getChunkBytes_asU8(): Uint8Array;
    getChunkBytes_asB64(): string;
    setChunkBytes(value: Uint8Array | string): UploadVideoChunk;
    getChunkIndex(): number;
    setChunkIndex(value: number): UploadVideoChunk;
    getIsLastChunk(): boolean;
    setIsLastChunk(value: boolean): UploadVideoChunk;
    getIsInit(): boolean;
    setIsInit(value: boolean): UploadVideoChunk;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): UploadVideoChunk.AsObject;
    static toObject(includeInstance: boolean, msg: UploadVideoChunk): UploadVideoChunk.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: UploadVideoChunk, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): UploadVideoChunk;
    static deserializeBinaryFromReader(message: UploadVideoChunk, reader: jspb.BinaryReader): UploadVideoChunk;
}

export namespace UploadVideoChunk {
    export type AsObject = {
        sessionId: string,
        adAccountId: string,
        filename: string,
        fileSize: number,
        chunkBytes: Uint8Array | string,
        chunkIndex: number,
        isLastChunk: boolean,
        isInit: boolean,
    }
}

export class UploadVideoResponse extends jspb.Message { 
    getVideoId(): string;
    setVideoId(value: string): UploadVideoResponse;
    getOk(): boolean;
    setOk(value: boolean): UploadVideoResponse;
    getError(): string;
    setError(value: string): UploadVideoResponse;
    getDurationMs(): number;
    setDurationMs(value: number): UploadVideoResponse;
    getChunksProcessed(): number;
    setChunksProcessed(value: number): UploadVideoResponse;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): UploadVideoResponse.AsObject;
    static toObject(includeInstance: boolean, msg: UploadVideoResponse): UploadVideoResponse.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: UploadVideoResponse, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): UploadVideoResponse;
    static deserializeBinaryFromReader(message: UploadVideoResponse, reader: jspb.BinaryReader): UploadVideoResponse;
}

export namespace UploadVideoResponse {
    export type AsObject = {
        videoId: string,
        ok: boolean,
        error: string,
        durationMs: number,
        chunksProcessed: number,
    }
}
