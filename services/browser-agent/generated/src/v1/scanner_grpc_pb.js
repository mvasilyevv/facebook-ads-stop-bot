// GENERATED CODE -- DO NOT EDIT!

'use strict';
var grpc = require('grpc');
var v1_scanner_pb = require('../v1/scanner_pb.js');

function serialize_fb_agent_scanner_v1_HardReloadPageRequest(arg) {
  if (!(arg instanceof v1_scanner_pb.HardReloadPageRequest)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.HardReloadPageRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_HardReloadPageRequest(buffer_arg) {
  return v1_scanner_pb.HardReloadPageRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_scanner_v1_HardReloadPageResponse(arg) {
  if (!(arg instanceof v1_scanner_pb.HardReloadPageResponse)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.HardReloadPageResponse');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_HardReloadPageResponse(buffer_arg) {
  return v1_scanner_pb.HardReloadPageResponse.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_scanner_v1_ListCampaignsRequest(arg) {
  if (!(arg instanceof v1_scanner_pb.ListCampaignsRequest)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.ListCampaignsRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_ListCampaignsRequest(buffer_arg) {
  return v1_scanner_pb.ListCampaignsRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_scanner_v1_ListCampaignsResponse(arg) {
  if (!(arg instanceof v1_scanner_pb.ListCampaignsResponse)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.ListCampaignsResponse');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_ListCampaignsResponse(buffer_arg) {
  return v1_scanner_pb.ListCampaignsResponse.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_scanner_v1_RunScanCycleRequest(arg) {
  if (!(arg instanceof v1_scanner_pb.RunScanCycleRequest)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.RunScanCycleRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_RunScanCycleRequest(buffer_arg) {
  return v1_scanner_pb.RunScanCycleRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_scanner_v1_ScanCycleEvent(arg) {
  if (!(arg instanceof v1_scanner_pb.ScanCycleEvent)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.ScanCycleEvent');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_ScanCycleEvent(buffer_arg) {
  return v1_scanner_pb.ScanCycleEvent.deserializeBinary(new Uint8Array(buffer_arg));
}


var ScannerServiceService = exports.ScannerServiceService = {
  // Полный цикл сканирования через am_tabular (graph-канал UI, active replication).
// Стримит один ScanComplete (DOM-парсинг/скролл выпилены).
runScanCycle: {
    path: '/fb_agent.scanner.v1.ScannerService/RunScanCycle',
    requestStream: false,
    responseStream: true,
    requestType: v1_scanner_pb.RunScanCycleRequest,
    responseType: v1_scanner_pb.ScanCycleEvent,
    requestSerialize: serialize_fb_agent_scanner_v1_RunScanCycleRequest,
    requestDeserialize: deserialize_fb_agent_scanner_v1_RunScanCycleRequest,
    responseSerialize: serialize_fb_agent_scanner_v1_ScanCycleEvent,
    responseDeserialize: deserialize_fb_agent_scanner_v1_ScanCycleEvent,
  },
  // Жёсткая перезагрузка страницы с очисткой кеша (через CDP Network.clearBrowserCache).
hardReloadPage: {
    path: '/fb_agent.scanner.v1.ScannerService/HardReloadPage',
    requestStream: false,
    responseStream: false,
    requestType: v1_scanner_pb.HardReloadPageRequest,
    responseType: v1_scanner_pb.HardReloadPageResponse,
    requestSerialize: serialize_fb_agent_scanner_v1_HardReloadPageRequest,
    requestDeserialize: deserialize_fb_agent_scanner_v1_HardReloadPageRequest,
    responseSerialize: serialize_fb_agent_scanner_v1_HardReloadPageResponse,
    responseDeserialize: deserialize_fb_agent_scanner_v1_HardReloadPageResponse,
  },
  // Live-список кампаний по owner_tag (через Graph campaigns edge, МИМО allowlist).
// Для UI «Кампании для сканирования»: показывает ВСЕ кампании владельца, включая
// новые, которые ещё не сканировались (allowlist их не пропускал бы в обычный скан).
listCampaigns: {
    path: '/fb_agent.scanner.v1.ScannerService/ListCampaigns',
    requestStream: false,
    responseStream: false,
    requestType: v1_scanner_pb.ListCampaignsRequest,
    responseType: v1_scanner_pb.ListCampaignsResponse,
    requestSerialize: serialize_fb_agent_scanner_v1_ListCampaignsRequest,
    requestDeserialize: deserialize_fb_agent_scanner_v1_ListCampaignsRequest,
    responseSerialize: serialize_fb_agent_scanner_v1_ListCampaignsResponse,
    responseDeserialize: deserialize_fb_agent_scanner_v1_ListCampaignsResponse,
  },
};

exports.ScannerServiceClient = grpc.makeGenericClientConstructor(ScannerServiceService, 'ScannerService');
