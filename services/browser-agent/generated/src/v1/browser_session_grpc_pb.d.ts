// package: fb_agent.browser_session.v1
// file: v1/browser_session.proto

/* tslint:disable */
/* eslint-disable */

import * as grpc from "grpc";
import * as v1_browser_session_pb from "../v1/browser_session_pb";

interface IBrowserSessionServiceService extends grpc.ServiceDefinition<grpc.UntypedServiceImplementation> {
    startBrowser: IBrowserSessionServiceService_IStartBrowser;
    disconnectBrowser: IBrowserSessionServiceService_IDisconnectBrowser;
    stopBrowser: IBrowserSessionServiceService_IStopBrowser;
    reconnectBrowser: IBrowserSessionServiceService_IReconnectBrowser;
    getSessionInfo: IBrowserSessionServiceService_IGetSessionInfo;
    navigate: IBrowserSessionServiceService_INavigate;
    streamSessionStatus: IBrowserSessionServiceService_IStreamSessionStatus;
}

interface IBrowserSessionServiceService_IStartBrowser extends grpc.MethodDefinition<v1_browser_session_pb.StartBrowserRequest, v1_browser_session_pb.StartBrowserResponse> {
    path: "/fb_agent.browser_session.v1.BrowserSessionService/StartBrowser";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_browser_session_pb.StartBrowserRequest>;
    requestDeserialize: grpc.deserialize<v1_browser_session_pb.StartBrowserRequest>;
    responseSerialize: grpc.serialize<v1_browser_session_pb.StartBrowserResponse>;
    responseDeserialize: grpc.deserialize<v1_browser_session_pb.StartBrowserResponse>;
}
interface IBrowserSessionServiceService_IDisconnectBrowser extends grpc.MethodDefinition<v1_browser_session_pb.DisconnectBrowserRequest, v1_browser_session_pb.DisconnectBrowserResponse> {
    path: "/fb_agent.browser_session.v1.BrowserSessionService/DisconnectBrowser";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_browser_session_pb.DisconnectBrowserRequest>;
    requestDeserialize: grpc.deserialize<v1_browser_session_pb.DisconnectBrowserRequest>;
    responseSerialize: grpc.serialize<v1_browser_session_pb.DisconnectBrowserResponse>;
    responseDeserialize: grpc.deserialize<v1_browser_session_pb.DisconnectBrowserResponse>;
}
interface IBrowserSessionServiceService_IStopBrowser extends grpc.MethodDefinition<v1_browser_session_pb.StopBrowserRequest, v1_browser_session_pb.StopBrowserResponse> {
    path: "/fb_agent.browser_session.v1.BrowserSessionService/StopBrowser";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_browser_session_pb.StopBrowserRequest>;
    requestDeserialize: grpc.deserialize<v1_browser_session_pb.StopBrowserRequest>;
    responseSerialize: grpc.serialize<v1_browser_session_pb.StopBrowserResponse>;
    responseDeserialize: grpc.deserialize<v1_browser_session_pb.StopBrowserResponse>;
}
interface IBrowserSessionServiceService_IReconnectBrowser extends grpc.MethodDefinition<v1_browser_session_pb.ReconnectBrowserRequest, v1_browser_session_pb.StartBrowserResponse> {
    path: "/fb_agent.browser_session.v1.BrowserSessionService/ReconnectBrowser";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_browser_session_pb.ReconnectBrowserRequest>;
    requestDeserialize: grpc.deserialize<v1_browser_session_pb.ReconnectBrowserRequest>;
    responseSerialize: grpc.serialize<v1_browser_session_pb.StartBrowserResponse>;
    responseDeserialize: grpc.deserialize<v1_browser_session_pb.StartBrowserResponse>;
}
interface IBrowserSessionServiceService_IGetSessionInfo extends grpc.MethodDefinition<v1_browser_session_pb.GetSessionInfoRequest, v1_browser_session_pb.GetSessionInfoResponse> {
    path: "/fb_agent.browser_session.v1.BrowserSessionService/GetSessionInfo";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_browser_session_pb.GetSessionInfoRequest>;
    requestDeserialize: grpc.deserialize<v1_browser_session_pb.GetSessionInfoRequest>;
    responseSerialize: grpc.serialize<v1_browser_session_pb.GetSessionInfoResponse>;
    responseDeserialize: grpc.deserialize<v1_browser_session_pb.GetSessionInfoResponse>;
}
interface IBrowserSessionServiceService_INavigate extends grpc.MethodDefinition<v1_browser_session_pb.NavigateRequest, v1_browser_session_pb.NavigateResponse> {
    path: "/fb_agent.browser_session.v1.BrowserSessionService/Navigate";
    requestStream: false;
    responseStream: false;
    requestSerialize: grpc.serialize<v1_browser_session_pb.NavigateRequest>;
    requestDeserialize: grpc.deserialize<v1_browser_session_pb.NavigateRequest>;
    responseSerialize: grpc.serialize<v1_browser_session_pb.NavigateResponse>;
    responseDeserialize: grpc.deserialize<v1_browser_session_pb.NavigateResponse>;
}
interface IBrowserSessionServiceService_IStreamSessionStatus extends grpc.MethodDefinition<v1_browser_session_pb.StreamSessionStatusRequest, v1_browser_session_pb.SessionStatusEvent> {
    path: "/fb_agent.browser_session.v1.BrowserSessionService/StreamSessionStatus";
    requestStream: false;
    responseStream: true;
    requestSerialize: grpc.serialize<v1_browser_session_pb.StreamSessionStatusRequest>;
    requestDeserialize: grpc.deserialize<v1_browser_session_pb.StreamSessionStatusRequest>;
    responseSerialize: grpc.serialize<v1_browser_session_pb.SessionStatusEvent>;
    responseDeserialize: grpc.deserialize<v1_browser_session_pb.SessionStatusEvent>;
}

