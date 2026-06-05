// package: fb_agent.scanner.v1
// file: v1/scanner.proto

/* tslint:disable */
/* eslint-disable */

import * as grpc from "grpc";
import * as v1_scanner_pb from "../v1/scanner_pb";

interface IScannerServiceService extends grpc.ServiceDefinition<grpc.UntypedServiceImplementation> {
    runScanCycle: IScannerServiceService_IRunScanCycle;
    hardReloadPage: IScannerServiceService_IHardReloadPage;
    listCampaigns: IScannerServiceService_IListCampaigns;
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
interface IScannerServiceService_IHardReloadPage extends grpc.MethodDefinition<v1_scanner_pb.HardReloadPageRequest, v1_scanner_pb.HardReloadPageResponse> {
    path: "/fb_agent.scanner.v1.ScannerService/HardReloadPage";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_scanner_pb.HardReloadPageRequest>;
    requestDeserialize: grpc.deserialize<v1_scanner_pb.HardReloadPageRequest>;
    responseSerialize: grpc.serialize<v1_scanner_pb.HardReloadPageResponse>;
    responseDeserialize: grpc.deserialize<v1_scanner_pb.HardReloadPageResponse>;
}
interface IScannerServiceService_IListCampaigns extends grpc.MethodDefinition<v1_scanner_pb.ListCampaignsRequest, v1_scanner_pb.ListCampaignsResponse> {
    path: "/fb_agent.scanner.v1.ScannerService/ListCampaigns";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_scanner_pb.ListCampaignsRequest>;
    requestDeserialize: grpc.deserialize<v1_scanner_pb.ListCampaignsRequest>;
    responseSerialize: grpc.serialize<v1_scanner_pb.ListCampaignsResponse>;
    responseDeserialize: grpc.deserialize<v1_scanner_pb.ListCampaignsResponse>;
}

export const ScannerServiceService: IScannerServiceService;

export interface IScannerServiceServer {
    runScanCycle: grpc.handleServerStreamingCall<v1_scanner_pb.RunScanCycleRequest, v1_scanner_pb.ScanCycleEvent>;
    hardReloadPage: grpc.handleUnaryCall<v1_scanner_pb.HardReloadPageRequest, v1_scanner_pb.HardReloadPageResponse>;
    listCampaigns: grpc.handleUnaryCall<v1_scanner_pb.ListCampaignsRequest, v1_scanner_pb.ListCampaignsResponse>;
}

export interface IScannerServiceClient {
    runScanCycle(request: v1_scanner_pb.RunScanCycleRequest, options?: Partial<grpc.CallOptions>): grpc.ClientReadableStream<v1_scanner_pb.ScanCycleEvent>;
    runScanCycle(request: v1_scanner_pb.RunScanCycleRequest, metadata?: grpc.Metadata, options?: Partial<grpc.CallOptions>): grpc.ClientReadableStream<v1_scanner_pb.ScanCycleEvent>;
    hardReloadPage(request: v1_scanner_pb.HardReloadPageRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.HardReloadPageResponse) => void): grpc.ClientUnaryCall;
    hardReloadPage(request: v1_scanner_pb.HardReloadPageRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.HardReloadPageResponse) => void): grpc.ClientUnaryCall;
    hardReloadPage(request: v1_scanner_pb.HardReloadPageRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.HardReloadPageResponse) => void): grpc.ClientUnaryCall;
    listCampaigns(request: v1_scanner_pb.ListCampaignsRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ListCampaignsResponse) => void): grpc.ClientUnaryCall;
    listCampaigns(request: v1_scanner_pb.ListCampaignsRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ListCampaignsResponse) => void): grpc.ClientUnaryCall;
    listCampaigns(request: v1_scanner_pb.ListCampaignsRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ListCampaignsResponse) => void): grpc.ClientUnaryCall;
}

export class ScannerServiceClient extends grpc.Client implements IScannerServiceClient {
    constructor(address: string, credentials: grpc.ChannelCredentials, options?: object);
    public runScanCycle(request: v1_scanner_pb.RunScanCycleRequest, options?: Partial<grpc.CallOptions>): grpc.ClientReadableStream<v1_scanner_pb.ScanCycleEvent>;
    public runScanCycle(request: v1_scanner_pb.RunScanCycleRequest, metadata?: grpc.Metadata, options?: Partial<grpc.CallOptions>): grpc.ClientReadableStream<v1_scanner_pb.ScanCycleEvent>;
    public hardReloadPage(request: v1_scanner_pb.HardReloadPageRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.HardReloadPageResponse) => void): grpc.ClientUnaryCall;
    public hardReloadPage(request: v1_scanner_pb.HardReloadPageRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.HardReloadPageResponse) => void): grpc.ClientUnaryCall;
    public hardReloadPage(request: v1_scanner_pb.HardReloadPageRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.HardReloadPageResponse) => void): grpc.ClientUnaryCall;
    public listCampaigns(request: v1_scanner_pb.ListCampaignsRequest, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ListCampaignsResponse) => void): grpc.ClientUnaryCall;
    public listCampaigns(request: v1_scanner_pb.ListCampaignsRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ListCampaignsResponse) => void): grpc.ClientUnaryCall;
    public listCampaigns(request: v1_scanner_pb.ListCampaignsRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_scanner_pb.ListCampaignsResponse) => void): grpc.ClientUnaryCall;
}
