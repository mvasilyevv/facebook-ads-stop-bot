from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar
from typing import Optional as _Optional
from typing import Union as _Union

from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from google.protobuf.internal import containers as _containers

DESCRIPTOR: _descriptor.FileDescriptor

class ExecuteGraphCallRequest(_message.Message):
    __slots__ = ("session_id", "method", "endpoint", "query_params", "body_json", "timeout_ms")
    class QueryParamsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...

    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    METHOD_FIELD_NUMBER: _ClassVar[int]
    ENDPOINT_FIELD_NUMBER: _ClassVar[int]
    QUERY_PARAMS_FIELD_NUMBER: _ClassVar[int]
    BODY_JSON_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_MS_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    method: str
    endpoint: str
    query_params: _containers.ScalarMap[str, str]
    body_json: str
    timeout_ms: int
    def __init__(
        self,
        session_id: _Optional[str] = ...,
        method: _Optional[str] = ...,
        endpoint: _Optional[str] = ...,
        query_params: _Optional[_Mapping[str, str]] = ...,
        body_json: _Optional[str] = ...,
        timeout_ms: _Optional[int] = ...,
    ) -> None: ...

class ExecuteGraphCallResponse(_message.Message):
    __slots__ = ("status_code", "response_json", "duration_ms", "error")
    STATUS_CODE_FIELD_NUMBER: _ClassVar[int]
    RESPONSE_JSON_FIELD_NUMBER: _ClassVar[int]
    DURATION_MS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    status_code: int
    response_json: str
    duration_ms: int
    error: GraphApiError
    def __init__(
        self,
        status_code: _Optional[int] = ...,
        response_json: _Optional[str] = ...,
        duration_ms: _Optional[int] = ...,
        error: _Optional[_Union[GraphApiError, _Mapping]] = ...,
    ) -> None: ...

class GraphApiError(_message.Message):
    __slots__ = ("code", "subcode", "type", "message", "fbtrace_id")
    CODE_FIELD_NUMBER: _ClassVar[int]
    SUBCODE_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    FBTRACE_ID_FIELD_NUMBER: _ClassVar[int]
    code: int
    subcode: int
    type: str
    message: str
    fbtrace_id: str
    def __init__(
        self,
        code: _Optional[int] = ...,
        subcode: _Optional[int] = ...,
        type: _Optional[str] = ...,
        message: _Optional[str] = ...,
        fbtrace_id: _Optional[str] = ...,
    ) -> None: ...

class CheckMetaApiHealthRequest(_message.Message):
    __slots__ = ("session_id",)
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    def __init__(self, session_id: _Optional[str] = ...) -> None: ...

class CheckMetaApiHealthResponse(_message.Message):
    __slots__ = ("healthy", "current_url", "token_present", "token_length", "detail")
    HEALTHY_FIELD_NUMBER: _ClassVar[int]
    CURRENT_URL_FIELD_NUMBER: _ClassVar[int]
    TOKEN_PRESENT_FIELD_NUMBER: _ClassVar[int]
    TOKEN_LENGTH_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    healthy: bool
    current_url: str
    token_present: bool
    token_length: int
    detail: str
    def __init__(
        self,
        healthy: bool = ...,
        current_url: _Optional[str] = ...,
        token_present: bool = ...,
        token_length: _Optional[int] = ...,
        detail: _Optional[str] = ...,
    ) -> None: ...
