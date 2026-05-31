// package: fb_agent.scanner.v1
// file: v1/scanner.proto

/* tslint:disable */
/* eslint-disable */

import * as grpc from "grpc";
import * as v1_scanner_pb from "../v1/scanner_pb";

interface IScannerServiceService extends grpc.ServiceDefinition<grpc.UntypedServiceImplementation> {
    runScanCycle: IScannerServiceService_IRunScanCycle;
    findToggleCell: IScannerServiceService_IFindToggleCell;
    readToggleState: IScannerServiceService_IReadToggleState;
    toggleAd: IScannerServiceService_IToggleAd;
    humanMove: IScannerServiceService_IHumanMove;
    humanClick: IScannerServiceService_IHumanClick;
    humanWheelScroll: IScannerServiceService_IHumanWheelScroll;
    waitForToggleConfirmation: IScannerServiceService_IWaitForToggleConfirmation;
    hardReloadPage: IScannerServiceService_IHardReloadPage;
}

interface IScannerServiceService_IRunScanCycle extends grpc.MethodDefinition<v1_scanner_pb.RunScanCycleRequest, v1_scanner_pb.ScanCycleEvent> {
    path: "/fb_agent.scanner.v1.ScannerService/RunScanCycle";
    requestStream: false;
    responseStream: true;
    requestSerialize: grpc.serialize<v1_scanner_pb.RunScanCycleRequest>;
    requestDeserialize: grpc.deserialize<v1_scanner_pb.RunScanCycleRequest>;
    responseSerialize: grpc.serialize<v1_scanner_pb.ScanCycleEvent>;
    responseDeserialize: grpc.deserialize<v1_scanner_pb.ScanCycleEvent>;
}
interface IScannerServiceService_IFindToggleCell extends grpc.MethodDefinition<v1_scanner_pb.FindToggleCellRequest, v1_scanner_pb.FindToggleCellResponse> {
    path: "/fb_agent.scanner.v1.ScannerService/FindToggleCell";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_scanner_pb.FindToggleCellRequest>;
    requestDeserialize: grpc.deserialize<v1_scanner_pb.FindToggleCellRequest>;
    responseSerialize: grpc.serialize<v1_scanner_pb.FindToggleCellResponse>;
    responseDeserialize: grpc.deserialize<v1_scanner_pb.FindToggleCellResponse>;
}
interface IScannerServiceService_IReadToggleState extends grpc.MethodDefinition<v1_scanner_pb.ReadToggleStateRequest, v1_scanner_pb.ReadToggleStateResponse> {
    path: "/fb_agent.scanner.v1.ScannerService/ReadToggleState";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_scanner_pb.ReadToggleStateRequest>;
    requestDeserialize: grpc.deserialize<v1_scanner_pb.ReadToggleStateRequest>;
    responseSerialize: grpc.serialize<v1_scanner_pb.ReadToggleStateResponse>;
    responseDeserialize: grpc.deserialize<v1_scanner_pb.ReadToggleStateResponse>;
}
interface IScannerServiceService_IToggleAd extends grpc.MethodDefinition<v1_scanner_pb.ToggleAdRequest, v1_scanner_pb.ToggleAdResponse> {
    path: "/fb_agent.scanner.v1.ScannerService/ToggleAd";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_scanner_pb.ToggleAdRequest>;
    requestDeserialize: grpc.deserialize<v1_scanner_pb.ToggleAdRequest>;
    responseSerialize: grpc.serialize<v1_scanner_pb.ToggleAdResponse>;
    responseDeserialize: grpc.deserialize<v1_scanner_pb.ToggleAdResponse>;
}
interface IScannerServiceService_IHumanMove extends grpc.MethodDefinition<v1_scanner_pb.HumanMoveRequest, v1_scanner_pb.HumanMoveResponse> {
    path: "/fb_agent.scanner.v1.ScannerService/HumanMove";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_scanner_pb.HumanMoveRequest>;
    requestDeserialize: grpc.deserialize<v1_scanner_pb.HumanMoveRequest>;
    responseSerialize: grpc.serialize<v1_scanner_pb.HumanMoveResponse>;
    responseDeserialize: grpc.deserialize<v1_scanner_pb.HumanMoveResponse>;
}
interface IScannerServiceService_IHumanClick extends grpc.MethodDefinition<v1_scanner_pb.HumanClickRequest, v1_scanner_pb.HumanClickResponse> {
    path: "/fb_agent.scanner.v1.ScannerService/HumanClick";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_scanner_pb.HumanClickRequest>;
    requestDeserialize: grpc.deserialize<v1_scanner_pb.HumanClickRequest>;
    responseSerialize: grpc.serialize<v1_scanner_pb.HumanClickResponse>;
    responseDeserialize: grpc.deserialize<v1_scanner_pb.HumanClickResponse>;
}
interface IScannerServiceService_IHumanWheelScroll extends grpc.MethodDefinition<v1_scanner_pb.HumanWheelScrollRequest, v1_scanner_pb.HumanWheelScrollResponse> {
    path: "/fb_agent.scanner.v1.ScannerService/HumanWheelScroll";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_scanner_pb.HumanWheelScrollRequest>;
    requestDeserialize: grpc.deserialize<v1_scanner_pb.HumanWheelScrollRequest>;
    responseSerialize: grpc.serialize<v1_scanner_pb.HumanWheelScrollResponse>;
    responseDeserialize: grpc.deserialize<v1_scanner_pb.HumanWheelScrollResponse>;
}
interface IScannerServiceService_IWaitForToggleConfirmation extends grpc.MethodDefinition<v1_scanner_pb.WaitForToggleConfirmationRequest, v1_scanner_pb.WaitForToggleConfirmationResponse> {
    path: "/fb_agent.scanner.v1.ScannerService/WaitForToggleConfirmation";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_scanner_pb.WaitForToggleConfirmationRequest>;
    requestDeserialize: grpc.deserialize<v1_scanner_pb.WaitForToggleConfirmationRequest>;
    responseSerialize: grpc.serialize<v1_scanner_pb.WaitForToggleConfirmationResponse>;
    responseDeserialize: grpc.deserialize<v1_scanner_pb.WaitForToggleConfirmationResponse>;
}
interface IScannerServiceService_IHardReloadPage extends grpc.MethodDefinition<v1_scanner_pb.HardReloadPageRequest, v1_scanner_pb.HardReloadPageResponse> {
    path: "/fb_agent.scanner.v1.ScannerService/HardReloadPage";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_scanner_pb.HardReloadPageRequest>;
    requestDeserialize: grpc.deserialize<v1_scanner_pb.HardReloadPageRequest>;
    responseSerialize: grpc.serialize<v1_scanner_pb.HardReloadPageResponse>;
    responseDeserialize: grpc.deserialize<v1_scanner_pb.HardReloadPageResponse>;
}

