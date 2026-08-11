from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ListCampaignsRequest(_message.Message):
    __slots__ = ("session_id", "page_id", "owner_tag", "ad_account_id")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PAGE_ID_FIELD_NUMBER: _ClassVar[int]
    OWNER_TAG_FIELD_NUMBER: _ClassVar[int]
    AD_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    page_id: str
    owner_tag: str
    ad_account_id: str
    def __init__(self, session_id: _Optional[str] = ..., page_id: _Optional[str] = ..., owner_tag: _Optional[str] = ..., ad_account_id: _Optional[str] = ...) -> None: ...

class CampaignInfo(_message.Message):
    __slots__ = ("id", "name")
    ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    id: str
    name: str
    def __init__(self, id: _Optional[str] = ..., name: _Optional[str] = ...) -> None: ...

class ListCampaignsResponse(_message.Message):
    __slots__ = ("campaigns",)
    CAMPAIGNS_FIELD_NUMBER: _ClassVar[int]
    campaigns: _containers.RepeatedCompositeFieldContainer[CampaignInfo]
    def __init__(self, campaigns: _Optional[_Iterable[_Union[CampaignInfo, _Mapping]]] = ...) -> None: ...

class RunScanCycleRequest(_message.Message):
    __slots__ = ("session_id", "page_id", "max_scroll_passes", "do_refresh", "reset_scroll_first", "settle_delay_seconds", "campaign_ids", "owner_tag", "ad_account_id")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PAGE_ID_FIELD_NUMBER: _ClassVar[int]
    MAX_SCROLL_PASSES_FIELD_NUMBER: _ClassVar[int]
    DO_REFRESH_FIELD_NUMBER: _ClassVar[int]
    RESET_SCROLL_FIRST_FIELD_NUMBER: _ClassVar[int]
    SETTLE_DELAY_SECONDS_FIELD_NUMBER: _ClassVar[int]
    CAMPAIGN_IDS_FIELD_NUMBER: _ClassVar[int]
    OWNER_TAG_FIELD_NUMBER: _ClassVar[int]
    AD_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    page_id: str
    max_scroll_passes: int
    do_refresh: bool
    reset_scroll_first: bool
    settle_delay_seconds: float
    campaign_ids: _containers.RepeatedScalarFieldContainer[str]
    owner_tag: str
    ad_account_id: str
    def __init__(self, session_id: _Optional[str] = ..., page_id: _Optional[str] = ..., max_scroll_passes: _Optional[int] = ..., do_refresh: bool = ..., reset_scroll_first: bool = ..., settle_delay_seconds: _Optional[float] = ..., campaign_ids: _Optional[_Iterable[str]] = ..., owner_tag: _Optional[str] = ..., ad_account_id: _Optional[str] = ...) -> None: ...

class ScanCycleEvent(_message.Message):
    __slots__ = ("session_id", "progress", "complete", "error")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PROGRESS_FIELD_NUMBER: _ClassVar[int]
    COMPLETE_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    progress: ScanProgress
    complete: ScanComplete
    error: ScanError
    def __init__(self, session_id: _Optional[str] = ..., progress: _Optional[_Union[ScanProgress, _Mapping]] = ..., complete: _Optional[_Union[ScanComplete, _Mapping]] = ..., error: _Optional[_Union[ScanError, _Mapping]] = ...) -> None: ...

class ScanProgress(_message.Message):
    __slots__ = ("pass_number", "rows_so_far", "scroll_metrics", "new_rows")
    PASS_NUMBER_FIELD_NUMBER: _ClassVar[int]
    ROWS_SO_FAR_FIELD_NUMBER: _ClassVar[int]
    SCROLL_METRICS_FIELD_NUMBER: _ClassVar[int]
    NEW_ROWS_FIELD_NUMBER: _ClassVar[int]
    pass_number: int
    rows_so_far: int
    scroll_metrics: ScrollMetrics
    new_rows: _containers.RepeatedCompositeFieldContainer[ScannedAdRow]
    def __init__(self, pass_number: _Optional[int] = ..., rows_so_far: _Optional[int] = ..., scroll_metrics: _Optional[_Union[ScrollMetrics, _Mapping]] = ..., new_rows: _Optional[_Iterable[_Union[ScannedAdRow, _Mapping]]] = ...) -> None: ...

