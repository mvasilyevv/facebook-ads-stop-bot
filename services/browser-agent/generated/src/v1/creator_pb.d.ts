// package: fb_agent.creator.v1
// file: v1/creator.proto

/* tslint:disable */
/* eslint-disable */

import * as jspb from "google-protobuf";

export class RunPlanRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): RunPlanRequest;

    hasPageId(): boolean;
    clearPageId(): void;
    getPageId(): string | undefined;
    setPageId(value: string): RunPlanRequest;
    getPlanJson(): string;
    setPlanJson(value: string): RunPlanRequest;
    getVariablesJson(): string;
    setVariablesJson(value: string): RunPlanRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): RunPlanRequest.AsObject;
    static toObject(includeInstance: boolean, msg: RunPlanRequest): RunPlanRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: RunPlanRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): RunPlanRequest;
    static deserializeBinaryFromReader(message: RunPlanRequest, reader: jspb.BinaryReader): RunPlanRequest;
}

export namespace RunPlanRequest {
    export type AsObject = {
        sessionId: string,
        pageId?: string,
        planJson: string,
        variablesJson: string,
    }
}

export class PlanEvent extends jspb.Message { 

    hasStarted(): boolean;
    clearStarted(): void;
    getStarted(): StepStarted | undefined;
    setStarted(value?: StepStarted): PlanEvent;

    hasFinished(): boolean;
    clearFinished(): void;
    getFinished(): StepFinished | undefined;
    setFinished(value?: StepFinished): PlanEvent;

    hasFailed(): boolean;
    clearFailed(): void;
    getFailed(): StepFailed | undefined;
    setFailed(value?: StepFailed): PlanEvent;

    hasSkipped(): boolean;
    clearSkipped(): void;
    getSkipped(): StepSkipped | undefined;
    setSkipped(value?: StepSkipped): PlanEvent;

    hasComplete(): boolean;
    clearComplete(): void;
    getComplete(): PlanComplete | undefined;
    setComplete(value?: PlanComplete): PlanEvent;

    hasCheckpoint(): boolean;
    clearCheckpoint(): void;
    getCheckpoint(): CheckpointDetected | undefined;
    setCheckpoint(value?: CheckpointDetected): PlanEvent;

    getEventCase(): PlanEvent.EventCase;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): PlanEvent.AsObject;
    static toObject(includeInstance: boolean, msg: PlanEvent): PlanEvent.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: PlanEvent, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): PlanEvent;
    static deserializeBinaryFromReader(message: PlanEvent, reader: jspb.BinaryReader): PlanEvent;
}

export namespace PlanEvent {
    export type AsObject = {
        started?: StepStarted.AsObject,
        finished?: StepFinished.AsObject,
        failed?: StepFailed.AsObject,
        skipped?: StepSkipped.AsObject,
        complete?: PlanComplete.AsObject,
        checkpoint?: CheckpointDetected.AsObject,
    }

    export enum EventCase {
        EVENT_NOT_SET = 0,
        STARTED = 1,
        FINISHED = 2,
        FAILED = 3,
        SKIPPED = 4,
        COMPLETE = 5,
        CHECKPOINT = 6,
    }

}

export class StepStarted extends jspb.Message { 
    getStep(): string;
    setStep(value: string): StepStarted;
    getIndex(): number;
    setIndex(value: number): StepStarted;
    getTimestampMs(): number;
    setTimestampMs(value: number): StepStarted;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): StepStarted.AsObject;
    static toObject(includeInstance: boolean, msg: StepStarted): StepStarted.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: StepStarted, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): StepStarted;
    static deserializeBinaryFromReader(message: StepStarted, reader: jspb.BinaryReader): StepStarted;
}

export namespace StepStarted {
    export type AsObject = {
        step: string,
        index: number,
        timestampMs: number,
    }
}

export class StepFinished extends jspb.Message { 
    getStep(): string;
    setStep(value: string): StepFinished;
    getIndex(): number;
    setIndex(value: number): StepFinished;
    getTimestampMs(): number;
    setTimestampMs(value: number): StepFinished;
    getDetailJson(): string;
    setDetailJson(value: string): StepFinished;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): StepFinished.AsObject;
    static toObject(includeInstance: boolean, msg: StepFinished): StepFinished.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: StepFinished, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): StepFinished;
    static deserializeBinaryFromReader(message: StepFinished, reader: jspb.BinaryReader): StepFinished;
}