export const ScannerServiceService: IScannerServiceService;

export interface IScannerServiceServer {
    runScanCycle: grpc.handleServerStreamingCall<v1_scanner_pb.RunScanCycleRequest, v1_scanner_pb.ScanCycleEvent>;
    findToggleCell: grpc.handleUnaryCall<v1_scanner_pb.FindToggleCellRequest, v1_scanner_pb.FindToggleCellResponse>;
    readToggleState: grpc.handleUnaryCall<v1_scanner_pb.ReadToggleStateRequest, v1_scanner_pb.ReadToggleStateResponse>;
    toggleAd: grpc.handleUnaryCall<v1_scanner_pb.ToggleAdRequest, v1_scanner_pb.ToggleAdResponse>;
    humanMove: grpc.handleUnaryCall<v1_scanner_pb.HumanMoveRequest, v1_scanner_pb.HumanMoveResponse>;
    humanClick: grpc.handleUnaryCall<v1_scanner_pb.HumanClickRequest, v1_scanner_pb.HumanClickResponse>;
    humanWheelScroll: grpc.handleUnaryCall<v1_scanner_pb.HumanWheelScrollRequest, v1_scanner_pb.HumanWheelScrollResponse>;
    waitForToggleConfirmation: grpc.handleUnaryCall<v1_scanner_pb.WaitForToggleConfirmationRequest, v1_scanner_pb.WaitForToggleConfirmationResponse>;
    hardReloadPage: grpc.handleUnaryCall<v1_scanner_pb.HardReloadPageRequest, v1_scanner_pb.HardReloadPageResponse>;
}