export const BrowserSessionServiceService: IBrowserSessionServiceService;

export interface IBrowserSessionServiceServer {
    startBrowser: grpc.handleUnaryCall<v1_browser_session_pb.StartBrowserRequest, v1_browser_session_pb.StartBrowserResponse>;
    disconnectBrowser: grpc.handleUnaryCall<v1_browser_session_pb.DisconnectBrowserRequest, v1_browser_session_pb.DisconnectBrowserResponse>;
    stopBrowser: grpc.handleUnaryCall<v1_browser_session_pb.StopBrowserRequest, v1_browser_session_pb.StopBrowserResponse>;
    reconnectBrowser: grpc.handleUnaryCall<v1_browser_session_pb.ReconnectBrowserRequest, v1_browser_session_pb.StartBrowserResponse>;
    getSessionInfo: grpc.handleUnaryCall<v1_browser_session_pb.GetSessionInfoRequest, v1_browser_session_pb.GetSessionInfoResponse>;
    navigate: grpc.handleUnaryCall<v1_browser_session_pb.NavigateRequest, v1_browser_session_pb.NavigateResponse>;
    streamSessionStatus: grpc.handleServerStreamingCall<v1_browser_session_pb.StreamSessionStatusRequest, v1_browser_session_pb.SessionStatusEvent>;
}

export interface IBrowserSessionServiceClient {
    startBrowser(request: v1_browser_session_pb.StartBrowserRequest, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.StartBrowserResponse) => void): grpc.ClientUnaryCall;
    startBrowser(request: v1_browser_session_pb.StartBrowserRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.StartBrowserResponse) => void): grpc.ClientUnaryCall;
    startBrowser(request: v1_browser_session_pb.StartBrowserRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.StartBrowserResponse) => void): grpc.ClientUnaryCall;
    disconnectBrowser(request: v1_browser_session_pb.DisconnectBrowserRequest, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.DisconnectBrowserResponse) => void): grpc.ClientUnaryCall;
    disconnectBrowser(request: v1_browser_session_pb.DisconnectBrowserRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.DisconnectBrowserResponse) => void): grpc.ClientUnaryCall;
    disconnectBrowser(request: v1_browser_session_pb.DisconnectBrowserRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.DisconnectBrowserResponse) => void): grpc.ClientUnaryCall;
    stopBrowser(request: v1_browser_session_pb.StopBrowserRequest, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.StopBrowserResponse) => void): grpc.ClientUnaryCall;
    stopBrowser(request: v1_browser_session_pb.StopBrowserRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.StopBrowserResponse) => void): grpc.ClientUnaryCall;
    stopBrowser(request: v1_browser_session_pb.StopBrowserRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.StopBrowserResponse) => void): grpc.ClientUnaryCall;
    reconnectBrowser(request: v1_browser_session_pb.ReconnectBrowserRequest, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.StartBrowserResponse) => void): grpc.ClientUnaryCall;
    reconnectBrowser(request: v1_browser_session_pb.ReconnectBrowserRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.StartBrowserResponse) => void): grpc.ClientUnaryCall;
    reconnectBrowser(request: v1_browser_session_pb.ReconnectBrowserRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.StartBrowserResponse) => void): grpc.ClientUnaryCall;
    getSessionInfo(request: v1_browser_session_pb.GetSessionInfoRequest, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.GetSessionInfoResponse) => void): grpc.ClientUnaryCall;
    getSessionInfo(request: v1_browser_session_pb.GetSessionInfoRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.GetSessionInfoResponse) => void): grpc.ClientUnaryCall;
    getSessionInfo(request: v1_browser_session_pb.GetSessionInfoRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.GetSessionInfoResponse) => void): grpc.ClientUnaryCall;
    navigate(request: v1_browser_session_pb.NavigateRequest, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.NavigateResponse) => void): grpc.ClientUnaryCall;
    navigate(request: v1_browser_session_pb.NavigateRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.NavigateResponse) => void): grpc.ClientUnaryCall;
    navigate(request: v1_browser_session_pb.NavigateRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.NavigateResponse) => void): grpc.ClientUnaryCall;
    streamSessionStatus(request: v1_browser_session_pb.StreamSessionStatusRequest, options?: Partial<grpc.CallOptions>): grpc.ClientReadableStream<v1_browser_session_pb.SessionStatusEvent>;
    streamSessionStatus(request: v1_browser_session_pb.StreamSessionStatusRequest, metadata?: grpc.Metadata, options?: Partial<grpc.CallOptions>): grpc.ClientReadableStream<v1_browser_session_pb.SessionStatusEvent>;
}

