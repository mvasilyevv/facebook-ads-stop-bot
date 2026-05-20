// package: fb_agent.creator.v1
// file: v1/creator.proto

/* tslint:disable */
/* eslint-disable */

import * as grpc from "grpc";
import * as v1_creator_pb from "../v1/creator_pb";

interface ICreatorServiceService extends grpc.ServiceDefinition<grpc.UntypedServiceImplementation> {
    runPlan: ICreatorServiceService_IRunPlan;
    startRecording: ICreatorServiceService_IStartRecording;
    stopRecording: ICreatorServiceService_IStopRecording;
    getRecorderStatus: ICreatorServiceService_IGetRecorderStatus;
}

interface ICreatorServiceService_IRunPlan extends grpc.MethodDefinition<v1_creator_pb.RunPlanRequest, v1_creator_pb.PlanEvent> {
    path: "/fb_agent.creator.v1.CreatorService/RunPlan";
    requestStream: false;
    responseStream: true;
    requestSerialize: grpc.serialize<v1_creator_pb.RunPlanRequest>;
    requestDeserialize: grpc.deserialize<v1_creator_pb.RunPlanRequest>;
    responseSerialize: grpc.serialize<v1_creator_pb.PlanEvent>;
    responseDeserialize: grpc.deserialize<v1_creator_pb.PlanEvent>;
}
interface ICreatorServiceService_IStartRecording extends grpc.MethodDefinition<v1_creator_pb.StartRecordingRequest, v1_creator_pb.StartRecordingResponse> {
    path: "/fb_agent.creator.v1.CreatorService/StartRecording";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_creator_pb.StartRecordingRequest>;
    requestDeserialize: grpc.deserialize<v1_creator_pb.StartRecordingRequest>;
    responseSerialize: grpc.serialize<v1_creator_pb.StartRecordingResponse>;
    responseDeserialize: grpc.deserialize<v1_creator_pb.StartRecordingResponse>;
}
interface ICreatorServiceService_IStopRecording extends grpc.MethodDefinition<v1_creator_pb.StopRecordingRequest, v1_creator_pb.StopRecordingResponse> {
    path: "/fb_agent.creator.v1.CreatorService/StopRecording";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_creator_pb.StopRecordingRequest>;
    requestDeserialize: grpc.deserialize<v1_creator_pb.StopRecordingRequest>;
    responseSerialize: grpc.serialize<v1_creator_pb.StopRecordingResponse>;
    responseDeserialize: grpc.deserialize<v1_creator_pb.StopRecordingResponse>;
}
interface ICreatorServiceService_IGetRecorderStatus extends grpc.MethodDefinition<v1_creator_pb.GetRecorderStatusRequest, v1_creator_pb.GetRecorderStatusResponse> {
    path: "/fb_agent.creator.v1.CreatorService/GetRecorderStatus";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_creator_pb.GetRecorderStatusRequest>;
    requestDeserialize: grpc.deserialize<v1_creator_pb.GetRecorderStatusRequest>;
    responseSerialize: grpc.serialize<v1_creator_pb.GetRecorderStatusResponse>;
    responseDeserialize: grpc.deserialize<v1_creator_pb.GetRecorderStatusResponse>;
}

export const CreatorServiceService: ICreatorServiceService;

export interface ICreatorServiceServer {
    runPlan: grpc.handleServerStreamingCall<v1_creator_pb.RunPlanRequest, v1_creator_pb.PlanEvent>;
    startRecording: grpc.handleUnaryCall<v1_creator_pb.StartRecordingRequest, v1_creator_pb.StartRecordingResponse>;
    stopRecording: grpc.handleUnaryCall<v1_creator_pb.StopRecordingRequest, v1_creator_pb.StopRecordingResponse>;
    getRecorderStatus: grpc.handleUnaryCall<v1_creator_pb.GetRecorderStatusRequest, v1_creator_pb.GetRecorderStatusResponse>;
}