export interface IScannerServiceClient {
    runScanCycle(request: v1_scanner_pb.RunScanCycleRequest, options?: Partial<grpc.CallOptions>): grpc.ClientReadableStream<v1_scanner_pb.ScanCycleEvent>;
    runScanCycle(request: v1_scanner_pb.RunScanCycleRequest, metadata?: grpc.Metadata, options?: Partial<grpc.CallOptions>): grpc.ClientReadableStream<v1_scanner_pb.ScanCycleEvent>;
    findToggleCell(request: v1_scanner_pb.FindToggleCellRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.FindToggleCellResponse) => void): grpc.ClientUnaryCall;
    findToggleCell(request: v1_scanner_pb.FindToggleCellRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.FindToggleCellResponse) => void): grpc.ClientUnaryCall;
    findToggleCell(request: v1_scanner_pb.FindToggleCellRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.FindToggleCellResponse) => void): grpc.ClientUnaryCall;
    readToggleState(request: v1_scanner_pb.ReadToggleStateRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ReadToggleStateResponse) => void): grpc.ClientUnaryCall;
    readToggleState(request: v1_scanner_pb.ReadToggleStateRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ReadToggleStateResponse) => void): grpc.ClientUnaryCall;
    readToggleState(request: v1_scanner_pb.ReadToggleStateRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ReadToggleStateResponse) => void): grpc.ClientUnaryCall;
    toggleAd(request: v1_scanner_pb.ToggleAdRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ToggleAdResponse) => void): grpc.ClientUnaryCall;
    toggleAd(request: v1_scanner_pb.ToggleAdRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ToggleAdResponse) => void): grpc.ClientUnaryCall;
    toggleAd(request: v1_scanner_pb.ToggleAdRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ToggleAdResponse) => void): grpc.ClientUnaryCall;
    humanMove(request: v1_scanner_pb.HumanMoveRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.HumanMoveResponse) => void): grpc.ClientUnaryCall;
    humanMove(request: v1_scanner_pb.HumanMoveRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.HumanMoveResponse) => void): grpc.ClientUnaryCall;
    humanMove(request: v1_scanner_pb.HumanMoveRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.HumanMoveResponse) => void): grpc.ClientUnaryCall;
    humanClick(request: v1_scanner_pb.HumanClickRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.HumanClickResponse) => void): grpc.ClientUnaryCall;
    humanClick(request: v1_scanner_pb.HumanClickRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.HumanClickResponse) => void): grpc.ClientUnaryCall;
    humanClick(request: v1_scanner_pb.HumanClickRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.HumanClickResponse) => void): grpc.ClientUnaryCall;
    humanWheelScroll(request: v1_scanner_pb.HumanWheelScrollRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.HumanWheelScrollResponse) => void): grpc.ClientUnaryCall;
    humanWheelScroll(request: v1_scanner_pb.HumanWheelScrollRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.HumanWheelScrollResponse) => void): grpc.ClientUnaryCall;
    humanWheelScroll(request: v1_scanner_pb.HumanWheelScrollRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.HumanWheelScrollResponse) => void): grpc.ClientUnaryCall;
    waitForToggleConfirmation(request: v1_scanner_pb.WaitForToggleConfirmationRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.WaitForToggleConfirmationResponse) => void): grpc.ClientUnaryCall;
    waitForToggleConfirmation(request: v1_scanner_pb.WaitForToggleConfirmationRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.WaitForToggleConfirmationResponse) => void): grpc.ClientUnaryCall;
    waitForToggleConfirmation(request: v1_scanner_pb.WaitForToggleConfirmationRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.WaitForToggleConfirmationResponse) => void): grpc.ClientUnaryCall;
    hardReloadPage(request: v1_scanner_pb.HardReloadPageRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.HardReloadPageResponse) => void): grpc.ClientUnaryCall;
    hardReloadPage(request: v1_scanner_pb.HardReloadPageRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.HardReloadPageResponse) => void): grpc.ClientUnaryCall;
    hardReloadPage(request: v1_scanner_pb.HardReloadPageRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.HardReloadPageResponse) => void): grpc.ClientUnaryCall;
}

