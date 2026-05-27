// GENERATED CODE -- DO NOT EDIT!

'use strict';
var grpc = require('grpc');
var v1_ad_library_pb = require('../v1/ad_library_pb.js');

function serialize_fb_agent_ad_library_v1_CheckAdLibraryHealthRequest(arg) {
  if (!(arg instanceof v1_ad_library_pb.CheckAdLibraryHealthRequest)) {
    throw new Error('Expected argument of type fb_agent.ad_library.v1.CheckAdLibraryHealthRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_ad_library_v1_CheckAdLibraryHealthRequest(buffer_arg) {
  return v1_ad_library_pb.CheckAdLibraryHealthRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_ad_library_v1_CheckAdLibraryHealthResponse(arg) {
  if (!(arg instanceof v1_ad_library_pb.CheckAdLibraryHealthResponse)) {
    throw new Error('Expected argument of type fb_agent.ad_library.v1.CheckAdLibraryHealthResponse');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_ad_library_v1_CheckAdLibraryHealthResponse(buffer_arg) {
  return v1_ad_library_pb.CheckAdLibraryHealthResponse.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_ad_library_v1_SearchAdsBatchRequest(arg) {
  if (!(arg instanceof v1_ad_library_pb.SearchAdsBatchRequest)) {
    throw new Error('Expected argument of type fb_agent.ad_library.v1.SearchAdsBatchRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_ad_library_v1_SearchAdsBatchRequest(buffer_arg) {
  return v1_ad_library_pb.SearchAdsBatchRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_ad_library_v1_SearchAdsBatchResponse(arg) {
  if (!(arg instanceof v1_ad_library_pb.SearchAdsBatchResponse)) {
    throw new Error('Expected argument of type fb_agent.ad_library.v1.SearchAdsBatchResponse');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_ad_library_v1_SearchAdsBatchResponse(buffer_arg) {
  return v1_ad_library_pb.SearchAdsBatchResponse.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_ad_library_v1_SearchAdsRequest(arg) {
  if (!(arg instanceof v1_ad_library_pb.SearchAdsRequest)) {
    throw new Error('Expected argument of type fb_agent.ad_library.v1.SearchAdsRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_ad_library_v1_SearchAdsRequest(buffer_arg) {
  return v1_ad_library_pb.SearchAdsRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_ad_library_v1_SearchAdsResponse(arg) {
  if (!(arg instanceof v1_ad_library_pb.SearchAdsResponse)) {
    throw new Error('Expected argument of type fb_agent.ad_library.v1.SearchAdsResponse');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_ad_library_v1_SearchAdsResponse(buffer_arg) {
  return v1_ad_library_pb.SearchAdsResponse.deserializeBinary(new Uint8Array(buffer_arg));
}


// AdLibraryService — поиск рекламы в Meta Ad Library через активную Vision-сессию.
//
// Архитектурная заметка: публичный Ad Library API (через токен) НЕ покрывает наши
// GEO (Африка/LatAm/Турция — outside EU/UK). Standalone scraper'ы (curl_cffi,
// requests) моментально rate-limit'ятся Meta по IP/fingerprint.
//
// Поэтому работает session-tunneling: browser-agent открывает Ad Library страницу
// в новой вкладке той же Vision-сессии (где уже залогинен Facebook), перехватывает
// GraphQL responses и возвращает ads через gRPC. Meta видит залогиненного юзера,
// rate-limit мягкий.
var AdLibraryServiceService = exports.AdLibraryServiceService = {
  // Поиск ads по keyword + country. Возвращает массив raw GraphQL ads
// (структура сохранена для гибкого парсинга на Python-стороне).
searchAds: {
    path: '/fb_agent.ad_library.v1.AdLibraryService/SearchAds',
    requestStream: false,
    responseStream: false,
    requestType: v1_ad_library_pb.SearchAdsRequest,
    responseType: v1_ad_library_pb.SearchAdsResponse,
    requestSerialize: serialize_fb_agent_ad_library_v1_SearchAdsRequest,
    requestDeserialize: deserialize_fb_agent_ad_library_v1_SearchAdsRequest,
    responseSerialize: serialize_fb_agent_ad_library_v1_SearchAdsResponse,
    responseDeserialize: deserialize_fb_agent_ad_library_v1_SearchAdsResponse,
  },
  // Batch-поиск: открывает Ad Library один раз для country, прогоняет все
// queries через input.fill() (как реальный юзер), возвращает результаты пачкой.
// Это стабильнее чем серия SearchAds — Meta не блокирует повторные fetch
// потому что юзер "печатает" в input, а не делает goto на новый URL.
searchAdsBatch: {
    path: '/fb_agent.ad_library.v1.AdLibraryService/SearchAdsBatch',
    requestStream: false,
    responseStream: false,
    requestType: v1_ad_library_pb.SearchAdsBatchRequest,
    responseType: v1_ad_library_pb.SearchAdsBatchResponse,
    requestSerialize: serialize_fb_agent_ad_library_v1_SearchAdsBatchRequest,
    requestDeserialize: deserialize_fb_agent_ad_library_v1_SearchAdsBatchRequest,
    responseSerialize: serialize_fb_agent_ad_library_v1_SearchAdsBatchResponse,
    responseDeserialize: deserialize_fb_agent_ad_library_v1_SearchAdsBatchResponse,
  },
  // Проверить готовность канала: жив ли browser context, доступна ли Ad Library.
// Не делает реальных запросов к Meta — только проверка окружения.
checkAdLibraryHealth: {
    path: '/fb_agent.ad_library.v1.AdLibraryService/CheckAdLibraryHealth',
    requestStream: false,
    responseStream: false,
    requestType: v1_ad_library_pb.CheckAdLibraryHealthRequest,
    responseType: v1_ad_library_pb.CheckAdLibraryHealthResponse,
    requestSerialize: serialize_fb_agent_ad_library_v1_CheckAdLibraryHealthRequest,
    requestDeserialize: deserialize_fb_agent_ad_library_v1_CheckAdLibraryHealthRequest,
    responseSerialize: serialize_fb_agent_ad_library_v1_CheckAdLibraryHealthResponse,
    responseDeserialize: deserialize_fb_agent_ad_library_v1_CheckAdLibraryHealthResponse,
  },
};

exports.AdLibraryServiceClient = grpc.makeGenericClientConstructor(AdLibraryServiceService, 'AdLibraryService');
