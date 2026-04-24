// package: fb_agent.browser_session.v1
// file: v1/browser_session.proto

/* tslint:disable */
/* eslint-disable */

import * as jspb from "google-protobuf";

export class StartBrowserRequest extends jspb.Message { 
    getVisionXToken(): string;
    setVisionXToken(value: string): StartBrowserRequest;
    getVisionApiUrl(): string;
    setVisionApiUrl(value: string): StartBrowserRequest;
    getVisionProfileId(): string;
    setVisionProfileId(value: string): StartBrowserRequest;

    hasVisionFolderId(): boolean;
    clearVisionFolderId(): void;
    getVisionFolderId(): string | undefined;
    setVisionFolderId(value: string): StartBrowserRequest;
    getViewportWidth(): number;
    setViewportWidth(value: number): StartBrowserRequest;
    getViewportHeight(): number;
    setViewportHeight(value: number): StartBrowserRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): StartBrowserRequest.AsObject;
    static toObject(includeInstance: boolean, msg: StartBrowserRequest): StartBrowserRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: StartBrowserRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): StartBrowserRequest;
    static deserializeBinaryFromReader(message: StartBrowserRequest, reader: jspb.BinaryReader): StartBrowserRequest;
}

export namespace StartBrowserRequest {
    export type AsObject = {
        visionXToken: string,
        visionApiUrl: string,
        visionProfileId: string,
        visionFolderId?: string,
        viewportWidth: number,
        viewportHeight: number,
    }
}

export class StartBrowserResponse extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): StartBrowserResponse;

    hasProfile(): boolean;
    clearProfile(): void;
    getProfile(): VisionProfile | undefined;
    setProfile(value?: VisionProfile): StartBrowserResponse;
    getInitialPageUrl(): string;
    setInitialPageUrl(value: string): StartBrowserResponse;
    clearPagesList(): void;
    getPagesList(): Array<PageInfo>;
    setPagesList(value: Array<PageInfo>): StartBrowserResponse;
    addPages(value?: PageInfo, index?: number): PageInfo;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): StartBrowserResponse.AsObject;
    static toObject(includeInstance: boolean, msg: StartBrowserResponse): StartBrowserResponse.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: StartBrowserResponse, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): StartBrowserResponse;
    static deserializeBinaryFromReader(message: StartBrowserResponse, reader: jspb.BinaryReader): StartBrowserResponse;
}

export namespace StartBrowserResponse {
    export type AsObject = {
        sessionId: string,
        profile?: VisionProfile.AsObject,
        initialPageUrl: string,
        pagesList: Array<PageInfo.AsObject>,
    }
}

export class VisionProfile extends jspb.Message { 
    getFolderId(): string;
    setFolderId(value: string): VisionProfile;
    getProfileId(): string;
    setProfileId(value: string): VisionProfile;
    getCdpPort(): number;
    setCdpPort(value: number): VisionProfile;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): VisionProfile.AsObject;
    static toObject(includeInstance: boolean, msg: VisionProfile): VisionProfile.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: VisionProfile, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): VisionProfile;
    static deserializeBinaryFromReader(message: VisionProfile, reader: jspb.BinaryReader): VisionProfile;
}

export namespace VisionProfile {
    export type AsObject = {
        folderId: string,
        profileId: string,
        cdpPort: number,
    }
}

export class PageInfo extends jspb.Message { 
    getPageId(): string;
    setPageId(value: string): PageInfo;
    getUrl(): string;
    setUrl(value: string): PageInfo;
    getTitle(): string;
    setTitle(value: string): PageInfo;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): PageInfo.AsObject;
    static toObject(includeInstance: boolean, msg: PageInfo): PageInfo.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: PageInfo, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): PageInfo;
    static deserializeBinaryFromReader(message: PageInfo, reader: jspb.BinaryReader): PageInfo;
}

export namespace PageInfo {
    export type AsObject = {
        pageId: string,
        url: string,
        title: string,
    }
}

export class DisconnectBrowserRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): DisconnectBrowserRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): DisconnectBrowserRequest.AsObject;
    static toObject(includeInstance: boolean, msg: DisconnectBrowserRequest): DisconnectBrowserRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: DisconnectBrowserRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): DisconnectBrowserRequest;
    static deserializeBinaryFromReader(message: DisconnectBrowserRequest, reader: jspb.BinaryReader): DisconnectBrowserRequest;
}

export namespace DisconnectBrowserRequest {
    export type AsObject = {
        sessionId: string,
    }
}

export class DisconnectBrowserResponse extends jspb.Message { 

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): DisconnectBrowserResponse.AsObject;
    static toObject(includeInstance: boolean, msg: DisconnectBrowserResponse): DisconnectBrowserResponse.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: DisconnectBrowserResponse, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): DisconnectBrowserResponse;
    static deserializeBinaryFromReader(message: DisconnectBrowserResponse, reader: jspb.BinaryReader): DisconnectBrowserResponse;
}

export namespace DisconnectBrowserResponse {
    export type AsObject = {
    }
}

export class StopBrowserRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): StopBrowserRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): StopBrowserRequest.AsObject;
    static toObject(includeInstance: boolean, msg: StopBrowserRequest): StopBrowserRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: StopBrowserRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): StopBrowserRequest;
    static deserializeBinaryFromReader(message: StopBrowserRequest, reader: jspb.BinaryReader): StopBrowserRequest;
}

export namespace StopBrowserRequest {
    export type AsObject = {
        sessionId: string,
    }
}

export class StopBrowserResponse extends jspb.Message { 

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): StopBrowserResponse.AsObject;
    static toObject(includeInstance: boolean, msg: StopBrowserResponse): StopBrowserResponse.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: StopBrowserResponse, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): StopBrowserResponse;
    static deserializeBinaryFromReader(message: StopBrowserResponse, reader: jspb.BinaryReader): StopBrowserResponse;
}