export interface ICreatorServiceClient {
    runPlan(request: v1_creator_pb.RunPlanRequest, options?: Partial<grpc.CallOptions>): grpc.ClientReadableStream<v1_creator_pb.PlanEvent>;
    runPlan(request: v1_creator_pb.RunPlanRequest, metadata?: grpc.Metadata, options?: Partial<grpc.CallOptions>): grpc.ClientReadableStream<v1_creator_pb.PlanEvent>;
    startRecording(request: v1_creator_pb.StartRecordingRequest, callback: (error: grpc.ServiceError | null, response: v1_creator_pb.StartRecordingResponse) => void): grpc.ClientUnaryCall;
    startRecording(request: v1_creator_pb.StartRecordingRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_creator_pb.StartRecordingResponse) => void): grpc.ClientUnaryCall;
    startRecording(request: v1_creator_pb.StartRecordingRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_creator_pb.StartRecordingResponse) => void): grpc.ClientUnaryCall;
    stopRecording(request: v1_creator_pb.StopRecordingRequest, callback: (error: grpc.ServiceError | null, response: v1_creator_pb.StopRecordingResponse) => void): grpc.ClientUnaryCall;
    stopRecording(request: v1_creator_pb.StopRecordingRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_creator_pb.StopRecordingResponse) => void): grpc.ClientUnaryCall;
    stopRecording(request: v1_creator_pb.StopRecordingRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_creator_pb.StopRecordingResponse) => void): grpc.ClientUnaryCall;
    getRecorderStatus(request: v1_creator_pb.GetRecorderStatusRequest, callback: (error: grpc.ServiceError | null, response: v1_creator_pb.GetRecorderStatusResponse) => void): grpc.ClientUnaryCall;
    getRecorderStatus(request: v1_creator_pb.GetRecorderStatusRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_creator_pb.GetRecorderStatusResponse) => void): grpc.ClientUnaryCall;
    getRecorderStatus(request: v1_creator_pb.GetRecorderStatusRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_creator_pb.GetRecorderStatusResponse) => void): grpc.ClientUnaryCall;
}

export class CreatorServiceClient extends grpc.Client implements ICreatorServiceClient {
    constructor(address: string, credentials: grpc.ChannelCredentials, options?: object);
    public runPlan(request: v1_creator_pb.RunPlanRequest, options?: Partial<grpc.CallOptions>): grpc.ClientReadableStream<v1_creator_pb.PlanEvent>;
    public runPlan(request: v1_creator_pb.RunPlanRequest, metadata?: grpc.Metadata, options?: Partial<grpc.CallOptions>): grpc.ClientReadableStream<v1_creator_pb.PlanEvent>;
    public startRecording(request: v1_creator_pb.StartRecordingRequest, callback: (error: grpc.ServiceError | null, response: v1_creator_pb.StartRecordingResponse) => void): grpc.ClientUnaryCall;
    public startRecording(request: v1_creator_pb.StartRecordingRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_creator_pb.StartRecordingResponse) => void): grpc.ClientUnaryCall;
    public startRecording(request: v1_creator_pb.StartRecordingRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_creator_pb.StartRecordingResponse) => void): grpc.ClientUnaryCall;
    public stopRecording(request: v1_creator_pb.StopRecordingRequest, callback: (error: grpc.ServiceError | null, response: v1_creator_pb.StopRecordingResponse) => void): grpc.ClientUnaryCall;
    public stopRecording(request: v1_creator_pb.StopRecordingRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_creator_pb.StopRecordingResponse) => void): grpc.ClientUnaryCall;
    public stopRecording(request: v1_creator_pb.StopRecordingRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_creator_pb.StopRecordingResponse) => void): grpc.ClientUnaryCall;
    public getRecorderStatus(request: v1_creator_pb.GetRecorderStatusRequest, callback: (error: grpc.ServiceError | null, response: v1_creator_pb.GetRecorderStatusResponse) => void): grpc.ClientUnaryCall;
    public getRecorderStatus(request: v1_creator_pb.GetRecorderStatusRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_creator_pb.GetRecorderStatusResponse) => void): grpc.ClientUnaryCall;
    public getRecorderStatus(request: v1_creator_pb.GetRecorderStatusRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_creator_pb.GetRecorderStatusResponse) => void): grpc.ClientUnaryCall;
}