class ScanComplete(_message.Message):
    __slots__ = ("all_rows", "total_passes", "duration_seconds", "dismissed_modals", "unknown_modal_artifacts", "phase_timings", "partial_row_ids", "warnings", "empty_reason", "rows_with_all_metrics_empty", "metrics_contract_revision")
    ALL_ROWS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_PASSES_FIELD_NUMBER: _ClassVar[int]
    DURATION_SECONDS_FIELD_NUMBER: _ClassVar[int]
    DISMISSED_MODALS_FIELD_NUMBER: _ClassVar[int]
    UNKNOWN_MODAL_ARTIFACTS_FIELD_NUMBER: _ClassVar[int]
    PHASE_TIMINGS_FIELD_NUMBER: _ClassVar[int]
    PARTIAL_ROW_IDS_FIELD_NUMBER: _ClassVar[int]
    WARNINGS_FIELD_NUMBER: _ClassVar[int]
    EMPTY_REASON_FIELD_NUMBER: _ClassVar[int]
    ROWS_WITH_ALL_METRICS_EMPTY_FIELD_NUMBER: _ClassVar[int]
    METRICS_CONTRACT_REVISION_FIELD_NUMBER: _ClassVar[int]
    all_rows: _containers.RepeatedCompositeFieldContainer[ScannedAdRow]
    total_passes: int
    duration_seconds: float
    dismissed_modals: _containers.RepeatedScalarFieldContainer[str]
    unknown_modal_artifacts: _containers.RepeatedScalarFieldContainer[str]
    phase_timings: PhaseTimings
    partial_row_ids: _containers.RepeatedScalarFieldContainer[str]
    warnings: _containers.RepeatedScalarFieldContainer[str]
    empty_reason: str
    rows_with_all_metrics_empty: int
    metrics_contract_revision: int
    def __init__(self, all_rows: _Optional[_Iterable[_Union[ScannedAdRow, _Mapping]]] = ..., total_passes: _Optional[int] = ..., duration_seconds: _Optional[float] = ..., dismissed_modals: _Optional[_Iterable[str]] = ..., unknown_modal_artifacts: _Optional[_Iterable[str]] = ..., phase_timings: _Optional[_Union[PhaseTimings, _Mapping]] = ..., partial_row_ids: _Optional[_Iterable[str]] = ..., warnings: _Optional[_Iterable[str]] = ..., empty_reason: _Optional[str] = ..., rows_with_all_metrics_empty: _Optional[int] = ..., metrics_contract_revision: _Optional[int] = ...) -> None: ...

class PhaseTimings(_message.Message):
    __slots__ = ("refresh_ms", "first_row_ms", "scroll_ms", "parse_ms", "total_ms")
    REFRESH_MS_FIELD_NUMBER: _ClassVar[int]
    FIRST_ROW_MS_FIELD_NUMBER: _ClassVar[int]
    SCROLL_MS_FIELD_NUMBER: _ClassVar[int]
    PARSE_MS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_MS_FIELD_NUMBER: _ClassVar[int]
    refresh_ms: int
    first_row_ms: int
    scroll_ms: int
    parse_ms: int
    total_ms: int
    def __init__(self, refresh_ms: _Optional[int] = ..., first_row_ms: _Optional[int] = ..., scroll_ms: _Optional[int] = ..., parse_ms: _Optional[int] = ..., total_ms: _Optional[int] = ...) -> None: ...

class ScanError(_message.Message):
    __slots__ = ("message", "recoverable", "attempt")
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    RECOVERABLE_FIELD_NUMBER: _ClassVar[int]
    ATTEMPT_FIELD_NUMBER: _ClassVar[int]
    message: str
    recoverable: bool
    attempt: int
    def __init__(self, message: _Optional[str] = ..., recoverable: bool = ..., attempt: _Optional[int] = ...) -> None: ...

class ScrollMetrics(_message.Message):
    __slots__ = ("found", "scroll_top", "max_scroll_top", "at_bottom")
    FOUND_FIELD_NUMBER: _ClassVar[int]
    SCROLL_TOP_FIELD_NUMBER: _ClassVar[int]
    MAX_SCROLL_TOP_FIELD_NUMBER: _ClassVar[int]
    AT_BOTTOM_FIELD_NUMBER: _ClassVar[int]
    found: bool
    scroll_top: float
    max_scroll_top: float
    at_bottom: bool
    def __init__(self, found: bool = ..., scroll_top: _Optional[float] = ..., max_scroll_top: _Optional[float] = ..., at_bottom: bool = ...) -> None: ...

