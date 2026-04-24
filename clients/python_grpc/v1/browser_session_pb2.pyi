from collections.abc import Iterable as _Iterable
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar
from typing import Optional as _Optional
from typing import Union as _Union

from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from google.protobuf.internal import containers as _containers

DESCRIPTOR: _descriptor.FileDescriptor

class StartBrowserRequest(_message.Message):
    __slots__ = (
        "vision_x_token",
        "vision_api_url",
        "vision_profile_id",
        "vision_folder_id",
        "viewport_width",
        "viewport_height",
    )
    VISION_X_TOKEN_FIELD_NUMBER: _ClassVar[int]
    VISION_API_URL_FIELD_NUMBER: _ClassVar[int]
    VISION_PROFILE_ID_FIELD_NUMBER: _ClassVar[int]
    VISION_FOLDER_ID_FIELD_NUMBER: _ClassVar[int]
    VIEWPORT_WIDTH_FIELD_NUMBER: _ClassVar[int]
    VIEWPORT_HEIGHT_FIELD_NUMBER: _ClassVar[int]
    vision_x_token: str
    vision_api_url: str
    vision_profile_id: str
    vision_folder_id: str
    viewport_width: int
    viewport_height: int
    def __init__(
        self,
        vision_x_token: _Optional[str] = ...,
        vision_api_url: _Optional[str] = ...,
        vision_profile_id: _Optional[str] = ...,
        vision_folder_id: _Optional[str] = ...,
        viewport_width: _Optional[int] = ...,
        viewport_height: _Optional[int] = ...,
    ) -> None: ...

class StartBrowserResponse(_message.Message):
    __slots__ = ("session_id", "profile", "initial_page_url", "pages")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PROFILE_FIELD_NUMBER: _ClassVar[int]
    INITIAL_PAGE_URL_FIELD_NUMBER: _ClassVar[int]
    PAGES_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    profile: VisionProfile
    initial_page_url: str
    pages: _containers.RepeatedCompositeFieldContainer[PageInfo]
    def __init__(
        self,
        session_id: _Optional[str] = ...,
        profile: _Optional[_Union[VisionProfile, _Mapping]] = ...,
        initial_page_url: _Optional[str] = ...,
        pages: _Optional[_Iterable[_Union[PageInfo, _Mapping]]] = ...,
    ) -> None: ...

class VisionProfile(_message.Message):
    __slots__ = ("folder_id", "profile_id", "cdp_port")
    FOLDER_ID_FIELD_NUMBER: _ClassVar[int]
    PROFILE_ID_FIELD_NUMBER: _ClassVar[int]
    CDP_PORT_FIELD_NUMBER: _ClassVar[int]
    folder_id: str
    profile_id: str
    cdp_port: int
    def __init__(
        self,
        folder_id: _Optional[str] = ...,
        profile_id: _Optional[str] = ...,
        cdp_port: _Optional[int] = ...,
    ) -> None: ...

class PageInfo(_message.Message):
    __slots__ = ("page_id", "url", "title")
    PAGE_ID_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    page_id: str
    url: str
    title: str
    def __init__(
        self, page_id: _Optional[str] = ..., url: _Optional[str] = ..., title: _Optional[str] = ...
    ) -> None: ...

class DisconnectBrowserRequest(_message.Message):
    __slots__ = ("session_id",)
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    def __init__(self, session_id: _Optional[str] = ...) -> None: ...

class DisconnectBrowserResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class StopBrowserRequest(_message.Message):
    __slots__ = ("session_id",)
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    def __init__(self, session_id: _Optional[str] = ...) -> None: ...

class StopBrowserResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ReconnectBrowserRequest(_message.Message):
    __slots__ = ("session_id", "vision_x_token", "vision_api_url", "vision_profile_id")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    VISION_X_TOKEN_FIELD_NUMBER: _ClassVar[int]
    VISION_API_URL_FIELD_NUMBER: _ClassVar[int]
    VISION_PROFILE_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    vision_x_token: str
    vision_api_url: str
    vision_profile_id: str
    def __init__(
        self,
        session_id: _Optional[str] = ...,
        vision_x_token: _Optional[str] = ...,
        vision_api_url: _Optional[str] = ...,
        vision_profile_id: _Optional[str] = ...,
    ) -> None: ...

class GetSessionInfoRequest(_message.Message):
    __slots__ = ("session_id",)
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    def __init__(self, session_id: _Optional[str] = ...) -> None: ...

class GetSessionInfoResponse(_message.Message):
    __slots__ = ("session_id", "connected", "current_url", "pages", "connected_since")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    CONNECTED_FIELD_NUMBER: _ClassVar[int]
    CURRENT_URL_FIELD_NUMBER: _ClassVar[int]
    PAGES_FIELD_NUMBER: _ClassVar[int]
    CONNECTED_SINCE_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    connected: bool
    current_url: str
    pages: _containers.RepeatedCompositeFieldContainer[PageInfo]
    connected_since: int
    def __init__(
        self,
        session_id: _Optional[str] = ...,
        connected: bool = ...,
        current_url: _Optional[str] = ...,
        pages: _Optional[_Iterable[_Union[PageInfo, _Mapping]]] = ...,
        connected_since: _Optional[int] = ...,
    ) -> None: ...

class NavigateRequest(_message.Message):
    __slots__ = ("session_id", "page_id", "url", "wait_until")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PAGE_ID_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    WAIT_UNTIL_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    page_id: str
    url: str
    wait_until: str
    def __init__(
        self,
        session_id: _Optional[str] = ...,
        page_id: _Optional[str] = ...,
        url: _Optional[str] = ...,
        wait_until: _Optional[str] = ...,
    ) -> None: ...

class NavigateResponse(_message.Message):
    __slots__ = ("url",)
    URL_FIELD_NUMBER: _ClassVar[int]
    url: str
    def __init__(self, url: _Optional[str] = ...) -> None: ...

class StreamSessionStatusRequest(_message.Message):
    __slots__ = ("session_id",)
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    def __init__(self, session_id: _Optional[str] = ...) -> None: ...

class SessionStatusEvent(_message.Message):
    __slots__ = ("session_id", "status", "detail", "current_url", "timestamp")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    CURRENT_URL_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    status: str
    detail: str
    current_url: str
    timestamp: int
    def __init__(
        self,
        session_id: _Optional[str] = ...,
        status: _Optional[str] = ...,
        detail: _Optional[str] = ...,
        current_url: _Optional[str] = ...,
        timestamp: _Optional[int] = ...,
    ) -> None: ...
