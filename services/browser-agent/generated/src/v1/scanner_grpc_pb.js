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

function serialize_fb_agent_scanner_v1_GetScrollMetricsRequest(arg) {
  if (!(arg instanceof v1_scanner_pb.GetScrollMetricsRequest)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.GetScrollMetricsRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_GetScrollMetricsRequest(buffer_arg) {
  return v1_scanner_pb.GetScrollMetricsRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_scanner_v1_GetScrollMetricsResponse(arg) {
  if (!(arg instanceof v1_scanner_pb.GetScrollMetricsResponse)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.GetScrollMetricsResponse');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_GetScrollMetricsResponse(buffer_arg) {
  return v1_scanner_pb.GetScrollMetricsResponse.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_scanner_v1_GetVisibleRowIdsRequest(arg) {
  if (!(arg instanceof v1_scanner_pb.GetVisibleRowIdsRequest)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.GetVisibleRowIdsRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_GetVisibleRowIdsRequest(buffer_arg) {
  return v1_scanner_pb.GetVisibleRowIdsRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_scanner_v1_GetVisibleRowIdsResponse(arg) {
  if (!(arg instanceof v1_scanner_pb.GetVisibleRowIdsResponse)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.GetVisibleRowIdsResponse');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_GetVisibleRowIdsResponse(buffer_arg) {
  return v1_scanner_pb.GetVisibleRowIdsResponse.deserializeBinary(new Uint8Array(buffer_arg));
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

function serialize_fb_agent_scanner_v1_ParseVisibleRowsRequest(arg) {
  if (!(arg instanceof v1_scanner_pb.ParseVisibleRowsRequest)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.ParseVisibleRowsRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_ParseVisibleRowsRequest(buffer_arg) {
  return v1_scanner_pb.ParseVisibleRowsRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_scanner_v1_ParseVisibleRowsResponse(arg) {
  if (!(arg instanceof v1_scanner_pb.ParseVisibleRowsResponse)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.ParseVisibleRowsResponse');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_ParseVisibleRowsResponse(buffer_arg) {
  return v1_scanner_pb.ParseVisibleRowsResponse.deserializeBinary(new Uint8Array(buffer_arg));
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

function serialize_fb_agent_scanner_v1_RefreshTableRequest(arg) {
  if (!(arg instanceof v1_scanner_pb.RefreshTableRequest)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.RefreshTableRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_RefreshTableRequest(buffer_arg) {
  return v1_scanner_pb.RefreshTableRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_scanner_v1_RefreshTableResponse(arg) {
  if (!(arg instanceof v1_scanner_pb.RefreshTableResponse)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.RefreshTableResponse');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_RefreshTableResponse(buffer_arg) {
  return v1_scanner_pb.RefreshTableResponse.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_scanner_v1_ResetScrollRequest(arg) {
  if (!(arg instanceof v1_scanner_pb.ResetScrollRequest)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.ResetScrollRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_ResetScrollRequest(buffer_arg) {
  return v1_scanner_pb.ResetScrollRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_scanner_v1_ResetScrollResponse(arg) {
  if (!(arg instanceof v1_scanner_pb.ResetScrollResponse)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.ResetScrollResponse');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_ResetScrollResponse(buffer_arg) {
  return v1_scanner_pb.ResetScrollResponse.deserializeBinary(new Uint8Array(buffer_arg));
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

function serialize_fb_agent_scanner_v1_ScrollAndParseRequest(arg) {
  if (!(arg instanceof v1_scanner_pb.ScrollAndParseRequest)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.ScrollAndParseRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_ScrollAndParseRequest(buffer_arg) {
  return v1_scanner_pb.ScrollAndParseRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_scanner_v1_ScrollAndParseResponse(arg) {
  if (!(arg instanceof v1_scanner_pb.ScrollAndParseResponse)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.ScrollAndParseResponse');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_ScrollAndParseResponse(buffer_arg) {
  return v1_scanner_pb.ScrollAndParseResponse.deserializeBinary(new Uint8Array(buffer_arg));
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

function serialize_fb_agent_scanner_v1_ValidateColumnsRequest(arg) {
  if (!(arg instanceof v1_scanner_pb.ValidateColumnsRequest)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.ValidateColumnsRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_ValidateColumnsRequest(buffer_arg) {
  return v1_scanner_pb.ValidateColumnsRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_scanner_v1_ValidateColumnsResponse(arg) {
  if (!(arg instanceof v1_scanner_pb.ValidateColumnsResponse)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.ValidateColumnsResponse');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_ValidateColumnsResponse(buffer_arg) {
  return v1_scanner_pb.ValidateColumnsResponse.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_scanner_v1_WaitForDomStableRequest(arg) {
  if (!(arg instanceof v1_scanner_pb.WaitForDomStableRequest)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.WaitForDomStableRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_WaitForDomStableRequest(buffer_arg) {
  return v1_scanner_pb.WaitForDomStableRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fb_agent_scanner_v1_WaitForDomStableResponse(arg) {
  if (!(arg instanceof v1_scanner_pb.WaitForDomStableResponse)) {
    throw new Error('Expected argument of type fb_agent.scanner.v1.WaitForDomStableResponse');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fb_agent_scanner_v1_WaitForDomStableResponse(buffer_arg) {
  return v1_scanner_pb.WaitForDomStableResponse.deserializeBinary(new Uint8Array(buffer_arg));
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
  // Полный цикл сканирования: reset scroll → refresh → settle → scroll-and-parse.
// Стримит частичные результаты (rows per pass).
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
  // Обновить таблицу Ads Manager (клик по кнопке "Refresh").
refreshTable: {
    path: '/fb_agent.scanner.v1.ScannerService/RefreshTable',
    requestStream: false,
    responseStream: false,
    requestType: v1_scanner_pb.RefreshTableRequest,
    responseType: v1_scanner_pb.RefreshTableResponse,
    requestSerialize: serialize_fb_agent_scanner_v1_RefreshTableRequest,
    requestDeserialize: deserialize_fb_agent_scanner_v1_RefreshTableRequest,
    responseSerialize: serialize_fb_agent_scanner_v1_RefreshTableResponse,
    responseDeserialize: deserialize_fb_agent_scanner_v1_RefreshTableResponse,
  },
  // Распарсить видимые строки из текущего viewport (без скролла).
parseVisibleRows: {
    path: '/fb_agent.scanner.v1.ScannerService/ParseVisibleRows',
    requestStream: false,
    responseStream: false,
    requestType: v1_scanner_pb.ParseVisibleRowsRequest,
    responseType: v1_scanner_pb.ParseVisibleRowsResponse,
    requestSerialize: serialize_fb_agent_scanner_v1_ParseVisibleRowsRequest,
    requestDeserialize: deserialize_fb_agent_scanner_v1_ParseVisibleRowsRequest,
    responseSerialize: serialize_fb_agent_scanner_v1_ParseVisibleRowsResponse,
    responseDeserialize: deserialize_fb_agent_scanner_v1_ParseVisibleRowsResponse,
  },
  // Скроллить таблицу вниз и распарсить новые строки.
scrollAndParse: {
    path: '/fb_agent.scanner.v1.ScannerService/ScrollAndParse',
    requestStream: false,
    responseStream: false,
    requestType: v1_scanner_pb.ScrollAndParseRequest,
    responseType: v1_scanner_pb.ScrollAndParseResponse,
    requestSerialize: serialize_fb_agent_scanner_v1_ScrollAndParseRequest,
    requestDeserialize: deserialize_fb_agent_scanner_v1_ScrollAndParseRequest,
    responseSerialize: serialize_fb_agent_scanner_v1_ScrollAndParseResponse,
    responseDeserialize: deserialize_fb_agent_scanner_v1_ScrollAndParseResponse,
  },
  // Дождаться стабилизации DOM после скролла (количество строк перестаёт меняться).
waitForDomStable: {
    path: '/fb_agent.scanner.v1.ScannerService/WaitForDomStable',
    requestStream: false,
    responseStream: false,
    requestType: v1_scanner_pb.WaitForDomStableRequest,
    responseType: v1_scanner_pb.WaitForDomStableResponse,
    requestSerialize: serialize_fb_agent_scanner_v1_WaitForDomStableRequest,
    requestDeserialize: deserialize_fb_agent_scanner_v1_WaitForDomStableRequest,
    responseSerialize: serialize_fb_agent_scanner_v1_WaitForDomStableResponse,
    responseDeserialize: deserialize_fb_agent_scanner_v1_WaitForDomStableResponse,
  },
  // Сбросить позицию скролла таблицы наверх.
resetScroll: {
    path: '/fb_agent.scanner.v1.ScannerService/ResetScroll',
    requestStream: false,
    responseStream: false,
    requestType: v1_scanner_pb.ResetScrollRequest,
    responseType: v1_scanner_pb.ResetScrollResponse,
    requestSerialize: serialize_fb_agent_scanner_v1_ResetScrollRequest,
    requestDeserialize: deserialize_fb_agent_scanner_v1_ResetScrollRequest,
    responseSerialize: serialize_fb_agent_scanner_v1_ResetScrollResponse,
    responseDeserialize: deserialize_fb_agent_scanner_v1_ResetScrollResponse,
  },
  // Получить текущие метрики скролла (scrollTop, maxScrollTop, atBottom).
getScrollMetrics: {
    path: '/fb_agent.scanner.v1.ScannerService/GetScrollMetrics',
    requestStream: false,
    responseStream: false,
    requestType: v1_scanner_pb.GetScrollMetricsRequest,
    responseType: v1_scanner_pb.GetScrollMetricsResponse,
    requestSerialize: serialize_fb_agent_scanner_v1_GetScrollMetricsRequest,
    requestDeserialize: deserialize_fb_agent_scanner_v1_GetScrollMetricsRequest,
    responseSerialize: serialize_fb_agent_scanner_v1_GetScrollMetricsResponse,
    responseDeserialize: deserialize_fb_agent_scanner_v1_GetScrollMetricsResponse,
  },
  // Получить ID видимых строк в DOM.
getVisibleRowIds: {
    path: '/fb_agent.scanner.v1.ScannerService/GetVisibleRowIds',
    requestStream: false,
    responseStream: false,
    requestType: v1_scanner_pb.GetVisibleRowIdsRequest,
    responseType: v1_scanner_pb.GetVisibleRowIdsResponse,
    requestSerialize: serialize_fb_agent_scanner_v1_GetVisibleRowIdsRequest,
    requestDeserialize: deserialize_fb_agent_scanner_v1_GetVisibleRowIdsRequest,
    responseSerialize: serialize_fb_agent_scanner_v1_GetVisibleRowIdsResponse,
    responseDeserialize: deserialize_fb_agent_scanner_v1_GetVisibleRowIdsResponse,
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
  // Проверить наличие всех необходимых колонок в таблице Ads Manager.
validateColumns: {
    path: '/fb_agent.scanner.v1.ScannerService/ValidateColumns',
    requestStream: false,
    responseStream: false,
    requestType: v1_scanner_pb.ValidateColumnsRequest,
    responseType: v1_scanner_pb.ValidateColumnsResponse,
    requestSerialize: serialize_fb_agent_scanner_v1_ValidateColumnsRequest,
    requestDeserialize: deserialize_fb_agent_scanner_v1_ValidateColumnsRequest,
    responseSerialize: serialize_fb_agent_scanner_v1_ValidateColumnsResponse,
    responseDeserialize: deserialize_fb_agent_scanner_v1_ValidateColumnsResponse,
  },
};

exports.ScannerServiceClient = grpc.makeGenericClientConstructor(ScannerServiceService, 'ScannerService');
