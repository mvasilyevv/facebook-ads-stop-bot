// package: fb_agent.meta_api.v1
// file: v1/meta_api.proto

/* tslint:disable */
/* eslint-disable */

import * as grpc from "grpc";
import * as v1_meta_api_pb from "../v1/meta_api_pb";

interface IMetaApiServiceService extends grpc.ServiceDefinition<grpc.UntypedServiceImplementation> {
    executeGraphCall: IMetaApiServiceService_IExecuteGraphCall;
    checkMetaApiHealth: IMetaApiServiceService_ICheckMetaApiHealth;
    uploadImage: IMetaApiServiceService_IUploadImage;
    uploadVideo: IMetaApiServiceService_IUploadVideo;
}

interface IMetaApiServiceService_IExecuteGraphCall extends grpc.MethodDefinition<v1_meta_api_pb.ExecuteGraphCallRequest, v1_meta_api_pb.ExecuteGraphCallResponse> {
    path: "/fb_agent.meta_api.v1.MetaApiService/ExecuteGraphCall";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_meta_api_pb.ExecuteGraphCallRequest>;
    requestDeserialize: grpc.deserialize<v1_meta_api_pb.ExecuteGraphCallRequest>;
    responseSerialize: grpc.serialize<v1_meta_api_pb.ExecuteGraphCallResponse>;
    responseDeserialize: grpc.deserialize<v1_meta_api_pb.ExecuteGraphCallResponse>;
}
interface IMetaApiServiceService_ICheckMetaApiHealth extends grpc.MethodDefinition<v1_meta_api_pb.CheckMetaApiHealthRequest, v1_meta_api_pb.CheckMetaApiHealthResponse> {
    path: "/fb_agent.meta_api.v1.MetaApiService/CheckMetaApiHealth";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_meta_api_pb.CheckMetaApiHealthRequest>;
    requestDeserialize: grpc.deserialize<v1_meta_api_pb.CheckMetaApiHealthRequest>;
    responseSerialize: grpc.serialize<v1_meta_api_pb.CheckMetaApiHealthResponse>;
    responseDeserialize: grpc.deserialize<v1_meta_api_pb.CheckMetaApiHealthResponse>;
}
interface IMetaApiServiceService_IUploadImage extends grpc.MethodDefinition<v1_meta_api_pb.UploadImageRequest, v1_meta_api_pb.UploadImageResponse> {
    path: "/fb_agent.meta_api.v1.MetaApiService/UploadImage";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_meta_api_pb.UploadImageRequest>;
    requestDeserialize: grpc.deserialize<v1_meta_api_pb.UploadImageRequest>;
    responseSerialize: grpc.serialize<v1_meta_api_pb.UploadImageResponse>;
    responseDeserialize: grpc.deserialize<v1_meta_api_pb.UploadImageResponse>;
}
interface IMetaApiServiceService_IUploadVideo extends grpc.MethodDefinition<v1_meta_api_pb.UploadVideoChunk, v1_meta_api_pb.UploadVideoResponse> {
    path: "/fb_agent.meta_api.v1.MetaApiService/UploadVideo";
    requestStream: true;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_meta_api_pb.UploadVideoChunk>;
    requestDeserialize: grpc.deserialize<v1_meta_api_pb.UploadVideoChunk>;
    responseSerialize: grpc.serialize<v1_meta_api_pb.UploadVideoResponse>;
    responseDeserialize: grpc.deserialize<v1_meta_api_pb.UploadVideoResponse>;
}

export const MetaApiServiceService: IMetaApiServiceService;

export interface IMetaApiServiceServer {
    executeGraphCall: grpc.handleUnaryCall<v1_meta_api_pb.ExecuteGraphCallRequest, v1_meta_api_pb.ExecuteGraphCallResponse>;
    checkMetaApiHealth: grpc.handleUnaryCall<v1_meta_api_pb.CheckMetaApiHealthRequest, v1_meta_api_pb.CheckMetaApiHealthResponse>;
    uploadImage: grpc.handleUnaryCall<v1_meta_api_pb.UploadImageRequest, v1_meta_api_pb.UploadImageResponse>;
    uploadVideo: grpc.handleClientStreamingCall<v1_meta_api_pb.UploadVideoChunk, v1_meta_api_pb.UploadVideoResponse>;
}