export namespace StepFinished {
    export type AsObject = {
        step: string,
        index: number,
        timestampMs: number,
        detailJson: string,
    }
}

export class StepFailed extends jspb.Message { 
    getStep(): string;
    setStep(value: string): StepFailed;
    getIndex(): number;
    setIndex(value: number): StepFailed;
    getError(): string;
    setError(value: string): StepFailed;
    getTimestampMs(): number;
    setTimestampMs(value: number): StepFailed;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): StepFailed.AsObject;
    static toObject(includeInstance: boolean, msg: StepFailed): StepFailed.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: StepFailed, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): StepFailed;
    static deserializeBinaryFromReader(message: StepFailed, reader: jspb.BinaryReader): StepFailed;
}

export namespace StepFailed {
    export type AsObject = {
        step: string,
        index: number,
        error: string,
        timestampMs: number,
    }
}

export class StepSkipped extends jspb.Message { 
    getStep(): string;
    setStep(value: string): StepSkipped;
    getIndex(): number;
    setIndex(value: number): StepSkipped;
    getReason(): string;
    setReason(value: string): StepSkipped;
    getTimestampMs(): number;
    setTimestampMs(value: number): StepSkipped;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): StepSkipped.AsObject;
    static toObject(includeInstance: boolean, msg: StepSkipped): StepSkipped.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: StepSkipped, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): StepSkipped;
    static deserializeBinaryFromReader(message: StepSkipped, reader: jspb.BinaryReader): StepSkipped;
}

export namespace StepSkipped {
    export type AsObject = {
        step: string,
        index: number,
        reason: string,
        timestampMs: number,
    }
}

export class PlanComplete extends jspb.Message { 
    getOk(): boolean;
    setOk(value: boolean): PlanComplete;
    getError(): string;
    setError(value: string): PlanComplete;
    getTotalSteps(): number;
    setTotalSteps(value: number): PlanComplete;
    getDurationMs(): number;
    setDurationMs(value: number): PlanComplete;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): PlanComplete.AsObject;
    static toObject(includeInstance: boolean, msg: PlanComplete): PlanComplete.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: PlanComplete, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): PlanComplete;
    static deserializeBinaryFromReader(message: PlanComplete, reader: jspb.BinaryReader): PlanComplete;
}

export namespace PlanComplete {
    export type AsObject = {
        ok: boolean,
        error: string,
        totalSteps: number,
        durationMs: number,
    }
}

export class CheckpointDetected extends jspb.Message { 
    getUrl(): string;
    setUrl(value: string): CheckpointDetected;
    getDetail(): string;
    setDetail(value: string): CheckpointDetected;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): CheckpointDetected.AsObject;
    static toObject(includeInstance: boolean, msg: CheckpointDetected): CheckpointDetected.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: CheckpointDetected, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): CheckpointDetected;
    static deserializeBinaryFromReader(message: CheckpointDetected, reader: jspb.BinaryReader): CheckpointDetected;
}

export namespace CheckpointDetected {
    export type AsObject = {
        url: string,
        detail: string,
    }
}

export class StartRecordingRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): StartRecordingRequest;

    hasPageId(): boolean;
    clearPageId(): void;
    getPageId(): string | undefined;
    setPageId(value: string): StartRecordingRequest;
    getPlanName(): string;
    setPlanName(value: string): StartRecordingRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): StartRecordingRequest.AsObject;
    static toObject(includeInstance: boolean, msg: StartRecordingRequest): StartRecordingRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: StartRecordingRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): StartRecordingRequest;
    static deserializeBinaryFromReader(message: StartRecordingRequest, reader: jspb.BinaryReader): StartRecordingRequest;
}

export namespace StartRecordingRequest {
    export type AsObject = {
        sessionId: string,
        pageId?: string,
        planName: string,
    }
}

