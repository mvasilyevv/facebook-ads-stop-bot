// GENERATED CODE -- DO NOT EDIT!

'use strict';
var grpc = require('grpc');
var v1_meta_api_pb = require('../v1/meta_api_pb.js');

function serialize_fb_agent_meta_api_v1_CheckMetaApiHealthRequest(arg) {
  if (!(arg instanceof v1_meta_api_pb.CheckMetaApiHealthRequest)) {
    throw new Error('Expected argument of type fb_agent.meta_api.v1.CheckMetaApiHealthRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_meta_api_v1_CheckMetaApiHealthRequest(buffer_arg) {
  return v1_meta_api_pb.CheckMetaApiHealthRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_meta_api_v1_CheckMetaApiHealthResponse(arg) {
  if (!(arg instanceof v1_meta_api_pb.CheckMetaApiHealthResponse)) {
    throw new Error('Expected argument of type fb_agent.meta_api.v1.CheckMetaApiHealthResponse');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_meta_api_v1_CheckMetaApiHealthResponse(buffer_arg) {
  return v1_meta_api_pb.CheckMetaApiHealthResponse.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_meta_api_v1_ExecuteGraphCallRequest(arg) {
  if (!(arg instanceof v1_meta_api_pb.ExecuteGraphCallRequest)) {
    throw new Error('Expected argument of type fb_agent.meta_api.v1.ExecuteGraphCallRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_meta_api_v1_ExecuteGraphCallRequest(buffer_arg) {
  return v1_meta_api_pb.ExecuteGraphCallRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_meta_api_v1_ExecuteGraphCallResponse(arg) {
  if (!(arg instanceof v1_meta_api_pb.ExecuteGraphCallResponse)) {
    throw new Error('Expected argument of type fb_agent.meta_api.v1.ExecuteGraphCallResponse');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_meta_api_v1_ExecuteGraphCallResponse(buffer_arg) {
  return v1_meta_api_pb.ExecuteGraphCallResponse.deserializeBinary(new Uint8Array(buffer_arg));
}


// MetaApiService — мост к Marketing API через активную Vision-сессию.
//
// Архитектурная заметка: вызовы Marketing API НЕ исполняются standalone из Python,
// потому что EAAB-токены Ads Manager привязаны к browser session (machine_id, datr cookie,
// fingerprint). Anti-fraud Meta отвергает любые server-to-server вызовы с этими токенами.
// Поэтому весь Marketing API проходит ЧЕРЕЗ Playwright-страницу: browser-agent делает
// page.evaluate(fetch(...)) — запрос идёт из того же session-context, что и DOM-парсинг.
var MetaApiServiceService = exports.MetaApiServiceService = {
  // Универсальный wrapper. Исполняет произвольный Graph API endpoint
// изнутри активной браузерной сессии Ads Manager.
//
// Path формат: "/me", "/act_XXX/insights", "/{ad_id}", "/{campaign_id}/copies" и т.д.
// Параметры в query_params, тело в body_json.
// access_token подставляется автоматически из page source.
executeGraphCall: {
    path: '/fb_agent.meta_api.v1.MetaApiService/ExecuteGraphCall',
    requestStream: false,
    responseStream: false,
    requestType: v1_meta_api_pb.ExecuteGraphCallRequest,
    responseType: v1_meta_api_pb.ExecuteGraphCallResponse,
    requestSerialize: serialize_fb_agent_meta_api_v1_ExecuteGraphCallRequest,
    requestDeserialize: deserialize_fb_agent_meta_api_v1_ExecuteGraphCallRequest,
    responseSerialize: serialize_fb_agent_meta_api_v1_ExecuteGraphCallResponse,
    responseDeserialize: deserialize_fb_agent_meta_api_v1_ExecuteGraphCallResponse,
  },
  // Проверить готовность Marketing API канала: жива ли Vision-сессия,
// на правильной ли странице (Ads Manager), извлекается ли токен.
// Лёгкий запрос — используется в health_watchdog для мониторинга.
checkMetaApiHealth: {
    path: '/fb_agent.meta_api.v1.MetaApiService/CheckMetaApiHealth',
    requestStream: false,
    responseStream: false,
    requestType: v1_meta_api_pb.CheckMetaApiHealthRequest,
    responseType: v1_meta_api_pb.CheckMetaApiHealthResponse,
    requestSerialize: serialize_fb_agent_meta_api_v1_CheckMetaApiHealthRequest,
    requestDeserialize: deserialize_fb_agent_meta_api_v1_CheckMetaApiHealthRequest,
    responseSerialize: serialize_fb_agent_meta_api_v1_CheckMetaApiHealthResponse,
    responseDeserialize: deserialize_fb_agent_meta_api_v1_CheckMetaApiHealthResponse,
  },
};

exports.MetaApiServiceClient = grpc.makeGenericClientConstructor(MetaApiServiceService, 'MetaApiService');
