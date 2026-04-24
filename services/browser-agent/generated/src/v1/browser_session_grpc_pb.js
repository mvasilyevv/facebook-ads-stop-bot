// GENERATED CODE -- DO NOT EDIT!

'use strict';
var grpc = require('grpc');
var v1_browser_session_pb = require('../v1/browser_session_pb.js');

function serialize_fb_agent_browser_session_v1_DisconnectBrowserRequest(arg) {
  if (!(arg instanceof v1_browser_session_pb.DisconnectBrowserRequest)) {
    throw new Error('Expected argument of type fb_agent.browser_session.v1.DisconnectBrowserRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_browser_session_v1_DisconnectBrowserRequest(buffer_arg) {
  return v1_browser_session_pb.DisconnectBrowserRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_browser_session_v1_DisconnectBrowserResponse(arg) {
  if (!(arg instanceof v1_browser_session_pb.DisconnectBrowserResponse)) {
    throw new Error('Expected argument of type fb_agent.browser_session.v1.DisconnectBrowserResponse');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_browser_session_v1_DisconnectBrowserResponse(buffer_arg) {
  return v1_browser_session_pb.DisconnectBrowserResponse.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_browser_session_v1_GetSessionInfoRequest(arg) {
  if (!(arg instanceof v1_browser_session_pb.GetSessionInfoRequest)) {
    throw new Error('Expected argument of type fb_agent.browser_session.v1.GetSessionInfoRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_browser_session_v1_GetSessionInfoRequest(buffer_arg) {
  return v1_browser_session_pb.GetSessionInfoRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_browser_session_v1_GetSessionInfoResponse(arg) {
  if (!(arg instanceof v1_browser_session_pb.GetSessionInfoResponse)) {
    throw new Error('Expected argument of type fb_agent.browser_session.v1.GetSessionInfoResponse');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_browser_session_v1_GetSessionInfoResponse(buffer_arg) {
  return v1_browser_session_pb.GetSessionInfoResponse.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_browser_session_v1_NavigateRequest(arg) {
  if (!(arg instanceof v1_browser_session_pb.NavigateRequest)) {
    throw new Error('Expected argument of type fb_agent.browser_session.v1.NavigateRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_browser_session_v1_NavigateRequest(buffer_arg) {
  return v1_browser_session_pb.NavigateRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_browser_session_v1_NavigateResponse(arg) {
  if (!(arg instanceof v1_browser_session_pb.NavigateResponse)) {
    throw new Error('Expected argument of type fb_agent.browser_session.v1.NavigateResponse');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_browser_session_v1_NavigateResponse(buffer_arg) {
  return v1_browser_session_pb.NavigateResponse.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_browser_session_v1_ReconnectBrowserRequest(arg) {
  if (!(arg instanceof v1_browser_session_pb.ReconnectBrowserRequest)) {
    throw new Error('Expected argument of type fb_agent.browser_session.v1.ReconnectBrowserRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_browser_session_v1_ReconnectBrowserRequest(buffer_arg) {
  return v1_browser_session_pb.ReconnectBrowserRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_browser_session_v1_SessionStatusEvent(arg) {
  if (!(arg instanceof v1_browser_session_pb.SessionStatusEvent)) {
    throw new Error('Expected argument of type fb_agent.browser_session.v1.SessionStatusEvent');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_browser_session_v1_SessionStatusEvent(buffer_arg) {
  return v1_browser_session_pb.SessionStatusEvent.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_browser_session_v1_StartBrowserRequest(arg) {
  if (!(arg instanceof v1_browser_session_pb.StartBrowserRequest)) {
    throw new Error('Expected argument of type fb_agent.browser_session.v1.StartBrowserRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_browser_session_v1_StartBrowserRequest(buffer_arg) {
  return v1_browser_session_pb.StartBrowserRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_browser_session_v1_StartBrowserResponse(arg) {
  if (!(arg instanceof v1_browser_session_pb.StartBrowserResponse)) {
    throw new Error('Expected argument of type fb_agent.browser_session.v1.StartBrowserResponse');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_browser_session_v1_StartBrowserResponse(buffer_arg) {
  return v1_browser_session_pb.StartBrowserResponse.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_browser_session_v1_StopBrowserRequest(arg) {
  if (!(arg instanceof v1_browser_session_pb.StopBrowserRequest)) {
    throw new Error('Expected argument of type fb_agent.browser_session.v1.StopBrowserRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_browser_session_v1_StopBrowserRequest(buffer_arg) {
  return v1_browser_session_pb.StopBrowserRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_browser_session_v1_StopBrowserResponse(arg) {
  if (!(arg instanceof v1_browser_session_pb.StopBrowserResponse)) {
    throw new Error('Expected argument of type fb_agent.browser_session.v1.StopBrowserResponse');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_browser_session_v1_StopBrowserResponse(buffer_arg) {
  return v1_browser_session_pb.StopBrowserResponse.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_browser_session_v1_StreamSessionStatusRequest(arg) {
  if (!(arg instanceof v1_browser_session_pb.StreamSessionStatusRequest)) {
    throw new Error('Expected argument of type fb_agent.browser_session.v1.StreamSessionStatusRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_browser_session_v1_StreamSessionStatusRequest(buffer_arg) {
  return v1_browser_session_pb.StreamSessionStatusRequest.deserializeBinary(new Uint8Array(buffer_arg));
}


var BrowserSessionServiceService = exports.BrowserSessionServiceService = {
  // Запустить Vision профиль и подключиться через CDP.
// Возвращает session_id для всех последующих вызовов.
startBrowser: {
    path: '/fb_agent.browser_session.v1.BrowserSessionService/StartBrowser',
    requestStream: false,
    responseStream: false,
    requestType: v1_browser_session_pb.StartBrowserRequest,
    responseType: v1_browser_session_pb.StartBrowserResponse,
    requestSerialize: serialize_fb_agent_browser_session_v1_StartBrowserRequest,
    requestDeserialize: deserialize_fb_agent_browser_session_v1_StartBrowserRequest,
    responseSerialize: serialize_fb_agent_browser_session_v1_StartBrowserResponse,
    responseDeserialize: deserialize_fb_agent_browser_session_v1_StartBrowserResponse,
  },
  // Отключиться от браузера (закрыть CDP, не останавливая Vision профиль).
disconnectBrowser: {
    path: '/fb_agent.browser_session.v1.BrowserSessionService/DisconnectBrowser',
    requestStream: false,
    responseStream: false,
    requestType: v1_browser_session_pb.DisconnectBrowserRequest,
    responseType: v1_browser_session_pb.DisconnectBrowserResponse,
    requestSerialize: serialize_fb_agent_browser_session_v1_DisconnectBrowserRequest,
    requestDeserialize: deserialize_fb_agent_browser_session_v1_DisconnectBrowserRequest,
    responseSerialize: serialize_fb_agent_browser_session_v1_DisconnectBrowserResponse,
    responseDeserialize: deserialize_fb_agent_browser_session_v1_DisconnectBrowserResponse,
  },
  // Полностью остановить Vision профиль (disconnect + stop profile).
stopBrowser: {
    path: '/fb_agent.browser_session.v1.BrowserSessionService/StopBrowser',
    requestStream: false,
    responseStream: false,
    requestType: v1_browser_session_pb.StopBrowserRequest,
    responseType: v1_browser_session_pb.StopBrowserResponse,
    requestSerialize: serialize_fb_agent_browser_session_v1_StopBrowserRequest,
    requestDeserialize: deserialize_fb_agent_browser_session_v1_StopBrowserRequest,
    responseSerialize: serialize_fb_agent_browser_session_v1_StopBrowserResponse,
    responseDeserialize: deserialize_fb_agent_browser_session_v1_StopBrowserResponse,
  },
  // Переподключиться после разрыва или ошибки.
reconnectBrowser: {
    path: '/fb_agent.browser_session.v1.BrowserSessionService/ReconnectBrowser',
    requestStream: false,
    responseStream: false,
    requestType: v1_browser_session_pb.ReconnectBrowserRequest,
    responseType: v1_browser_session_pb.StartBrowserResponse,
    requestSerialize: serialize_fb_agent_browser_session_v1_ReconnectBrowserRequest,
    requestDeserialize: deserialize_fb_agent_browser_session_v1_ReconnectBrowserRequest,
    responseSerialize: serialize_fb_agent_browser_session_v1_StartBrowserResponse,
    responseDeserialize: deserialize_fb_agent_browser_session_v1_StartBrowserResponse,
  },
  // Получить информацию о сессии (URL, контексты, страницы).
getSessionInfo: {
    path: '/fb_agent.browser_session.v1.BrowserSessionService/GetSessionInfo',
    requestStream: false,
    responseStream: false,
    requestType: v1_browser_session_pb.GetSessionInfoRequest,
    responseType: v1_browser_session_pb.GetSessionInfoResponse,
    requestSerialize: serialize_fb_agent_browser_session_v1_GetSessionInfoRequest,
    requestDeserialize: deserialize_fb_agent_browser_session_v1_GetSessionInfoRequest,
    responseSerialize: serialize_fb_agent_browser_session_v1_GetSessionInfoResponse,
    responseDeserialize: deserialize_fb_agent_browser_session_v1_GetSessionInfoResponse,
  },
  // Перейти на URL.
navigate: {
    path: '/fb_agent.browser_session.v1.BrowserSessionService/Navigate',
    requestStream: false,
    responseStream: false,
    requestType: v1_browser_session_pb.NavigateRequest,
    responseType: v1_browser_session_pb.NavigateResponse,
    requestSerialize: serialize_fb_agent_browser_session_v1_NavigateRequest,
    requestDeserialize: deserialize_fb_agent_browser_session_v1_NavigateRequest,
    responseSerialize: serialize_fb_agent_browser_session_v1_NavigateResponse,
    responseDeserialize: deserialize_fb_agent_browser_session_v1_NavigateResponse,
  },
  // Стриминг статусов сессии (heartbeat, ошибки, смена страницы).
streamSessionStatus: {
    path: '/fb_agent.browser_session.v1.BrowserSessionService/StreamSessionStatus',
    requestStream: false,
    responseStream: true,
    requestType: v1_browser_session_pb.StreamSessionStatusRequest,
    responseType: v1_browser_session_pb.SessionStatusEvent,
    requestSerialize: serialize_fb_agent_browser_session_v1_StreamSessionStatusRequest,
    requestDeserialize: deserialize_fb_agent_browser_session_v1_StreamSessionStatusRequest,
    responseSerialize: serialize_fb_agent_browser_session_v1_SessionStatusEvent,
    responseDeserialize: deserialize_fb_agent_browser_session_v1_SessionStatusEvent,
  },
};

exports.BrowserSessionServiceClient = grpc.makeGenericClientConstructor(BrowserSessionServiceService, 'BrowserSessionService');