export class BrowserSessionServiceClient extends grpc.Client implements IBrowserSessionServiceClient {
    constructor(address: string, credentials: grpc.ChannelCredentials, options?: object);
    public startBrowser(request: v1_browser_session_pb.StartBrowserRequest, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.StartBrowserResponse) => void): grpc.ClientUnaryCall;
    public startBrowser(request: v1_browser_session_pb.StartBrowserRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.StartBrowserResponse) => void): grpc.ClientUnaryCall;
    public startBrowser(request: v1_browser_session_pb.StartBrowserRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.StartBrowserResponse) => void): grpc.ClientUnaryCall;
    public disconnectBrowser(request: v1_browser_session_pb.DisconnectBrowserRequest, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.DisconnectBrowserResponse) => void): grpc.ClientUnaryCall;
    public disconnectBrowser(request: v1_browser_session_pb.DisconnectBrowserRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.DisconnectBrowserResponse) => void): grpc.ClientUnaryCall;
    public disconnectBrowser(request: v1_browser_session_pb.DisconnectBrowserRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.DisconnectBrowserResponse) => void): grpc.ClientUnaryCall;
    public stopBrowser(request: v1_browser_session_pb.StopBrowserRequest, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.StopBrowserResponse) => void): grpc.ClientUnaryCall;
    public stopBrowser(request: v1_browser_session_pb.StopBrowserRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.StopBrowserResponse) => void): grpc.ClientUnaryCall;
    public stopBrowser(request: v1_browser_session_pb.StopBrowserRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.StopBrowserResponse) => void): grpc.ClientUnaryCall;
    public reconnectBrowser(request: v1_browser_session_pb.ReconnectBrowserRequest, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.StartBrowserResponse) => void): grpc.ClientUnaryCall;
    public reconnectBrowser(request: v1_browser_session_pb.ReconnectBrowserRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.StartBrowserResponse) => void): grpc.ClientUnaryCall;
    public reconnectBrowser(request: v1_browser_session_pb.ReconnectBrowserRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.StartBrowserResponse) => void): grpc.ClientUnaryCall;
    public getSessionInfo(request: v1_browser_session_pb.GetSessionInfoRequest, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.GetSessionInfoResponse) => void): grpc.ClientUnaryCall;
    public getSessionInfo(request: v1_browser_session_pb.GetSessionInfoRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.GetSessionInfoResponse) => void): grpc.ClientUnaryCall;
    public getSessionInfo(request: v1_browser_session_pb.GetSessionInfoRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.GetSessionInfoResponse) => void): grpc.ClientUnaryCall;
    public navigate(request: v1_browser_session_pb.NavigateRequest, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.NavigateResponse) => void): grpc.ClientUnaryCall;
    public navigate(request: v1_browser_session_pb.NavigateRequest, metadata: grpc.Metadata, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.NavigateResponse) => void): grpc.ClientUnaryCall;
    public navigate(request: v1_browser_session_pb.NavigateRequest, metadata: grpc.Metadata, options: Partial<grpc.CallOptions>, callback: (error: grpc.ServiceError | null, response: v1_browser_session_pb.NavigateResponse) => void): grpc.ClientUnaryCall;
    public streamSessionStatus(request: v1_browser_session_pb.StreamSessionStatusRequest, options?: Partial<grpc.CallOptions>): grpc.ClientReadableStream<v1_browser_session_pb.SessionStatusEvent>;
    public streamSessionStatus(request: v1_browser_session_pb.StreamSessionStatusRequest, metadata?: grpc.Metadata, options?: Partial<grpc.CallOptions>): grpc.ClientReadableStream<v1_browser_session_pb.SessionStatusEvent>;
}
