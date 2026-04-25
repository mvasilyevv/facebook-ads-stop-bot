// package: fb_agent.scanner.v1
// file: v1/scanner.proto

/* tslint:disable */
/* eslint-disable */

import * as grpc from "grpc";
import * as v1_scanner_pb from "../v1/scanner_pb";

interface IScannerServiceService extends grpc.ServiceDefinition<grpc.UntypedServiceImplementation> {
    runScanCycle: IScannerServiceService_IRunScanCycle;
    refreshTable: IScannerServiceService_IRefreshTable;
    parseVisibleRows: IScannerServiceService_IParseVisibleRows;
    scrollAndParse: IScannerServiceService_IScrollAndParse;
    waitForDomStable: IScannerServiceService_IWaitForDomStable;
    resetScroll: IScannerServiceService_IResetScroll;
    getScrollMetrics: IScannerServiceService_IGetScrollMetrics;
    getVisibleRowIds: IScannerServiceService_IGetVisibleRowIds;
    findToggleCell: IScannerServiceService_IFindToggleCell;
    readToggleState: IScannerServiceService_IReadToggleState;
    toggleAd: IScannerServiceService_IToggleAd;
    humanMove: IScannerServiceService_IHumanMove;
    humanClick: IScannerServiceService_IHumanClick;
    humanWheelScroll: IScannerServiceService_IHumanWheelScroll;
    waitForToggleConfirmation: IScannerServiceService_IWaitForToggleConfirmation;
    validateColumns: IScannerServiceService_IValidateColumns;
    applyColumnWidths: IScannerServiceService_IApplyColumnWidths;
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
interface IScannerServiceService_IRefreshTable extends grpc.MethodDefinition<v1_scanner_pb.RefreshTableRequest, v1_scanner_pb.RefreshTableResponse> {
    path: "/fb_agent.scanner.v1.ScannerService/RefreshTable";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_scanner_pb.RefreshTableRequest>;
    requestDeserialize: grpc.deserialize<v1_scanner_pb.RefreshTableRequest>;
    responseSerialize: grpc.serialize<v1_scanner_pb.RefreshTableResponse>;
    responseDeserialize: grpc.deserialize<v1_scanner_pb.RefreshTableResponse>;
}
interface IScannerServiceService_IParseVisibleRows extends grpc.MethodDefinition<v1_scanner_pb.ParseVisibleRowsRequest, v1_scanner_pb.ParseVisibleRowsResponse> {
    path: "/fb_agent.scanner.v1.ScannerService/ParseVisibleRows";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_scanner_pb.ParseVisibleRowsRequest>;
    requestDeserialize: grpc.deserialize<v1_scanner_pb.ParseVisibleRowsRequest>;
    responseSerialize: grpc.serialize<v1_scanner_pb.ParseVisibleRowsResponse>;
    responseDeserialize: grpc.deserialize<v1_scanner_pb.ParseVisibleRowsResponse>;
}
interface IScannerServiceService_IScrollAndParse extends grpc.MethodDefinition<v1_scanner_pb.ScrollAndParseRequest, v1_scanner_pb.ScrollAndParseResponse> {
    path: "/fb_agent.scanner.v1.ScannerService/ScrollAndParse";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_scanner_pb.ScrollAndParseRequest>;
    requestDeserialize: grpc.deserialize<v1_scanner_pb.ScrollAndParseRequest>;
    responseSerialize: grpc.serialize<v1_scanner_pb.ScrollAndParseResponse>;
    responseDeserialize: grpc.deserialize<v1_scanner_pb.ScrollAndParseResponse>;
}
interface IScannerServiceService_IWaitForDomStable extends grpc.MethodDefinition<v1_scanner_pb.WaitForDomStableRequest, v1_scanner_pb.WaitForDomStableResponse> {
    path: "/fb_agent.scanner.v1.ScannerService/WaitForDomStable";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_scanner_pb.WaitForDomStableRequest>;
    requestDeserialize: grpc.deserialize<v1_scanner_pb.WaitForDomStableRequest>;
    responseSerialize: grpc.serialize<v1_scanner_pb.WaitForDomStableResponse>;
    responseDeserialize: grpc.deserialize<v1_scanner_pb.WaitForDomStableResponse>;
}
interface IScannerServiceService_IResetScroll extends grpc.MethodDefinition<v1_scanner_pb.ResetScrollRequest, v1_scanner_pb.ResetScrollResponse> {
    path: "/fb_agent.scanner.v1.ScannerService/ResetScroll";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_scanner_pb.ResetScrollRequest>;
    requestDeserialize: grpc.deserialize<v1_scanner_pb.ResetScrollRequest>;
    responseSerialize: grpc.serialize<v1_scanner_pb.ResetScrollResponse>;
    responseDeserialize: grpc.deserialize<v1_scanner_pb.ResetScrollResponse>;
}
interface IScannerServiceService_IGetScrollMetrics extends grpc.MethodDefinition<v1_scanner_pb.GetScrollMetricsRequest, v1_scanner_pb.GetScrollMetricsResponse> {
    path: "/fb_agent.scanner.v1.ScannerService/GetScrollMetrics";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_scanner_pb.GetScrollMetricsRequest>;
    requestDeserialize: grpc.deserialize<v1_scanner_pb.GetScrollMetricsRequest>;
    responseSerialize: grpc.serialize<v1_scanner_pb.GetScrollMetricsResponse>;
    responseDeserialize: grpc.deserialize<v1_scanner_pb.GetScrollMetricsResponse>;
}
interface IScannerServiceService_IGetVisibleRowIds extends grpc.MethodDefinition<v1_scanner_pb.GetVisibleRowIdsRequest, v1_scanner_pb.GetVisibleRowIdsResponse> {
    path: "/fb_agent.scanner.v1.ScannerService/GetVisibleRowIds";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_scanner_pb.GetVisibleRowIdsRequest>;
    requestDeserialize: grpc.deserialize<v1_scanner_pb.GetVisibleRowIdsRequest>;
    responseSerialize: grpc.serialize<v1_scanner_pb.GetVisibleRowIdsResponse>;
    responseDeserialize: grpc.deserialize<v1_scanner_pb.GetVisibleRowIdsResponse>;
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
interface IScannerServiceService_IValidateColumns extends grpc.MethodDefinition<v1_scanner_pb.ValidateColumnsRequest, v1_scanner_pb.ValidateColumnsResponse> {
    path: "/fb_agent.scanner.v1.ScannerService/ValidateColumns";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_scanner_pb.ValidateColumnsRequest>;
    requestDeserialize: grpc.deserialize<v1_scanner_pb.ValidateColumnsRequest>;
    responseSerialize: grpc.serialize<v1_scanner_pb.ValidateColumnsResponse>;
    responseDeserialize: grpc.deserialize<v1_scanner_pb.ValidateColumnsResponse>;
}
interface IScannerServiceService_IApplyColumnWidths extends grpc.MethodDefinition<v1_scanner_pb.ApplyColumnWidthsRequest, v1_scanner_pb.ApplyColumnWidthsResponse> {
    path: "/fb_agent.scanner.v1.ScannerService/ApplyColumnWidths";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_scanner_pb.ApplyColumnWidthsRequest>;
    requestDeserialize: grpc.deserialize<v1_scanner_pb.ApplyColumnWidthsRequest>;
    responseSerialize: grpc.serialize<v1_scanner_pb.ApplyColumnWidthsResponse>;
    responseDeserialize: grpc.deserialize<v1_scanner_pb.ApplyColumnWidthsResponse>;
}

export const ScannerServiceService: IScannerServiceService;

export interface IScannerServiceServer {
    runScanCycle: grpc.handleServerStreamingCall<v1_scanner_pb.RunScanCycleRequest, v1_scanner_pb.ScanCycleEvent>;
    refreshTable: grpc.handleUnaryCall<v1_scanner_pb.RefreshTableRequest, v1_scanner_pb.RefreshTableResponse>;
    parseVisibleRows: grpc.handleUnaryCall<v1_scanner_pb.ParseVisibleRowsRequest, v1_scanner_pb.ParseVisibleRowsResponse>;
    scrollAndParse: grpc.handleUnaryCall<v1_scanner_pb.ScrollAndParseRequest, v1_scanner_pb.ScrollAndParseResponse>;
    waitForDomStable: grpc.handleUnaryCall<v1_scanner_pb.WaitForDomStableRequest, v1_scanner_pb.WaitForDomStableResponse>;
    resetScroll: grpc.handleUnaryCall<v1_scanner_pb.ResetScrollRequest, v1_scanner_pb.ResetScrollResponse>;
    getScrollMetrics: grpc.handleUnaryCall<v1_scanner_pb.GetScrollMetricsRequest, v1_scanner_pb.GetScrollMetricsResponse>;
    getVisibleRowIds: grpc.handleUnaryCall<v1_scanner_pb.GetVisibleRowIdsRequest, v1_scanner_pb.GetVisibleRowIdsResponse>;
    findToggleCell: grpc.handleUnaryCall<v1_scanner_pb.FindToggleCellRequest, v1_scanner_pb.FindToggleCellResponse>;
    readToggleState: grpc.handleUnaryCall<v1_scanner_pb.ReadToggleStateRequest, v1_scanner_pb.ReadToggleStateResponse>;
    toggleAd: grpc.handleUnaryCall<v1_scanner_pb.ToggleAdRequest, v1_scanner_pb.ToggleAdResponse>;
    humanMove: grpc.handleUnaryCall<v1_scanner_pb.HumanMoveRequest, v1_scanner_pb.HumanMoveResponse>;
    humanClick: grpc.handleUnaryCall<v1_scanner_pb.HumanClickRequest, v1_scanner_pb.HumanClickResponse>;
    humanWheelScroll: grpc.handleUnaryCall<v1_scanner_pb.HumanWheelScrollRequest, v1_scanner_pb.HumanWheelScrollResponse>;
    waitForToggleConfirmation: grpc.handleUnaryCall<v1_scanner_pb.WaitForToggleConfirmationRequest, v1_scanner_pb.WaitForToggleConfirmationResponse>;
    validateColumns: grpc.handleUnaryCall<v1_scanner_pb.ValidateColumnsRequest, v1_scanner_pb.ValidateColumnsResponse>;
    applyColumnWidths: grpc.handleUnaryCall<v1_scanner_pb.ApplyColumnWidthsRequest, v1_scanner_pb.ApplyColumnWidthsResponse>;
}

export interface IScannerServiceClient {
    runScanCycle(request: v1_scanner_pb.RunScanCycleRequest, options?: Partial<grpc.CallOptions>): grpc.ClientReadableStream<v1_scanner_pb.ScanCycleEvent>;
    runScanCycle(request: v1_scanner_pb.RunScanCycleRequest, metadata?: grpc.Metadata, options?: Partial<grpc.CallOptions>): grpc.ClientReadableStream<v1_scanner_pb.ScanCycleEvent>;
    refreshTable(request: v1_scanner_pb.RefreshTableRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.RefreshTableResponse) => void): grpc.ClientUnaryCall;
    refreshTable(request: v1_scanner_pb.RefreshTableRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.RefreshTableResponse) => void): grpc.ClientUnaryCall;
    refreshTable(request: v1_scanner_pb.RefreshTableRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.RefreshTableResponse) => void): grpc.ClientUnaryCall;
    parseVisibleRows(request: v1_scanner_pb.ParseVisibleRowsRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ParseVisibleRowsResponse) => void): grpc.ClientUnaryCall;
    parseVisibleRows(request: v1_scanner_pb.ParseVisibleRowsRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ParseVisibleRowsResponse) => void): grpc.ClientUnaryCall;
    parseVisibleRows(request: v1_scanner_pb.ParseVisibleRowsRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ParseVisibleRowsResponse) => void): grpc.ClientUnaryCall;
    scrollAndParse(request: v1_scanner_pb.ScrollAndParseRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ScrollAndParseResponse) => void): grpc.ClientUnaryCall;
    scrollAndParse(request: v1_scanner_pb.ScrollAndParseRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ScrollAndParseResponse) => void): grpc.ClientUnaryCall;
    scrollAndParse(request: v1_scanner_pb.ScrollAndParseRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ScrollAndParseResponse) => void): grpc.ClientUnaryCall;
    waitForDomStable(request: v1_scanner_pb.WaitForDomStableRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.WaitForDomStableResponse) => void): grpc.ClientUnaryCall;
    waitForDomStable(request: v1_scanner_pb.WaitForDomStableRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.WaitForDomStableResponse) => void): grpc.ClientUnaryCall;
    waitForDomStable(request: v1_scanner_pb.WaitForDomStableRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.WaitForDomStableResponse) => void): grpc.ClientUnaryCall;
    resetScroll(request: v1_scanner_pb.ResetScrollRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ResetScrollResponse) => void): grpc.ClientUnaryCall;
    resetScroll(request: v1_scanner_pb.ResetScrollRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ResetScrollResponse) => void): grpc.ClientUnaryCall;
    resetScroll(request: v1_scanner_pb.ResetScrollRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ResetScrollResponse) => void): grpc.ClientUnaryCall;
    getScrollMetrics(request: v1_scanner_pb.GetScrollMetricsRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.GetScrollMetricsResponse) => void): grpc.ClientUnaryCall;
    getScrollMetrics(request: v1_scanner_pb.GetScrollMetricsRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.GetScrollMetricsResponse) => void): grpc.ClientUnaryCall;
    getScrollMetrics(request: v1_scanner_pb.GetScrollMetricsRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.GetScrollMetricsResponse) => void): grpc.ClientUnaryCall;
    getVisibleRowIds(request: v1_scanner_pb.GetVisibleRowIdsRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.GetVisibleRowIdsResponse) => void): grpc.ClientUnaryCall;
    getVisibleRowIds(request: v1_scanner_pb.GetVisibleRowIdsRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.GetVisibleRowIdsResponse) => void): grpc.ClientUnaryCall;
    getVisibleRowIds(request: v1_scanner_pb.GetVisibleRowIdsRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.GetVisibleRowIdsResponse) => void): grpc.ClientUnaryCall;
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
    validateColumns(request: v1_scanner_pb.ValidateColumnsRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ValidateColumnsResponse) => void): grpc.ClientUnaryCall;
    validateColumns(request: v1_scanner_pb.ValidateColumnsRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ValidateColumnsResponse) => void): grpc.ClientUnaryCall;
    validateColumns(request: v1_scanner_pb.ValidateColumnsRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ValidateColumnsResponse) => void): grpc.ClientUnaryCall;
    applyColumnWidths(request: v1_scanner_pb.ApplyColumnWidthsRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ApplyColumnWidthsResponse) => void): grpc.ClientUnaryCall;
    applyColumnWidths(request: v1_scanner_pb.ApplyColumnWidthsRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ApplyColumnWidthsResponse) => void): grpc.ClientUnaryCall;
    applyColumnWidths(request: v1_scanner_pb.ApplyColumnWidthsRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ApplyColumnWidthsResponse) => void): grpc.ClientUnaryCall;
}