export class ScannerServiceClient extends grpc.Client implements IScannerServiceClient {
    constructor(address: string, credentials: grpc.ChannelCredentials, options?: object);
    public runScanCycle(request: v1_scanner_pb.RunScanCycleRequest, options?: Partial<grpc.CallOptions>): grpc.ClientReadableStream<v1_scanner_pb.ScanCycleEvent>;
    public runScanCycle(request: v1_scanner_pb.RunScanCycleRequest, metadata?: grpc.Metadata, options?: Partial<grpc.CallOptions>): grpc.ClientReadableStream<v1_scanner_pb.ScanCycleEvent>;
    public findToggleCell(request: v1_scanner_pb.FindToggleCellRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.FindToggleCellResponse) => void): grpc.ClientUnaryCall;
    public findToggleCell(request: v1_scanner_pb.FindToggleCellRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.FindToggleCellResponse) => void): grpc.ClientUnaryCall;
    public findToggleCell(request: v1_scanner_pb.FindToggleCellRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.FindToggleCellResponse) => void): grpc.ClientUnaryCall;
    public readToggleState(request: v1_scanner_pb.ReadToggleStateRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ReadToggleStateResponse) => void): grpc.ClientUnaryCall;
    public readToggleState(request: v1_scanner_pb.ReadToggleStateRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ReadToggleStateResponse) => void): grpc.ClientUnaryCall;
    public readToggleState(request: v1_scanner_pb.ReadToggleStateRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ReadToggleStateResponse) => void): grpc.ClientUnaryCall;
    public toggleAd(request: v1_scanner_pb.ToggleAdRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ToggleAdResponse) => void): grpc.ClientUnaryCall;
    public toggleAd(request: v1_scanner_pb.ToggleAdRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ToggleAdResponse) => void): grpc.ClientUnaryCall;
    public toggleAd(request: v1_scanner_pb.ToggleAdRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ToggleAdResponse) => void): grpc.ClientUnaryCall;
    public humanMove(request: v1_scanner_pb.HumanMoveRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.HumanMoveResponse) => void): grpc.ClientUnaryCall;
    public humanMove(request: v1_scanner_pb.HumanMoveRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.HumanMoveResponse) => void): grpc.ClientUnaryCall;
    public humanMove(request: v1_scanner_pb.HumanMoveRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.HumanMoveResponse) => void): grpc.ClientUnaryCall;
    public humanClick(request: v1_scanner_pb.HumanClickRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.HumanClickResponse) => void): grpc.ClientUnaryCall;
    public humanClick(request: v1_scanner_pb.HumanClickRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.HumanClickResponse) => void): grpc.ClientUnaryCall;
    public humanClick(request: v1_scanner_pb.HumanClickRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.HumanClickResponse) => void): grpc.ClientUnaryCall;
    public humanWheelScroll(request: v1_scanner_pb.HumanWheelScrollRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.HumanWheelScrollResponse) => void): grpc.ClientUnaryCall;
    public humanWheelScroll(request: v1_scanner_pb.HumanWheelScrollRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.HumanWheelScrollResponse) => void): grpc.ClientUnaryCall;
    public humanWheelScroll(request: v1_scanner_pb.HumanWheelScrollRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.HumanWheelScrollResponse) => void): grpc.ClientUnaryCall;
    public waitForToggleConfirmation(request: v1_scanner_pb.WaitForToggleConfirmationRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.WaitForToggleConfirmationResponse) => void): grpc.ClientUnaryCall;
    public waitForToggleConfirmation(request: v1_scanner_pb.WaitForToggleConfirmationRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.WaitForToggleConfirmationResponse) => void): grpc.ClientUnaryCall;
    public waitForToggleConfirmation(request: v1_scanner_pb.WaitForToggleConfirmationRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.WaitForToggleConfirmationResponse) => void): grpc.ClientUnaryCall;
    public hardReloadPage(request: v1_scanner_pb.HardReloadPageRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.HardReloadPageResponse) => void): grpc.ClientUnaryCall;
    public hardReloadPage(request: v1_scanner_pb.HardReloadPageRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.HardReloadPageResponse) => void): grpc.ClientUnaryCall;
    public hardReloadPage(request: v1_scanner_pb.HardReloadPageRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.HardReloadPageResponse) => void): grpc.ClientUnaryCall;
}