export interface IMetaApiServiceClient {
    executeGraphCall(request: v1_meta_api_pb.ExecuteGraphCallRequest, callback: (error: grpc.ServiceError | null, response: v1_meta_api_pb.ExecuteGraphCallResponse) => void): grpc.ClientUnaryCall;
    executeGraphCall(request: v1_meta_api_pb.ExecuteGraphCallRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_meta_api_pb.ExecuteGraphCallResponse) => void): grpc.ClientUnaryCall;
    executeGraphCall(request: v1_meta_api_pb.ExecuteGraphCallRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_meta_api_pb.ExecuteGraphCallResponse) => void): grpc.ClientUnaryCall;
    checkMetaApiHealth(request: v1_meta_api_pb.CheckMetaApiHealthRequest, callback: (error: grpc.ServiceError | null, response: v1_meta_api_pb.CheckMetaApiHealthResponse) => void): grpc.ClientUnaryCall;
    checkMetaApiHealth(request: v1_meta_api_pb.CheckMetaApiHealthRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_meta_api_pb.CheckMetaApiHealthResponse) => void): grpc.ClientUnaryCall;
    checkMetaApiHealth(request: v1_meta_api_pb.CheckMetaApiHealthRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_meta_api_pb.CheckMetaApiHealthResponse) => void): grpc.ClientUnaryCall;
    uploadImage(request: v1_meta_api_pb.UploadImageRequest, callback: (error: grpc.ServiceError | null, response: v1_meta_api_pb.UploadImageResponse) => void): grpc.ClientUnaryCall;
    uploadImage(request: v1_meta_api_pb.UploadImageRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_meta_api_pb.UploadImageResponse) => void): grpc.ClientUnaryCall;
    uploadImage(request: v1_meta_api_pb.UploadImageRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_meta_api_pb.UploadImageResponse) => void): grpc.ClientUnaryCall;
    uploadVideo(callback: (error: grpc.ServiceError | null, response: v1_meta_api_pb.UploadVideoResponse) => void): grpc.ClientWritableStream<v1_meta_api_pb.UploadVideoChunk>;
    uploadVideo(metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_meta_api_pb.UploadVideoResponse) => void): grpc.ClientWritableStream<v1_meta_api_pb.UploadVideoChunk>;
    uploadVideo(options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_meta_api_pb.UploadVideoResponse) => void): grpc.ClientWritableStream<v1_meta_api_pb.UploadVideoChunk>;
    uploadVideo(metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_meta_api_pb.UploadVideoResponse) => void): grpc.ClientWritableStream<v1_meta_api_pb.UploadVideoChunk>;
}

export class MetaApiServiceClient extends grpc.Client implements IMetaApiServiceClient {
    constructor(address: string, credentials: grpc.ChannelCredentials, options?: object);
    public executeGraphCall(request: v1_meta_api_pb.ExecuteGraphCallRequest, callback: (error: grpc.ServiceError | null, response: v1_meta_api_pb.ExecuteGraphCallResponse) => void): grpc.ClientUnaryCall;
    public executeGraphCall(request: v1_meta_api_pb.ExecuteGraphCallRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_meta_api_pb.ExecuteGraphCallResponse) => void): grpc.ClientUnaryCall;
    public executeGraphCall(request: v1_meta_api_pb.ExecuteGraphCallRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_meta_api_pb.ExecuteGraphCallResponse) => void): grpc.ClientUnaryCall;
    public checkMetaApiHealth(request: v1_meta_api_pb.CheckMetaApiHealthRequest, callback: (error: grpc.ServiceError | null, response: v1_meta_api_pb.CheckMetaApiHealthResponse) => void): grpc.ClientUnaryCall;
    public checkMetaApiHealth(request: v1_meta_api_pb.CheckMetaApiHealthRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_meta_api_pb.CheckMetaApiHealthResponse) => void): grpc.ClientUnaryCall;
    public checkMetaApiHealth(request: v1_meta_api_pb.CheckMetaApiHealthRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_meta_api_pb.CheckMetaApiHealthResponse) => void): grpc.ClientUnaryCall;
    public uploadImage(request: v1_meta_api_pb.UploadImageRequest, callback: (error: grpc.ServiceError | null, response: v1_meta_api_pb.UploadImageResponse) => void): grpc.ClientUnaryCall;
    public uploadImage(request: v1_meta_api_pb.UploadImageRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_meta_api_pb.UploadImageResponse) => void): grpc.ClientUnaryCall;
    public uploadImage(request: v1_meta_api_pb.UploadImageRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_meta_api_pb.UploadImageResponse) => void): grpc.ClientUnaryCall;
    public uploadVideo(callback: (error: grpc.ServiceError | null, response: v1_meta_api_pb.UploadVideoResponse) => void): grpc.ClientWritableStream<v1_meta_api_pb.UploadVideoChunk>;
    public uploadVideo(metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_meta_api_pb.UploadVideoResponse) => void): grpc.ClientWritableStream<v1_meta_api_pb.UploadVideoChunk>;
    public uploadVideo(options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_meta_api_pb.UploadVideoResponse) => void): grpc.ClientWritableStream<v1_meta_api_pb.UploadVideoChunk>;
    public uploadVideo(metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_meta_api_pb.UploadVideoResponse) => void): grpc.ClientWritableStream<v1_meta_api_pb.UploadVideoChunk>;
}