export class ScannerServiceClient extends grpc.Client implements IScannerServiceClient {
    constructor(address: string, credentials: grpc.ChannelCredentials, options?: object);
    public runScanCycle(request: v1_scanner_pb.RunScanCycleRequest, options?: Partial<grpc.CallOptions>): grpc.ClientReadableStream<v1_scanner_pb.ScanCycleEvent>;
    public runScanCycle(request: v1_scanner_pb.RunScanCycleRequest, metadata?: grpc.Metadata, options?: Partial<grpc.CallOptions>): grpc.ClientReadableStream<v1_scanner_pb.ScanCycleEvent>;
    public refreshTable(request: v1_scanner_pb.RefreshTableRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.RefreshTableResponse) => void): grpc.ClientUnaryCall;
    public refreshTable(request: v1_scanner_pb.RefreshTableRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.RefreshTableResponse) => void): grpc.ClientUnaryCall;
    public refreshTable(request: v1_scanner_pb.RefreshTableRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.RefreshTableResponse) => void): grpc.ClientUnaryCall;
    public parseVisibleRows(request: v1_scanner_pb.ParseVisibleRowsRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ParseVisibleRowsResponse) => void): grpc.ClientUnaryCall;
    public parseVisibleRows(request: v1_scanner_pb.ParseVisibleRowsRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ParseVisibleRowsResponse) => void): grpc.ClientUnaryCall;
    public parseVisibleRows(request: v1_scanner_pb.ParseVisibleRowsRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ParseVisibleRowsResponse) => void): grpc.ClientUnaryCall;
    public scrollAndParse(request: v1_scanner_pb.ScrollAndParseRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ScrollAndParseResponse) => void): grpc.ClientUnaryCall;
    public scrollAndParse(request: v1_scanner_pb.ScrollAndParseRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ScrollAndParseResponse) => void): grpc.ClientUnaryCall;
    public scrollAndParse(request: v1_scanner_pb.ScrollAndParseRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ScrollAndParseResponse) => void): grpc.ClientUnaryCall;
    public waitForDomStable(request: v1_scanner_pb.WaitForDomStableRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.WaitForDomStableResponse) => void): grpc.ClientUnaryCall;
    public waitForDomStable(request: v1_scanner_pb.WaitForDomStableRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.WaitForDomStableResponse) => void): grpc.ClientUnaryCall;
    public waitForDomStable(request: v1_scanner_pb.WaitForDomStableRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.WaitForDomStableResponse) => void): grpc.ClientUnaryCall;
    public resetScroll(request: v1_scanner_pb.ResetScrollRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ResetScrollResponse) => void): grpc.ClientUnaryCall;
    public resetScroll(request: v1_scanner_pb.ResetScrollRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ResetScrollResponse) => void): grpc.ClientUnaryCall;
    public resetScroll(request: v1_scanner_pb.ResetScrollRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ResetScrollResponse) => void): grpc.ClientUnaryCall;
    public getScrollMetrics(request: v1_scanner_pb.GetScrollMetricsRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.GetScrollMetricsResponse) => void): grpc.ClientUnaryCall;
    public getScrollMetrics(request: v1_scanner_pb.GetScrollMetricsRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.GetScrollMetricsResponse) => void): grpc.ClientUnaryCall;
    public getScrollMetrics(request: v1_scanner_pb.GetScrollMetricsRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.GetScrollMetricsResponse) => void): grpc.ClientUnaryCall;
    public getVisibleRowIds(request: v1_scanner_pb.GetVisibleRowIdsRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.GetVisibleRowIdsResponse) => void): grpc.ClientUnaryCall;
    public getVisibleRowIds(request: v1_scanner_pb.GetVisibleRowIdsRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.GetVisibleRowIdsResponse) => void): grpc.ClientUnaryCall;
    public getVisibleRowIds(request: v1_scanner_pb.GetVisibleRowIdsRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.GetVisibleRowIdsResponse) => void): grpc.ClientUnaryCall;
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
    public validateColumns(request: v1_scanner_pb.ValidateColumnsRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ValidateColumnsResponse) => void): grpc.ClientUnaryCall;
    public validateColumns(request: v1_scanner_pb.ValidateColumnsRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ValidateColumnsResponse) => void): grpc.ClientUnaryCall;
    public validateColumns(request: v1_scanner_pb.ValidateColumnsRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ValidateColumnsResponse) => void): grpc.ClientUnaryCall;
    public applyColumnWidths(request: v1_scanner_pb.ApplyColumnWidthsRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ApplyColumnWidthsResponse) => void): grpc.ClientUnaryCall;
    public applyColumnWidths(request: v1_scanner_pb.ApplyColumnWidthsRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ApplyColumnWidthsResponse) => void): grpc.ClientUnaryCall;
    public applyColumnWidths(request: v1_scanner_pb.ApplyColumnWidthsRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ApplyColumnWidthsResponse) => void): grpc.ClientUnaryCall;
}
