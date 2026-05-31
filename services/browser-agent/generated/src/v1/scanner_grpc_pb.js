// GENERATED CODE -- DO NOT EDIT!

'use strict';
var grpc = require('grpc');
var v1_scanner_pb = require('../v1/scanner_pb.js');

function serialize_fb_agent_scanner_v1_FindToggleCellRequest(arg) {
  if (!(arg instanceof v1_scanner_pb.FindToggleCellRequest)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.FindToggleCellRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_FindToggleCellRequest(buffer_arg) {
  return v1_scanner_pb.FindToggleCellRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_scanner_v1_FindToggleCellResponse(arg) {
  if (!(arg instanceof v1_scanner_pb.FindToggleCellResponse)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.FindToggleCellResponse');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_FindToggleCellResponse(buffer_arg) {
  return v1_scanner_pb.FindToggleCellResponse.deserializeBinary(new Uint8Array(buffer_arg));
}

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

function serialize_fb_agent_scanner_v1_HumanClickRequest(arg) {
  if (!(arg instanceof v1_scanner_pb.HumanClickRequest)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.HumanClickRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_HumanClickRequest(buffer_arg) {
  return v1_scanner_pb.HumanClickRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_scanner_v1_HumanClickResponse(arg) {
  if (!(arg instanceof v1_scanner_pb.HumanClickResponse)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.HumanClickResponse');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_HumanClickResponse(buffer_arg) {
  return v1_scanner_pb.HumanClickResponse.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_scanner_v1_HumanMoveRequest(arg) {
  if (!(arg instanceof v1_scanner_pb.HumanMoveRequest)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.HumanMoveRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_HumanMoveRequest(buffer_arg) {
  return v1_scanner_pb.HumanMoveRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_scanner_v1_HumanMoveResponse(arg) {
  if (!(arg instanceof v1_scanner_pb.HumanMoveResponse)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.HumanMoveResponse');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_HumanMoveResponse(buffer_arg) {
  return v1_scanner_pb.HumanMoveResponse.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_scanner_v1_HumanWheelScrollRequest(arg) {
  if (!(arg instanceof v1_scanner_pb.HumanWheelScrollRequest)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.HumanWheelScrollRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_HumanWheelScrollRequest(buffer_arg) {
  return v1_scanner_pb.HumanWheelScrollRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_scanner_v1_HumanWheelScrollResponse(arg) {
  if (!(arg instanceof v1_scanner_pb.HumanWheelScrollResponse)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.HumanWheelScrollResponse');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_HumanWheelScrollResponse(buffer_arg) {
  return v1_scanner_pb.HumanWheelScrollResponse.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_scanner_v1_ReadToggleStateRequest(arg) {
  if (!(arg instanceof v1_scanner_pb.ReadToggleStateRequest)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.ReadToggleStateRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_ReadToggleStateRequest(buffer_arg) {
  return v1_scanner_pb.ReadToggleStateRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_scanner_v1_ReadToggleStateResponse(arg) {
  if (!(arg instanceof v1_scanner_pb.ReadToggleStateResponse)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.ReadToggleStateResponse');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_ReadToggleStateResponse(buffer_arg) {
  return v1_scanner_pb.ReadToggleStateResponse.deserializeBinary(new Uint8Array(buffer_arg));
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

function serialize_fb_agent_scanner_v1_ToggleAdRequest(arg) {
  if (!(arg instanceof v1_scanner_pb.ToggleAdRequest)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.ToggleAdRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_ToggleAdRequest(buffer_arg) {
  return v1_scanner_pb.ToggleAdRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_scanner_v1_ToggleAdResponse(arg) {
  if (!(arg instanceof v1_scanner_pb.ToggleAdResponse)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.ToggleAdResponse');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_ToggleAdResponse(buffer_arg) {
  return v1_scanner_pb.ToggleAdResponse.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_scanner_v1_WaitForToggleConfirmationRequest(arg) {
  if (!(arg instanceof v1_scanner_pb.WaitForToggleConfirmationRequest)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.WaitForToggleConfirmationRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_WaitForToggleConfirmationRequest(buffer_arg) {
  return v1_scanner_pb.WaitForToggleConfirmationRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_scanner_v1_WaitForToggleConfirmationResponse(arg) {
  if (!(arg instanceof v1_scanner_pb.WaitForToggleConfirmationResponse)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.WaitForToggleConfirmationResponse');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_WaitForToggleConfirmationResponse(buffer_arg) {
  return v1_scanner_pb.WaitForToggleConfirmationResponse.deserializeBinary(new Uint8Array(buffer_arg));
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
  // Найти toggle-ячейку для конкретного объявления (скроллит таблицу).
findToggleCell: {
    path: '/fb_agent.scanner.v1.ScannerService/FindToggleCell',
    requestStream: false,
    responseStream: false,
    requestType: v1_scanner_pb.FindToggleCellRequest,
    responseType: v1_scanner_pb.FindToggleCellResponse,
    requestSerialize: serialize_fb_agent_scanner_v1_FindToggleCellRequest,
    requestDeserialize: deserialize_fb_agent_scanner_v1_FindToggleCellRequest,
    responseSerialize: serialize_fb_agent_scanner_v1_FindToggleCellResponse,
    responseDeserialize: deserialize_fb_agent_scanner_v1_FindToggleCellResponse,
  },
  // Прочитать aria-checked состояние toggle-ячейки.
readToggleState: {
    path: '/fb_agent.scanner.v1.ScannerService/ReadToggleState',
    requestStream: false,
    responseStream: false,
    requestType: v1_scanner_pb.ReadToggleStateRequest,
    responseType: v1_scanner_pb.ReadToggleStateResponse,
    requestSerialize: serialize_fb_agent_scanner_v1_ReadToggleStateRequest,
    requestDeserialize: deserialize_fb_agent_scanner_v1_ReadToggleStateRequest,
    responseSerialize: serialize_fb_agent_scanner_v1_ReadToggleStateResponse,
    responseDeserialize: deserialize_fb_agent_scanner_v1_ReadToggleStateResponse,
  },
  // Переключить on/off switch объявления.
toggleAd: {
    path: '/fb_agent.scanner.v1.ScannerService/ToggleAd',
    requestStream: false,
    responseStream: false,
    requestType: v1_scanner_pb.ToggleAdRequest,
    responseType: v1_scanner_pb.ToggleAdResponse,
    requestSerialize: serialize_fb_agent_scanner_v1_ToggleAdRequest,
    requestDeserialize: deserialize_fb_agent_scanner_v1_ToggleAdRequest,
    responseSerialize: serialize_fb_agent_scanner_v1_ToggleAdResponse,
    responseDeserialize: deserialize_fb_agent_scanner_v1_ToggleAdResponse,
  },
  // Human-like движение мыши к координатам.
humanMove: {
    path: '/fb_agent.scanner.v1.ScannerService/HumanMove',
    requestStream: false,
    responseStream: false,
    requestType: v1_scanner_pb.HumanMoveRequest,
    responseType: v1_scanner_pb.HumanMoveResponse,
    requestSerialize: serialize_fb_agent_scanner_v1_HumanMoveRequest,
    requestDeserialize: deserialize_fb_agent_scanner_v1_HumanMoveRequest,
    responseSerialize: serialize_fb_agent_scanner_v1_HumanMoveResponse,
    responseDeserialize: deserialize_fb_agent_scanner_v1_HumanMoveResponse,
  },
  // Human-like клик по координатам.
humanClick: {
    path: '/fb_agent.scanner.v1.ScannerService/HumanClick',
    requestStream: false,
    responseStream: false,
    requestType: v1_scanner_pb.HumanClickRequest,
    responseType: v1_scanner_pb.HumanClickResponse,
    requestSerialize: serialize_fb_agent_scanner_v1_HumanClickRequest,
    requestDeserialize: deserialize_fb_agent_scanner_v1_HumanClickRequest,
    responseSerialize: serialize_fb_agent_scanner_v1_HumanClickResponse,
    responseDeserialize: deserialize_fb_agent_scanner_v1_HumanClickResponse,
  },
  // Human-like скролл колесом.
humanWheelScroll: {
    path: '/fb_agent.scanner.v1.ScannerService/HumanWheelScroll',
    requestStream: false,
    responseStream: false,
    requestType: v1_scanner_pb.HumanWheelScrollRequest,
    responseType: v1_scanner_pb.HumanWheelScrollResponse,
    requestSerialize: serialize_fb_agent_scanner_v1_HumanWheelScrollRequest,
    requestDeserialize: deserialize_fb_agent_scanner_v1_HumanWheelScrollRequest,
    responseSerialize: serialize_fb_agent_scanner_v1_HumanWheelScrollResponse,
    responseDeserialize: deserialize_fb_agent_scanner_v1_HumanWheelScrollResponse,
  },
  // Ждать подтверждения toggle через повторные чтения aria-checked.
waitForToggleConfirmation: {
    path: '/fb_agent.scanner.v1.ScannerService/WaitForToggleConfirmation',
    requestStream: false,
    responseStream: false,
    requestType: v1_scanner_pb.WaitForToggleConfirmationRequest,
    responseType: v1_scanner_pb.WaitForToggleConfirmationResponse,
    requestSerialize: serialize_fb_agent_scanner_v1_WaitForToggleConfirmationRequest,
    requestDeserialize: deserialize_fb_agent_scanner_v1_WaitForToggleConfirmationRequest,
    responseSerialize: serialize_fb_agent_scanner_v1_WaitForToggleConfirmationResponse,
    responseDeserialize: deserialize_fb_agent_scanner_v1_WaitForToggleConfirmationResponse,
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
};

exports.ScannerServiceClient = grpc.makeGenericClientConstructor(ScannerServiceService, 'ScannerService');