class ScannedAdRow(_message.Message):
    __slots__ = ("fb_ad_id", "campaign_name", "adset_name", "ad_name", "delivery_status", "spend", "budget", "reach", "impressions", "clicks", "cpc", "ctr", "outbound_clicks", "outbound_ctr", "landing_page_views", "cost_per_landing_page_view", "cost_per_result", "cpm", "frequency", "leads", "cost_per_lead", "registrations", "cost_per_registration", "deposits", "resolved_offer_code", "campaign_id", "creative_thumb_url", "creative_image_url", "adset_pixel_id", "adset_daily_budget", "adset_lifetime_budget", "adset_budget_remaining", "adset_learning_stage", "adset_id")
    FB_AD_ID_FIELD_NUMBER: _ClassVar[int]
    CAMPAIGN_NAME_FIELD_NUMBER: _ClassVar[int]
    ADSET_NAME_FIELD_NUMBER: _ClassVar[int]
    AD_NAME_FIELD_NUMBER: _ClassVar[int]
    DELIVERY_STATUS_FIELD_NUMBER: _ClassVar[int]
    SPEND_FIELD_NUMBER: _ClassVar[int]
    BUDGET_FIELD_NUMBER: _ClassVar[int]
    REACH_FIELD_NUMBER: _ClassVar[int]
    IMPRESSIONS_FIELD_NUMBER: _ClassVar[int]
    CLICKS_FIELD_NUMBER: _ClassVar[int]
    CPC_FIELD_NUMBER: _ClassVar[int]
    CTR_FIELD_NUMBER: _ClassVar[int]
    OUTBOUND_CLICKS_FIELD_NUMBER: _ClassVar[int]
    OUTBOUND_CTR_FIELD_NUMBER: _ClassVar[int]
    LANDING_PAGE_VIEWS_FIELD_NUMBER: _ClassVar[int]
    COST_PER_LANDING_PAGE_VIEW_FIELD_NUMBER: _ClassVar[int]
    COST_PER_RESULT_FIELD_NUMBER: _ClassVar[int]
    CPM_FIELD_NUMBER: _ClassVar[int]
    FREQUENCY_FIELD_NUMBER: _ClassVar[int]
    LEADS_FIELD_NUMBER: _ClassVar[int]
    COST_PER_LEAD_FIELD_NUMBER: _ClassVar[int]
    REGISTRATIONS_FIELD_NUMBER: _ClassVar[int]
    COST_PER_REGISTRATION_FIELD_NUMBER: _ClassVar[int]
    DEPOSITS_FIELD_NUMBER: _ClassVar[int]
    RESOLVED_OFFER_CODE_FIELD_NUMBER: _ClassVar[int]
    CAMPAIGN_ID_FIELD_NUMBER: _ClassVar[int]
    CREATIVE_THUMB_URL_FIELD_NUMBER: _ClassVar[int]
    CREATIVE_IMAGE_URL_FIELD_NUMBER: _ClassVar[int]
    ADSET_PIXEL_ID_FIELD_NUMBER: _ClassVar[int]
    ADSET_DAILY_BUDGET_FIELD_NUMBER: _ClassVar[int]
    ADSET_LIFETIME_BUDGET_FIELD_NUMBER: _ClassVar[int]
    ADSET_BUDGET_REMAINING_FIELD_NUMBER: _ClassVar[int]
    ADSET_LEARNING_STAGE_FIELD_NUMBER: _ClassVar[int]
    ADSET_ID_FIELD_NUMBER: _ClassVar[int]
    fb_ad_id: str
    campaign_name: str
    adset_name: str
    ad_name: str
    delivery_status: str
    spend: str
    budget: str
    reach: int
    impressions: int
    clicks: int
    cpc: str
    ctr: str
    outbound_clicks: int
    outbound_ctr: str
    landing_page_views: int
    cost_per_landing_page_view: str
    cost_per_result: str
    cpm: str
    frequency: str
    leads: int
    cost_per_lead: str
    registrations: int
    cost_per_registration: str
    deposits: int
    resolved_offer_code: str
    campaign_id: str
    creative_thumb_url: str
    creative_image_url: str
    adset_pixel_id: str
    adset_daily_budget: str
    adset_lifetime_budget: str
    adset_budget_remaining: str
    adset_learning_stage: str
    adset_id: str
    def __init__(self, fb_ad_id: _Optional[str] = ..., campaign_name: _Optional[str] = ..., adset_name: _Optional[str] = ..., ad_name: _Optional[str] = ..., delivery_status: _Optional[str] = ..., spend: _Optional[str] = ..., budget: _Optional[str] = ..., reach: _Optional[int] = ..., impressions: _Optional[int] = ..., clicks: _Optional[int] = ..., cpc: _Optional[str] = ..., ctr: _Optional[str] = ..., outbound_clicks: _Optional[int] = ..., outbound_ctr: _Optional[str] = ..., landing_page_views: _Optional[int] = ..., cost_per_landing_page_view: _Optional[str] = ..., cost_per_result: _Optional[str] = ..., cpm: _Optional[str] = ..., frequency: _Optional[str] = ..., leads: _Optional[int] = ..., cost_per_lead: _Optional[str] = ..., registrations: _Optional[int] = ..., cost_per_registration: _Optional[str] = ..., deposits: _Optional[int] = ..., resolved_offer_code: _Optional[str] = ..., campaign_id: _Optional[str] = ..., creative_thumb_url: _Optional[str] = ..., creative_image_url: _Optional[str] = ..., adset_pixel_id: _Optional[str] = ..., adset_daily_budget: _Optional[str] = ..., adset_lifetime_budget: _Optional[str] = ..., adset_budget_remaining: _Optional[str] = ..., adset_learning_stage: _Optional[str] = ..., adset_id: _Optional[str] = ...) -> None: ...
