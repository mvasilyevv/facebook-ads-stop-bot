from collections.abc import Iterable as _Iterable
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar
from typing import Optional as _Optional
from typing import Union as _Union

from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from google.protobuf.internal import containers as _containers

DESCRIPTOR: _descriptor.FileDescriptor

class SearchAdsRequest(_message.Message):
    __slots__ = (
        "session_id",
        "country",
        "query",
        "active_status",
        "ad_type",
        "timeout_ms",
        "search_type",
        "max_pages",
        "page_size",
    )
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    COUNTRY_FIELD_NUMBER: _ClassVar[int]
    QUERY_FIELD_NUMBER: _ClassVar[int]
    ACTIVE_STATUS_FIELD_NUMBER: _ClassVar[int]
    AD_TYPE_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_MS_FIELD_NUMBER: _ClassVar[int]
    SEARCH_TYPE_FIELD_NUMBER: _ClassVar[int]
    MAX_PAGES_FIELD_NUMBER: _ClassVar[int]
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    country: str
    query: str
    active_status: str
    ad_type: str
    timeout_ms: int
    search_type: str
    max_pages: int
    page_size: int
    def __init__(
        self,
        session_id: _Optional[str] = ...,
        country: _Optional[str] = ...,
        query: _Optional[str] = ...,
        active_status: _Optional[str] = ...,
        ad_type: _Optional[str] = ...,
        timeout_ms: _Optional[int] = ...,
        search_type: _Optional[str] = ...,
        max_pages: _Optional[int] = ...,
        page_size: _Optional[int] = ...,
    ) -> None: ...

class SearchAdsResponse(_message.Message):
    __slots__ = ("ad_count", "ads_json", "duration_ms", "pages_fetched", "error")
    AD_COUNT_FIELD_NUMBER: _ClassVar[int]
    ADS_JSON_FIELD_NUMBER: _ClassVar[int]
    DURATION_MS_FIELD_NUMBER: _ClassVar[int]
    PAGES_FETCHED_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    ad_count: int
    ads_json: str
    duration_ms: int
    pages_fetched: int
    error: AdLibraryError
    def __init__(
        self,
        ad_count: _Optional[int] = ...,
        ads_json: _Optional[str] = ...,
        duration_ms: _Optional[int] = ...,
        pages_fetched: _Optional[int] = ...,
        error: _Optional[_Union[AdLibraryError, _Mapping]] = ...,
    ) -> None: ...

class AdLibraryError(_message.Message):
    __slots__ = ("code", "type", "message")
    CODE_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    code: int
    type: str
    message: str
    def __init__(
        self, code: _Optional[int] = ..., type: _Optional[str] = ..., message: _Optional[str] = ...
    ) -> None: ...

class SearchAdsBatchRequest(_message.Message):
    __slots__ = (
        "session_id",
        "country",
        "queries",
        "active_status",
        "ad_type",
        "per_query_timeout_ms",
        "search_type",
        "max_pages",
        "page_size",
    )
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    COUNTRY_FIELD_NUMBER: _ClassVar[int]
    QUERIES_FIELD_NUMBER: _ClassVar[int]
    ACTIVE_STATUS_FIELD_NUMBER: _ClassVar[int]
    AD_TYPE_FIELD_NUMBER: _ClassVar[int]
    PER_QUERY_TIMEOUT_MS_FIELD_NUMBER: _ClassVar[int]
    SEARCH_TYPE_FIELD_NUMBER: _ClassVar[int]
    MAX_PAGES_FIELD_NUMBER: _ClassVar[int]
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    country: str
    queries: _containers.RepeatedScalarFieldContainer[str]
    active_status: str
    ad_type: str
    per_query_timeout_ms: int
    search_type: str
    max_pages: int
    page_size: int
    def __init__(
        self,
        session_id: _Optional[str] = ...,
        country: _Optional[str] = ...,
        queries: _Optional[_Iterable[str]] = ...,
        active_status: _Optional[str] = ...,
        ad_type: _Optional[str] = ...,
        per_query_timeout_ms: _Optional[int] = ...,
        search_type: _Optional[str] = ...,
        max_pages: _Optional[int] = ...,
        page_size: _Optional[int] = ...,
    ) -> None: ...

class SearchAdsBatchResponse(_message.Message):
    __slots__ = ("results", "total_duration_ms")
    RESULTS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_DURATION_MS_FIELD_NUMBER: _ClassVar[int]
    results: _containers.RepeatedCompositeFieldContainer[QueryResult]
    total_duration_ms: int
    def __init__(
        self,
        results: _Optional[_Iterable[_Union[QueryResult, _Mapping]]] = ...,
        total_duration_ms: _Optional[int] = ...,
    ) -> None: ...

class QueryResult(_message.Message):
    __slots__ = ("query", "ad_count", "ads_json", "duration_ms", "pages_fetched", "error")
    QUERY_FIELD_NUMBER: _ClassVar[int]
    AD_COUNT_FIELD_NUMBER: _ClassVar[int]
    ADS_JSON_FIELD_NUMBER: _ClassVar[int]
    DURATION_MS_FIELD_NUMBER: _ClassVar[int]
    PAGES_FETCHED_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    query: str
    ad_count: int
    ads_json: str
    duration_ms: int
    pages_fetched: int
    error: AdLibraryError
    def __init__(
        self,
        query: _Optional[str] = ...,
        ad_count: _Optional[int] = ...,
        ads_json: _Optional[str] = ...,
        duration_ms: _Optional[int] = ...,
        pages_fetched: _Optional[int] = ...,
        error: _Optional[_Union[AdLibraryError, _Mapping]] = ...,
    ) -> None: ...

class CheckAdLibraryHealthRequest(_message.Message):
    __slots__ = ("session_id",)
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    def __init__(self, session_id: _Optional[str] = ...) -> None: ...

class CheckAdLibraryHealthResponse(_message.Message):
    __slots__ = ("healthy", "detail")
    HEALTHY_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    healthy: bool
    detail: str
    def __init__(self, healthy: bool = ..., detail: _Optional[str] = ...) -> None: ...