export class StartRecordingResponse extends jspb.Message { 
    getStarted(): boolean;
    setStarted(value: boolean): StartRecordingResponse;
    getMessage(): string;
    setMessage(value: string): StartRecordingResponse;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): StartRecordingResponse.AsObject;
    static toObject(includeInstance: boolean, msg: StartRecordingResponse): StartRecordingResponse.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: StartRecordingResponse, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): StartRecordingResponse;
    static deserializeBinaryFromReader(message: StartRecordingResponse, reader: jspb.BinaryReader): StartRecordingResponse;
}

export namespace StartRecordingResponse {
    export type AsObject = {
        started: boolean,
        message: string,
    }
}

export class StopRecordingRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): StopRecordingRequest;

    hasPageId(): boolean;
    clearPageId(): void;
    getPageId(): string | undefined;
    setPageId(value: string): StopRecordingRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): StopRecordingRequest.AsObject;
    static toObject(includeInstance: boolean, msg: StopRecordingRequest): StopRecordingRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: StopRecordingRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): StopRecordingRequest;
    static deserializeBinaryFromReader(message: StopRecordingRequest, reader: jspb.BinaryReader): StopRecordingRequest;
}

export namespace StopRecordingRequest {
    export type AsObject = {
        sessionId: string,
        pageId?: string,
    }
}

export class StopRecordingResponse extends jspb.Message { 
    getStopped(): boolean;
    setStopped(value: boolean): StopRecordingResponse;
    getPlanJson(): string;
    setPlanJson(value: string): StopRecordingResponse;
    getRecordedSteps(): number;
    setRecordedSteps(value: number): StopRecordingResponse;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): StopRecordingResponse.AsObject;
    static toObject(includeInstance: boolean, msg: StopRecordingResponse): StopRecordingResponse.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: StopRecordingResponse, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): StopRecordingResponse;
    static deserializeBinaryFromReader(message: StopRecordingResponse, reader: jspb.BinaryReader): StopRecordingResponse;
}

export namespace StopRecordingResponse {
    export type AsObject = {
        stopped: boolean,
        planJson: string,
        recordedSteps: number,
    }
}

export class GetRecorderStatusRequest extends jspb.Message { 
    getSessionId(): string;
    setSessionId(value: string): GetRecorderStatusRequest;

    hasPageId(): boolean;
    clearPageId(): void;
    getPageId(): string | undefined;
    setPageId(value: string): GetRecorderStatusRequest;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): GetRecorderStatusRequest.AsObject;
    static toObject(includeInstance: boolean, msg: GetRecorderStatusRequest): GetRecorderStatusRequest.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: GetRecorderStatusRequest, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): GetRecorderStatusRequest;
    static deserializeBinaryFromReader(message: GetRecorderStatusRequest, reader: jspb.BinaryReader): GetRecorderStatusRequest;
}

export namespace GetRecorderStatusRequest {
    export type AsObject = {
        sessionId: string,
        pageId?: string,
    }
}

export class GetRecorderStatusResponse extends jspb.Message { 
    getRecording(): boolean;
    setRecording(value: boolean): GetRecorderStatusResponse;
    getPlanName(): string;
    setPlanName(value: string): GetRecorderStatusResponse;
    getRecordedSteps(): number;
    setRecordedSteps(value: number): GetRecorderStatusResponse;

    serializeBinary(): Uint8Array;
    toObject(includeInstance?: boolean): GetRecorderStatusResponse.AsObject;
    static toObject(includeInstance: boolean, msg: GetRecorderStatusResponse): GetRecorderStatusResponse.AsObject;
    static extensions: {[key: number]: jspb.ExtensionFieldInfo<jspb.Message>};
    static extensionsBinary: {[key: number]: jspb.ExtensionFieldBinaryInfo<jspb.Message>};
    static serializeBinaryToWriter(message: GetRecorderStatusResponse, writer: jspb.BinaryWriter): void;
    static deserializeBinary(bytes: Uint8Array): GetRecorderStatusResponse;
    static deserializeBinaryFromReader(message: GetRecorderStatusResponse, reader: jspb.BinaryReader): GetRecorderStatusResponse;
}

export namespace GetRecorderStatusResponse {
    export type AsObject = {
        recording: boolean,
        planName: string,
        recordedSteps: number,
    }
}
