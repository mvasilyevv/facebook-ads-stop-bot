// GENERATED CODE -- DO NOT EDIT!

'use strict';
var grpc = require('grpc');
var v1_creator_pb = require('../v1/creator_pb.js');

function serialize_fb_agent_creator_v1_GetRecorderStatusRequest(arg) {
  if (!(arg instanceof v1_creator_pb.GetRecorderStatusRequest)) {
    throw new Error('Expected argument of type fb_agent.creator.v1.GetRecorderStatusRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_creator_v1_GetRecorderStatusRequest(buffer_arg) {
  return v1_creator_pb.GetRecorderStatusRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_creator_v1_GetRecorderStatusResponse(arg) {
  if (!(arg instanceof v1_creator_pb.GetRecorderStatusResponse)) {
    throw new Error('Expected argument of type fb_agent.creator.v1.GetRecorderStatusResponse');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_creator_v1_GetRecorderStatusResponse(buffer_arg) {
  return v1_creator_pb.GetRecorderStatusResponse.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_creator_v1_PlanEvent(arg) {
  if (!(arg instanceof v1_creator_pb.PlanEvent)) {
    throw new Error('Expected argument of type fb_agent.creator.v1.PlanEvent');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_creator_v1_PlanEvent(buffer_arg) {
  return v1_creator_pb.PlanEvent.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_creator_v1_RunPlanRequest(arg) {
  if (!(arg instanceof v1_creator_pb.RunPlanRequest)) {
    throw new Error('Expected argument of type fb_agent.creator.v1.RunPlanRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_creator_v1_RunPlanRequest(buffer_arg) {
  return v1_creator_pb.RunPlanRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_creator_v1_StartRecordingRequest(arg) {
  if (!(arg instanceof v1_creator_pb.StartRecordingRequest)) {
    throw new Error('Expected argument of type fb_agent.creator.v1.StartRecordingRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_creator_v1_StartRecordingRequest(buffer_arg) {
  return v1_creator_pb.StartRecordingRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_creator_v1_StartRecordingResponse(arg) {
  if (!(arg instanceof v1_creator_pb.StartRecordingResponse)) {
    throw new Error('Expected argument of type fb_agent.creator.v1.StartRecordingResponse');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_creator_v1_StartRecordingResponse(buffer_arg) {
  return v1_creator_pb.StartRecordingResponse.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_creator_v1_StopRecordingRequest(arg) {
  if (!(arg instanceof v1_creator_pb.StopRecordingRequest)) {
    throw new Error('Expected argument of type fb_agent.creator.v1.StopRecordingRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_creator_v1_StopRecordingRequest(buffer_arg) {
  return v1_creator_pb.StopRecordingRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_creator_v1_StopRecordingResponse(arg) {
  if (!(arg instanceof v1_creator_pb.StopRecordingResponse)) {
    throw new Error('Expected argument of type fb_agent.creator.v1.StopRecordingResponse');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_creator_v1_StopRecordingResponse(buffer_arg) {
  return v1_creator_pb.StopRecordingResponse.deserializeBinary(new Uint8Array(buffer_arg));
}


// Сервис создания FB-кампаний: выполнение планов и запись новых через recorder.
// Работает поверх browser-agent-сессии (см. browser_session.proto).
var CreatorServiceService = exports.CreatorServiceService = {
  // Выполнить план: стримит события каждого шага (started/finished/failed/skipped)
// плюс финальный complete с итоговым статусом.
runPlan: {
    path: '/fb_agent.creator.v1.CreatorService/RunPlan',
    requestStream: false,
    responseStream: true,
    requestType: v1_creator_pb.RunPlanRequest,
    responseType: v1_creator_pb.PlanEvent,
    requestSerialize: serialize_fb_agent_creator_v1_RunPlanRequest,
    requestDeserialize: deserialize_fb_agent_creator_v1_RunPlanRequest,
    responseSerialize: serialize_fb_agent_creator_v1_PlanEvent,
    responseDeserialize: deserialize_fb_agent_creator_v1_PlanEvent,
  },
  // Начать запись действий пользователя в Plan-формате. Возвращается сразу,
// recorder работает в браузере до StopRecording.
startRecording: {
    path: '/fb_agent.creator.v1.CreatorService/StartRecording',
    requestStream: false,
    responseStream: false,
    requestType: v1_creator_pb.StartRecordingRequest,
    responseType: v1_creator_pb.StartRecordingResponse,
    requestSerialize: serialize_fb_agent_creator_v1_StartRecordingRequest,
    requestDeserialize: deserialize_fb_agent_creator_v1_StartRecordingRequest,
    responseSerialize: serialize_fb_agent_creator_v1_StartRecordingResponse,
    responseDeserialize: deserialize_fb_agent_creator_v1_StartRecordingResponse,
  },
  // Остановить запись и вернуть собранный Plan (JSON-строка).
stopRecording: {
    path: '/fb_agent.creator.v1.CreatorService/StopRecording',
    requestStream: false,
    responseStream: false,
    requestType: v1_creator_pb.StopRecordingRequest,
    responseType: v1_creator_pb.StopRecordingResponse,
    requestSerialize: serialize_fb_agent_creator_v1_StopRecordingRequest,
    requestDeserialize: deserialize_fb_agent_creator_v1_StopRecordingRequest,
    responseSerialize: serialize_fb_agent_creator_v1_StopRecordingResponse,
    responseDeserialize: deserialize_fb_agent_creator_v1_StopRecordingResponse,
  },
  // Получить текущее состояние recorder (для UI/polling). Не останавливает.
getRecorderStatus: {
    path: '/fb_agent.creator.v1.CreatorService/GetRecorderStatus',
    requestStream: false,
    responseStream: false,
    requestType: v1_creator_pb.GetRecorderStatusRequest,
    responseType: v1_creator_pb.GetRecorderStatusResponse,
    requestSerialize: serialize_fb_agent_creator_v1_GetRecorderStatusRequest,
    requestDeserialize: deserialize_fb_agent_creator_v1_GetRecorderStatusRequest,
    responseSerialize: serialize_fb_agent_creator_v1_GetRecorderStatusResponse,
    responseDeserialize: deserialize_fb_agent_creator_v1_GetRecorderStatusResponse,
  },
};

exports.CreatorServiceClient = grpc.makeGenericClientConstructor(CreatorServiceService, 'CreatorService');
