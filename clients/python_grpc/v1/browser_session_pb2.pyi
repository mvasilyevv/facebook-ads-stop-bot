from collections.abc import Iterable as _Iterable
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar
from typing import Optional as _Optional
from typing import Union as _Union

from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from google.protobuf.internal import containers as _containers

DESCRIPTOR: _descriptor.FileDescriptor

class OpenCabinetTabsRequest(_message.Message):
    __slots__ = ("session_id", "ad_account_ids")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    AD_ACCOUNT_IDS_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    ad_account_ids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(
        self, session_id: _Optional[str] = ..., ad_account_ids: _Optional[_Iterable[str]] = ...
    ) -> None: ...

class OpenCabinetTabsResponse(_message.Message):
    __slots__ = ("results",)
    RESULTS_FIELD_NUMBER: _ClassVar[int]
    results: _containers.RepeatedCompositeFieldContainer[CabinetTabResult]
    def __init__(
        self, results: _Optional[_Iterable[_Union[CabinetTabResult, _Mapping]]] = ...
    ) -> None: ...

class CabinetTabResult(_message.Message):
    __slots__ = ("ad_account_id", "opened", "url", "error")
    AD_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    OPENED_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    ad_account_id: str
    opened: bool
    url: str
    error: str
    def __init__(
        self,
        ad_account_id: _Optional[str] = ...,
        opened: bool = ...,
        url: _Optional[str] = ...,
        error: _Optional[str] = ...,
    ) -> None: ...

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
    __slots__ = ("session_id", "profile", "initial_page_url")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PROFILE_FIELD_NUMBER: _ClassVar[int]
    INITIAL_PAGE_URL_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    profile: VisionProfile
    initial_page_url: str
    def __init__(
        self,
        session_id: _Optional[str] = ...,
        profile: _Optional[_Union[VisionProfile, _Mapping]] = ...,
        initial_page_url: _Optional[str] = ...,
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

class ReconnectBrowserRequest(_message.Message):
    __slots__ = ("session_id",)
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    def __init__(self, session_id: _Optional[str] = ...) -> None: ...

class RecoverBrowserProfileRequest(_message.Message):
    __slots__ = (
        "vision_x_token",
        "vision_api_url",
        "vision_profile_id",
        "vision_folder_id",
        "maintenance_owner",
        "capability_expires_at",
        "capability_nonce",
        "capability_signature",
    )
    VISION_X_TOKEN_FIELD_NUMBER: _ClassVar[int]
    VISION_API_URL_FIELD_NUMBER: _ClassVar[int]
    VISION_PROFILE_ID_FIELD_NUMBER: _ClassVar[int]
    VISION_FOLDER_ID_FIELD_NUMBER: _ClassVar[int]
    MAINTENANCE_OWNER_FIELD_NUMBER: _ClassVar[int]
    CAPABILITY_EXPIRES_AT_FIELD_NUMBER: _ClassVar[int]
    CAPABILITY_NONCE_FIELD_NUMBER: _ClassVar[int]
    CAPABILITY_SIGNATURE_FIELD_NUMBER: _ClassVar[int]
    vision_x_token: str
    vision_api_url: str
    vision_profile_id: str
    vision_folder_id: str
    maintenance_owner: str
    capability_expires_at: int
    capability_nonce: str
    capability_signature: str
    def __init__(
        self,
        vision_x_token: _Optional[str] = ...,
        vision_api_url: _Optional[str] = ...,
        vision_profile_id: _Optional[str] = ...,
        vision_folder_id: _Optional[str] = ...,
        maintenance_owner: _Optional[str] = ...,
        capability_expires_at: _Optional[int] = ...,
        capability_nonce: _Optional[str] = ...,
        capability_signature: _Optional[str] = ...,
    ) -> None: ...
