from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar
from typing import Optional as _Optional
from typing import Union as _Union

from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from google.protobuf.internal import containers as _containers

DESCRIPTOR: _descriptor.FileDescriptor

class ExecuteGraphCallRequest(_message.Message):
    __slots__ = (
        "session_id",
        "method",
        "endpoint",
        "query_params",
        "body_json",
        "timeout_ms",
        "ad_account_id",
        "vision_profile_id",
        "authorized_caller",
        "task_id",
        "lease_owner",
        "lease_token",
        "capability_expires_at",
        "capability_nonce",
        "capability_signature",
    )
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
    AD_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    VISION_PROFILE_ID_FIELD_NUMBER: _ClassVar[int]
    AUTHORIZED_CALLER_FIELD_NUMBER: _ClassVar[int]
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    LEASE_OWNER_FIELD_NUMBER: _ClassVar[int]
    LEASE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    CAPABILITY_EXPIRES_AT_FIELD_NUMBER: _ClassVar[int]
    CAPABILITY_NONCE_FIELD_NUMBER: _ClassVar[int]
    CAPABILITY_SIGNATURE_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    method: str
    endpoint: str
    query_params: _containers.ScalarMap[str, str]
    body_json: str
    timeout_ms: int
    ad_account_id: str
    vision_profile_id: str
    authorized_caller: str
    task_id: int
    lease_owner: str
    lease_token: int
    capability_expires_at: int
    capability_nonce: str
    capability_signature: str
    def __init__(
        self,
        session_id: _Optional[str] = ...,
        method: _Optional[str] = ...,
        endpoint: _Optional[str] = ...,
        query_params: _Optional[_Mapping[str, str]] = ...,
        body_json: _Optional[str] = ...,
        timeout_ms: _Optional[int] = ...,
        ad_account_id: _Optional[str] = ...,
        vision_profile_id: _Optional[str] = ...,
        authorized_caller: _Optional[str] = ...,
        task_id: _Optional[int] = ...,
        lease_owner: _Optional[str] = ...,
        lease_token: _Optional[int] = ...,
        capability_expires_at: _Optional[int] = ...,
        capability_nonce: _Optional[str] = ...,
        capability_signature: _Optional[str] = ...,
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
    __slots__ = ("session_id", "full_probe", "expected_vision_profile_id")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    FULL_PROBE_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_VISION_PROFILE_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    full_probe: bool
    expected_vision_profile_id: str
    def __init__(
        self,
        session_id: _Optional[str] = ...,
        full_probe: bool = ...,
        expected_vision_profile_id: _Optional[str] = ...,
    ) -> None: ...

class CheckMetaApiHealthResponse(_message.Message):
    __slots__ = (
        "healthy",
        "current_url",
        "token_present",
        "token_length",
        "detail",
        "probe_performed",
        "probe_ok",
        "probe_status_code",
        "probe_duration_ms",
        "probe_detail",
        "browser_contract_version",
        "session_id",
        "vision_profile_id",
    )
    HEALTHY_FIELD_NUMBER: _ClassVar[int]
    CURRENT_URL_FIELD_NUMBER: _ClassVar[int]
    TOKEN_PRESENT_FIELD_NUMBER: _ClassVar[int]
    TOKEN_LENGTH_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    PROBE_PERFORMED_FIELD_NUMBER: _ClassVar[int]
    PROBE_OK_FIELD_NUMBER: _ClassVar[int]
    PROBE_STATUS_CODE_FIELD_NUMBER: _ClassVar[int]
    PROBE_DURATION_MS_FIELD_NUMBER: _ClassVar[int]
    PROBE_DETAIL_FIELD_NUMBER: _ClassVar[int]
    BROWSER_CONTRACT_VERSION_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    VISION_PROFILE_ID_FIELD_NUMBER: _ClassVar[int]
    healthy: bool
    current_url: str
    token_present: bool
    token_length: int
    detail: str
    probe_performed: bool
    probe_ok: bool
    probe_status_code: int
    probe_duration_ms: int
    probe_detail: str
    browser_contract_version: int
    session_id: str
    vision_profile_id: str
    def __init__(
        self,
        healthy: bool = ...,
        current_url: _Optional[str] = ...,
        token_present: bool = ...,
        token_length: _Optional[int] = ...,
        detail: _Optional[str] = ...,
        probe_performed: bool = ...,
        probe_ok: bool = ...,
        probe_status_code: _Optional[int] = ...,
        probe_duration_ms: _Optional[int] = ...,
        probe_detail: _Optional[str] = ...,
        browser_contract_version: _Optional[int] = ...,
        session_id: _Optional[str] = ...,
        vision_profile_id: _Optional[str] = ...,
    ) -> None: ...

class UploadImageRequest(_message.Message):
    __slots__ = (
        "session_id",
        "ad_account_id",
        "filename",
        "content_type",
        "file_bytes",
        "vision_profile_id",
        "authorized_caller",
        "task_id",
        "lease_owner",
        "lease_token",
        "capability_expires_at",
        "capability_nonce",
        "capability_signature",
    )
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    AD_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    FILENAME_FIELD_NUMBER: _ClassVar[int]
    CONTENT_TYPE_FIELD_NUMBER: _ClassVar[int]
    FILE_BYTES_FIELD_NUMBER: _ClassVar[int]
    VISION_PROFILE_ID_FIELD_NUMBER: _ClassVar[int]
    AUTHORIZED_CALLER_FIELD_NUMBER: _ClassVar[int]
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    LEASE_OWNER_FIELD_NUMBER: _ClassVar[int]
    LEASE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    CAPABILITY_EXPIRES_AT_FIELD_NUMBER: _ClassVar[int]
    CAPABILITY_NONCE_FIELD_NUMBER: _ClassVar[int]
    CAPABILITY_SIGNATURE_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    ad_account_id: str
    filename: str
    content_type: str
    file_bytes: bytes
    vision_profile_id: str
    authorized_caller: str
    task_id: int
    lease_owner: str
    lease_token: int
    capability_expires_at: int
    capability_nonce: str
    capability_signature: str
    def __init__(
        self,
        session_id: _Optional[str] = ...,
        ad_account_id: _Optional[str] = ...,
        filename: _Optional[str] = ...,
        content_type: _Optional[str] = ...,
        file_bytes: _Optional[bytes] = ...,
        vision_profile_id: _Optional[str] = ...,
        authorized_caller: _Optional[str] = ...,
        task_id: _Optional[int] = ...,
        lease_owner: _Optional[str] = ...,
        lease_token: _Optional[int] = ...,
        capability_expires_at: _Optional[int] = ...,
        capability_nonce: _Optional[str] = ...,
        capability_signature: _Optional[str] = ...,
    ) -> None: ...

class UploadImageResponse(_message.Message):
    __slots__ = ("image_hash", "ok", "error", "url", "duration_ms")
    IMAGE_HASH_FIELD_NUMBER: _ClassVar[int]
    OK_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    DURATION_MS_FIELD_NUMBER: _ClassVar[int]
    image_hash: str
    ok: bool
    error: str
    url: str
    duration_ms: int
    def __init__(
        self,
        image_hash: _Optional[str] = ...,
        ok: bool = ...,
        error: _Optional[str] = ...,
        url: _Optional[str] = ...,
        duration_ms: _Optional[int] = ...,
    ) -> None: ...

class UploadVideoChunk(_message.Message):
    __slots__ = (
        "session_id",
        "ad_account_id",
        "filename",
        "file_size",
        "chunk_bytes",
        "chunk_index",
        "is_last_chunk",
        "is_init",
        "vision_profile_id",
        "authorized_caller",
        "task_id",
        "lease_owner",
        "lease_token",
        "capability_expires_at",
        "capability_nonce",
        "capability_signature",
    )
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    AD_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    FILENAME_FIELD_NUMBER: _ClassVar[int]
    FILE_SIZE_FIELD_NUMBER: _ClassVar[int]
    CHUNK_BYTES_FIELD_NUMBER: _ClassVar[int]
    CHUNK_INDEX_FIELD_NUMBER: _ClassVar[int]
    IS_LAST_CHUNK_FIELD_NUMBER: _ClassVar[int]
    IS_INIT_FIELD_NUMBER: _ClassVar[int]
    VISION_PROFILE_ID_FIELD_NUMBER: _ClassVar[int]
    AUTHORIZED_CALLER_FIELD_NUMBER: _ClassVar[int]
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    LEASE_OWNER_FIELD_NUMBER: _ClassVar[int]
    LEASE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    CAPABILITY_EXPIRES_AT_FIELD_NUMBER: _ClassVar[int]
    CAPABILITY_NONCE_FIELD_NUMBER: _ClassVar[int]
    CAPABILITY_SIGNATURE_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    ad_account_id: str
    filename: str
    file_size: int
    chunk_bytes: bytes
    chunk_index: int
    is_last_chunk: bool
    is_init: bool
    vision_profile_id: str
    authorized_caller: str
    task_id: int
    lease_owner: str
    lease_token: int
    capability_expires_at: int
    capability_nonce: str
    capability_signature: str
    def __init__(
        self,
        session_id: _Optional[str] = ...,
        ad_account_id: _Optional[str] = ...,
        filename: _Optional[str] = ...,
        file_size: _Optional[int] = ...,
        chunk_bytes: _Optional[bytes] = ...,
        chunk_index: _Optional[int] = ...,
        is_last_chunk: bool = ...,
        is_init: bool = ...,
        vision_profile_id: _Optional[str] = ...,
        authorized_caller: _Optional[str] = ...,
        task_id: _Optional[int] = ...,
        lease_owner: _Optional[str] = ...,
        lease_token: _Optional[int] = ...,
        capability_expires_at: _Optional[int] = ...,
        capability_nonce: _Optional[str] = ...,
        capability_signature: _Optional[str] = ...,
    ) -> None: ...

class UploadVideoResponse(_message.Message):
    __slots__ = ("video_id", "ok", "error", "duration_ms", "chunks_processed")
    VIDEO_ID_FIELD_NUMBER: _ClassVar[int]
    OK_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    DURATION_MS_FIELD_NUMBER: _ClassVar[int]
    CHUNKS_PROCESSED_FIELD_NUMBER: _ClassVar[int]
    video_id: str
    ok: bool
    error: str
    duration_ms: int
    chunks_processed: int
    def __init__(
        self,
        video_id: _Optional[str] = ...,
        ok: bool = ...,
        error: _Optional[str] = ...,
        duration_ms: _Optional[int] = ...,
        chunks_processed: _Optional[int] = ...,
    ) -> None: ...