export namespace StopBrowserResponse {
    export type AsObject = {
    }
}

export class ReconnectBrowserRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): ReconnectBrowserRequest;
    getVisionXToken(): string;
    setVisionXToken(value: string): ReconnectBrowserRequest;
    getVisionApiUrl(): string;
    setVisionApiUrl(value: string): ReconnectBrowserRequest;
    getVisionProfileId(): string;
    setVisionProfileId(value: string): ReconnectBrowserRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): ReconnectBrowserRequest.AsObject;
    static toObject(includeInstance: boolean, msg: ReconnectBrowserRequest): ReconnectBrowserRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: ReconnectBrowserRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): ReconnectBrowserRequest;
    static deserializeBinaryFromReader(message: ReconnectBrowserRequest, reader: jspb.BinaryReader): ReconnectBrowserRequest;
}

export namespace ReconnectBrowserRequest {
    export type AsObject = {
        sessionId: string,
        visionXToken: string,
        visionApiUrl: string,
        visionProfileId: string,
    }
}

export class GetSessionInfoRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): GetSessionInfoRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): GetSessionInfoRequest.AsObject;
    static toObject(includeInstance: boolean, msg: GetSessionInfoRequest): GetSessionInfoRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: GetSessionInfoRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): GetSessionInfoRequest;
    static deserializeBinaryFromReader(message: GetSessionInfoRequest, reader: jspb.BinaryReader): GetSessionInfoRequest;
}

export namespace GetSessionInfoRequest {
    export type AsObject = {
        sessionId: string,
    }
}

export class GetSessionInfoResponse extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): GetSessionInfoResponse;
    getConnected(): boolean;
    setConnected(value: boolean): GetSessionInfoResponse;
    getCurrentUrl(): string;
    setCurrentUrl(value: string): GetSessionInfoResponse;
    clearPagesList(): void;
    getPagesList(): Array<PageInfo>;
    setPagesList(value: Array<PageInfo>): GetSessionInfoResponse;
    addPages(value?: PageInfo, index?: number): PageInfo;
    getConnectedSince(): number;
    setConnectedSince(value: number): GetSessionInfoResponse;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): GetSessionInfoResponse.AsObject;
    static toObject(includeInstance: boolean, msg: GetSessionInfoResponse): GetSessionInfoResponse.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: GetSessionInfoResponse, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): GetSessionInfoResponse;
    static deserializeBinaryFromReader(message: GetSessionInfoResponse, reader: jspb.BinaryReader): GetSessionInfoResponse;
}

export namespace GetSessionInfoResponse {
    export type AsObject = {
        sessionId: string,
        connected: boolean,
        currentUrl: string,
        pagesList: Array<PageInfo.AsObject>,
        connectedSince: number,
    }
}

export class NavigateRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): NavigateRequest;

    hasPageId(): boolean;
    clearPageId(): void;
    getPageId(): string | undefined;
    setPageId(value: string): NavigateRequest;
    getUrl(): string;
    setUrl(value: string): NavigateRequest;
    getWaitUntil(): string;
    setWaitUntil(value: string): NavigateRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): NavigateRequest.AsObject;
    static toObject(includeInstance: boolean, msg: NavigateRequest): NavigateRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: NavigateRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): NavigateRequest;
    static deserializeBinaryFromReader(message: NavigateRequest, reader: jspb.BinaryReader): NavigateRequest;
}

export namespace NavigateRequest {
    export type AsObject = {
        sessionId: string,
        pageId?: string,
        url: string,
        waitUntil: string,
    }
}

export class NavigateResponse extends jspb.Message { 
    getUrl(): string;
    setUrl(value: string): NavigateResponse;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): NavigateResponse.AsObject;
    static toObject(includeInstance: boolean, msg: NavigateResponse): NavigateResponse.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: NavigateResponse, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): NavigateResponse;
    static deserializeBinaryFromReader(message: NavigateResponse, reader: jspb.BinaryReader): NavigateResponse;
}

export namespace NavigateResponse {
    export type AsObject = {
        url: string,
    }
}

export class StreamSessionStatusRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): StreamSessionStatusRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): StreamSessionStatusRequest.AsObject;
    static toObject(includeInstance: boolean, msg: StreamSessionStatusRequest): StreamSessionStatusRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: StreamSessionStatusRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): StreamSessionStatusRequest;
    static deserializeBinaryFromReader(message: StreamSessionStatusRequest, reader: jspb.BinaryReader): StreamSessionStatusRequest;
}

export namespace StreamSessionStatusRequest {
    export type AsObject = {
        sessionId: string,
    }
}

export class SessionStatusEvent extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): SessionStatusEvent;
    getStatus(): string;
    setStatus(value: string): SessionStatusEvent;
    getDetail(): string;
    setDetail(value: string): SessionStatusEvent;
    getCurrentUrl(): string;
    setCurrentUrl(value: string): SessionStatusEvent;
    getTimestamp(): number;
    setTimestamp(value: number): SessionStatusEvent;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): SessionStatusEvent.AsObject;
    static toObject(includeInstance: boolean, msg: SessionStatusEvent): SessionStatusEvent.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: SessionStatusEvent, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): SessionStatusEvent;
    static deserializeBinaryFromReader(message: SessionStatusEvent, reader: jspb.BinaryReader): SessionStatusEvent;
}

export namespace SessionStatusEvent {
    export type AsObject = {
        sessionId: string,
        status: string,
        detail: string,
        currentUrl: string,
        timestamp: number,
    }
}
