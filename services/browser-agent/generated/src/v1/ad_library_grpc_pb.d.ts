// package: fb_agent.ad_library.v1
// file: v1/ad_library.proto

/* tslint:disable */
/* eslint-disable */

import * as grpc from "grpc";
import * as v1_ad_library_pb from "../v1/ad_library_pb";

interface IAdLibraryServiceService extends grpc.ServiceDefinition<grpc.UntypedServiceImplementation> {
    searchAds: IAdLibraryServiceService_ISearchAds;
    searchAdsBatch: IAdLibraryServiceService_ISearchAdsBatch;
    checkAdLibraryHealth: IAdLibraryServiceService_ICheckAdLibraryHealth;
}

interface IAdLibraryServiceService_ISearchAds extends grpc.MethodDefinition<v1_ad_library_pb.SearchAdsRequest, v1_ad_library_pb.SearchAdsResponse> {
    path: "/fb_agent.ad_library.v1.AdLibraryService/SearchAds";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_ad_library_pb.SearchAdsRequest>;
    requestDeserialize: grpc.deserialize<v1_ad_library_pb.SearchAdsRequest>;
    responseSerialize: grpc.serialize<v1_ad_library_pb.SearchAdsResponse>;
    responseDeserialize: grpc.deserialize<v1_ad_library_pb.SearchAdsResponse>;
}
interface IAdLibraryServiceService_ISearchAdsBatch extends grpc.MethodDefinition<v1_ad_library_pb.SearchAdsBatchRequest, v1_ad_library_pb.SearchAdsBatchResponse> {
    path: "/fb_agent.ad_library.v1.AdLibraryService/SearchAdsBatch";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_ad_library_pb.SearchAdsBatchRequest>;
    requestDeserialize: grpc.deserialize<v1_ad_library_pb.SearchAdsBatchRequest>;
    responseSerialize: grpc.serialize<v1_ad_library_pb.SearchAdsBatchResponse>;
    responseDeserialize: grpc.deserialize<v1_ad_library_pb.SearchAdsBatchResponse>;
}
interface IAdLibraryServiceService_ICheckAdLibraryHealth extends grpc.MethodDefinition<v1_ad_library_pb.CheckAdLibraryHealthRequest, v1_ad_library_pb.CheckAdLibraryHealthResponse> {
    path: "/fb_agent.ad_library.v1.AdLibraryService/CheckAdLibraryHealth";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_ad_library_pb.CheckAdLibraryHealthRequest>;
    requestDeserialize: grpc.deserialize<v1_ad_library_pb.CheckAdLibraryHealthRequest>;
    responseSerialize: grpc.serialize<v1_ad_library_pb.CheckAdLibraryHealthResponse>;
    responseDeserialize: grpc.deserialize<v1_ad_library_pb.CheckAdLibraryHealthResponse>;
}

export const AdLibraryServiceService: IAdLibraryServiceService;

export interface IAdLibraryServiceServer {
    searchAds: grpc.handleUnaryCall<v1_ad_library_pb.SearchAdsRequest, v1_ad_library_pb.SearchAdsResponse>;
    searchAdsBatch: grpc.handleUnaryCall<v1_ad_library_pb.SearchAdsBatchRequest, v1_ad_library_pb.SearchAdsBatchResponse>;
    checkAdLibraryHealth: grpc.handleUnaryCall<v1_ad_library_pb.CheckAdLibraryHealthRequest, v1_ad_library_pb.CheckAdLibraryHealthResponse>;
}

export interface IAdLibraryServiceClient {
    searchAds(request: v1_ad_library_pb.SearchAdsRequest, callback: (error: grpc.ServiceError | null, response: v1_ad_library_pb.SearchAdsResponse) => void): grpc.ClientUnaryCall;
    searchAds(request: v1_ad_library_pb.SearchAdsRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_ad_library_pb.SearchAdsResponse) => void): grpc.ClientUnaryCall;
    searchAds(request: v1_ad_library_pb.SearchAdsRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_ad_library_pb.SearchAdsResponse) => void): grpc.ClientUnaryCall;
    searchAdsBatch(request: v1_ad_library_pb.SearchAdsBatchRequest, callback: (error: grpc.ServiceError | null, response: v1_ad_library_pb.SearchAdsBatchResponse) => void): grpc.ClientUnaryCall;
    searchAdsBatch(request: v1_ad_library_pb.SearchAdsBatchRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_ad_library_pb.SearchAdsBatchResponse) => void): grpc.ClientUnaryCall;
    searchAdsBatch(request: v1_ad_library_pb.SearchAdsBatchRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_ad_library_pb.SearchAdsBatchResponse) => void): grpc.ClientUnaryCall;
    checkAdLibraryHealth(request: v1_ad_library_pb.CheckAdLibraryHealthRequest, callback: (error: grpc.ServiceError | null, response: v1_ad_library_pb.CheckAdLibraryHealthResponse) => void): grpc.ClientUnaryCall;
    checkAdLibraryHealth(request: v1_ad_library_pb.CheckAdLibraryHealthRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_ad_library_pb.CheckAdLibraryHealthResponse) => void): grpc.ClientUnaryCall;
    checkAdLibraryHealth(request: v1_ad_library_pb.CheckAdLibraryHealthRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_ad_library_pb.CheckAdLibraryHealthResponse) => void): grpc.ClientUnaryCall;
}

export class AdLibraryServiceClient extends grpc.Client implements IAdLibraryServiceClient {
    constructor(address: string, credentials: grpc.ChannelCredentials, options?: object);
    public searchAds(request: v1_ad_library_pb.SearchAdsRequest, callback: (error: grpc.ServiceError | null, response: v1_ad_library_pb.SearchAdsResponse) => void): grpc.ClientUnaryCall;
    public searchAds(request: v1_ad_library_pb.SearchAdsRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_ad_library_pb.SearchAdsResponse) => void): grpc.ClientUnaryCall;
    public searchAds(request: v1_ad_library_pb.SearchAdsRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_ad_library_pb.SearchAdsResponse) => void): grpc.ClientUnaryCall;
    public searchAdsBatch(request: v1_ad_library_pb.SearchAdsBatchRequest, callback: (error: grpc.ServiceError | null, response: v1_ad_library_pb.SearchAdsBatchResponse) => void): grpc.ClientUnaryCall;
    public searchAdsBatch(request: v1_ad_library_pb.SearchAdsBatchRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_ad_library_pb.SearchAdsBatchResponse) => void): grpc.ClientUnaryCall;
    public searchAdsBatch(request: v1_ad_library_pb.SearchAdsBatchRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_ad_library_pb.SearchAdsBatchResponse) => void): grpc.ClientUnaryCall;
    public checkAdLibraryHealth(request: v1_ad_library_pb.CheckAdLibraryHealthRequest, callback: (error: grpc.ServiceError | null, response: v1_ad_library_pb.CheckAdLibraryHealthResponse) => void): grpc.ClientUnaryCall;
    public checkAdLibraryHealth(request: v1_ad_library_pb.CheckAdLibraryHealthRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_ad_library_pb.CheckAdLibraryHealthResponse) => void): grpc.ClientUnaryCall;
    public checkAdLibraryHealth(request: v1_ad_library_pb.CheckAdLibraryHealthRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_ad_library_pb.CheckAdLibraryHealthResponse) => void): grpc.ClientUnaryCall;
}
