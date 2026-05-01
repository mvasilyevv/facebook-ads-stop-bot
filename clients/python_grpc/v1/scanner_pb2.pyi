from collections.abc import Iterable as _Iterable
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar
from typing import Optional as _Optional
from typing import Union as _Union

from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from google.protobuf.internal import containers as _containers

DESCRIPTOR: _descriptor.FileDescriptor

class RunScanCycleRequest(_message.Message):
    __slots__ = (
        "session_id",
        "page_id",
        "max_scroll_passes",
        "do_refresh",
        "reset_scroll_first",
        "settle_delay_seconds",
    )
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PAGE_ID_FIELD_NUMBER: _ClassVar[int]
    MAX_SCROLL_PASSES_FIELD_NUMBER: _ClassVar[int]
    DO_REFRESH_FIELD_NUMBER: _ClassVar[int]
    RESET_SCROLL_FIRST_FIELD_NUMBER: _ClassVar[int]
    SETTLE_DELAY_SECONDS_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    page_id: str
    max_scroll_passes: int
    do_refresh: bool
    reset_scroll_first: bool
    settle_delay_seconds: float
    def __init__(
        self,
        session_id: _Optional[str] = ...,
        page_id: _Optional[str] = ...,
        max_scroll_passes: _Optional[int] = ...,
        do_refresh: bool = ...,
        reset_scroll_first: bool = ...,
        settle_delay_seconds: _Optional[float] = ...,
    ) -> None: ...

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
    def __init__(
        self,
        session_id: _Optional[str] = ...,
        progress: _Optional[_Union[ScanProgress, _Mapping]] = ...,
        complete: _Optional[_Union[ScanComplete, _Mapping]] = ...,
        error: _Optional[_Union[ScanError, _Mapping]] = ...,
    ) -> None: ...

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
    def __init__(
        self,
        pass_number: _Optional[int] = ...,
        rows_so_far: _Optional[int] = ...,
        scroll_metrics: _Optional[_Union[ScrollMetrics, _Mapping]] = ...,
        new_rows: _Optional[_Iterable[_Union[ScannedAdRow, _Mapping]]] = ...,
    ) -> None: ...

class ScanComplete(_message.Message):
    __slots__ = (
        "all_rows",
        "total_passes",
        "duration_seconds",
        "dismissed_modals",
        "unknown_modal_artifacts",
    )
    ALL_ROWS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_PASSES_FIELD_NUMBER: _ClassVar[int]
    DURATION_SECONDS_FIELD_NUMBER: _ClassVar[int]
    DISMISSED_MODALS_FIELD_NUMBER: _ClassVar[int]
    UNKNOWN_MODAL_ARTIFACTS_FIELD_NUMBER: _ClassVar[int]
    all_rows: _containers.RepeatedCompositeFieldContainer[ScannedAdRow]
    total_passes: int
    duration_seconds: float
    dismissed_modals: _containers.RepeatedScalarFieldContainer[str]
    unknown_modal_artifacts: _containers.RepeatedScalarFieldContainer[str]
    def __init__(
        self,
        all_rows: _Optional[_Iterable[_Union[ScannedAdRow, _Mapping]]] = ...,
        total_passes: _Optional[int] = ...,
        duration_seconds: _Optional[float] = ...,
        dismissed_modals: _Optional[_Iterable[str]] = ...,
        unknown_modal_artifacts: _Optional[_Iterable[str]] = ...,
    ) -> None: ...

class ScanError(_message.Message):
    __slots__ = ("message", "recoverable", "attempt")
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    RECOVERABLE_FIELD_NUMBER: _ClassVar[int]
    ATTEMPT_FIELD_NUMBER: _ClassVar[int]
    message: str
    recoverable: bool
    attempt: int
    def __init__(
        self, message: _Optional[str] = ..., recoverable: bool = ..., attempt: _Optional[int] = ...
    ) -> None: ...

class RefreshTableRequest(_message.Message):
    __slots__ = ("session_id", "page_id")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PAGE_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    page_id: str
    def __init__(self, session_id: _Optional[str] = ..., page_id: _Optional[str] = ...) -> None: ...

class RefreshTableResponse(_message.Message):
    __slots__ = ("refreshed", "fallback_reload")
    REFRESHED_FIELD_NUMBER: _ClassVar[int]
    FALLBACK_RELOAD_FIELD_NUMBER: _ClassVar[int]
    refreshed: bool
    fallback_reload: bool
    def __init__(self, refreshed: bool = ..., fallback_reload: bool = ...) -> None: ...

class ParseVisibleRowsRequest(_message.Message):
    __slots__ = ("session_id", "page_id")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PAGE_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    page_id: str
    def __init__(self, session_id: _Optional[str] = ..., page_id: _Optional[str] = ...) -> None: ...

class ParseVisibleRowsResponse(_message.Message):
    __slots__ = ("rows",)
    ROWS_FIELD_NUMBER: _ClassVar[int]
    rows: _containers.RepeatedCompositeFieldContainer[ScannedAdRow]
    def __init__(
        self, rows: _Optional[_Iterable[_Union[ScannedAdRow, _Mapping]]] = ...
    ) -> None: ...

class ScrollAndParseRequest(_message.Message):
    __slots__ = (
        "session_id",
        "page_id",
        "scroll_amount",
        "wait_for_stable",
        "stable_timeout_seconds",
    )
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PAGE_ID_FIELD_NUMBER: _ClassVar[int]
    SCROLL_AMOUNT_FIELD_NUMBER: _ClassVar[int]
    WAIT_FOR_STABLE_FIELD_NUMBER: _ClassVar[int]
    STABLE_TIMEOUT_SECONDS_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    page_id: str
    scroll_amount: int
    wait_for_stable: bool
    stable_timeout_seconds: float
    def __init__(
        self,
        session_id: _Optional[str] = ...,
        page_id: _Optional[str] = ...,
        scroll_amount: _Optional[int] = ...,
        wait_for_stable: bool = ...,
        stable_timeout_seconds: _Optional[float] = ...,
    ) -> None: ...

class ScrollAndParseResponse(_message.Message):
    __slots__ = ("new_rows", "scroll_metrics", "at_bottom")
    NEW_ROWS_FIELD_NUMBER: _ClassVar[int]
    SCROLL_METRICS_FIELD_NUMBER: _ClassVar[int]
    AT_BOTTOM_FIELD_NUMBER: _ClassVar[int]
    new_rows: _containers.RepeatedCompositeFieldContainer[ScannedAdRow]
    scroll_metrics: ScrollMetrics
    at_bottom: bool
    def __init__(
        self,
        new_rows: _Optional[_Iterable[_Union[ScannedAdRow, _Mapping]]] = ...,
        scroll_metrics: _Optional[_Union[ScrollMetrics, _Mapping]] = ...,
        at_bottom: bool = ...,
    ) -> None: ...

class WaitForDomStableRequest(_message.Message):
    __slots__ = ("session_id", "page_id", "timeout_seconds", "poll_interval_seconds")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PAGE_ID_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_SECONDS_FIELD_NUMBER: _ClassVar[int]
    POLL_INTERVAL_SECONDS_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    page_id: str
    timeout_seconds: float
    poll_interval_seconds: float
    def __init__(
        self,
        session_id: _Optional[str] = ...,
        page_id: _Optional[str] = ...,
        timeout_seconds: _Optional[float] = ...,
        poll_interval_seconds: _Optional[float] = ...,
    ) -> None: ...

class WaitForDomStableResponse(_message.Message):
    __slots__ = ("stabilized", "final_row_count")
    STABILIZED_FIELD_NUMBER: _ClassVar[int]
    FINAL_ROW_COUNT_FIELD_NUMBER: _ClassVar[int]
    stabilized: bool
    final_row_count: int
    def __init__(self, stabilized: bool = ..., final_row_count: _Optional[int] = ...) -> None: ...

class ResetScrollRequest(_message.Message):
    __slots__ = ("session_id", "page_id")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PAGE_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    page_id: str
    def __init__(self, session_id: _Optional[str] = ..., page_id: _Optional[str] = ...) -> None: ...

class ResetScrollResponse(_message.Message):
    __slots__ = ("containers_reset",)
    CONTAINERS_RESET_FIELD_NUMBER: _ClassVar[int]
    containers_reset: int
    def __init__(self, containers_reset: _Optional[int] = ...) -> None: ...

class GetScrollMetricsRequest(_message.Message):
    __slots__ = ("session_id", "page_id")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PAGE_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    page_id: str
    def __init__(self, session_id: _Optional[str] = ..., page_id: _Optional[str] = ...) -> None: ...

class GetScrollMetricsResponse(_message.Message):
    __slots__ = ("metrics",)
    METRICS_FIELD_NUMBER: _ClassVar[int]
    metrics: ScrollMetrics
    def __init__(self, metrics: _Optional[_Union[ScrollMetrics, _Mapping]] = ...) -> None: ...

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
    def __init__(
        self,
        found: bool = ...,
        scroll_top: _Optional[float] = ...,
        max_scroll_top: _Optional[float] = ...,
        at_bottom: bool = ...,
    ) -> None: ...

class GetVisibleRowIdsRequest(_message.Message):
    __slots__ = ("session_id", "page_id")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PAGE_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    page_id: str
    def __init__(self, session_id: _Optional[str] = ..., page_id: _Optional[str] = ...) -> None: ...

class GetVisibleRowIdsResponse(_message.Message):
    __slots__ = ("row_ids",)
    ROW_IDS_FIELD_NUMBER: _ClassVar[int]
    row_ids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, row_ids: _Optional[_Iterable[str]] = ...) -> None: ...

class FindToggleCellRequest(_message.Message):
    __slots__ = ("session_id", "page_id", "fb_ad_id", "max_scroll_passes", "reset_to_top")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PAGE_ID_FIELD_NUMBER: _ClassVar[int]
    FB_AD_ID_FIELD_NUMBER: _ClassVar[int]
    MAX_SCROLL_PASSES_FIELD_NUMBER: _ClassVar[int]
    RESET_TO_TOP_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    page_id: str
    fb_ad_id: str
    max_scroll_passes: int
    reset_to_top: bool
    def __init__(
        self,
        session_id: _Optional[str] = ...,
        page_id: _Optional[str] = ...,
        fb_ad_id: _Optional[str] = ...,
        max_scroll_passes: _Optional[int] = ...,
        reset_to_top: bool = ...,
    ) -> None: ...

class FindToggleCellResponse(_message.Message):
    __slots__ = ("found", "cell_x", "cell_y", "aria_checked")
    FOUND_FIELD_NUMBER: _ClassVar[int]
    CELL_X_FIELD_NUMBER: _ClassVar[int]
    CELL_Y_FIELD_NUMBER: _ClassVar[int]
    ARIA_CHECKED_FIELD_NUMBER: _ClassVar[int]
    found: bool
    cell_x: float
    cell_y: float
    aria_checked: str
    def __init__(
        self,
        found: bool = ...,
        cell_x: _Optional[float] = ...,
        cell_y: _Optional[float] = ...,
        aria_checked: _Optional[str] = ...,
    ) -> None: ...

class ReadToggleStateRequest(_message.Message):
    __slots__ = ("session_id", "page_id", "fb_ad_id")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PAGE_ID_FIELD_NUMBER: _ClassVar[int]
    FB_AD_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    page_id: str
    fb_ad_id: str
    def __init__(
        self,
        session_id: _Optional[str] = ...,
        page_id: _Optional[str] = ...,
        fb_ad_id: _Optional[str] = ...,
    ) -> None: ...

class ReadToggleStateResponse(_message.Message):
    __slots__ = ("found", "aria_checked")
    FOUND_FIELD_NUMBER: _ClassVar[int]
    ARIA_CHECKED_FIELD_NUMBER: _ClassVar[int]
    found: bool
    aria_checked: str
    def __init__(self, found: bool = ..., aria_checked: _Optional[str] = ...) -> None: ...

class ToggleAdRequest(_message.Message):
    __slots__ = ("session_id", "page_id", "fb_ad_id", "target_state")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PAGE_ID_FIELD_NUMBER: _ClassVar[int]
    FB_AD_ID_FIELD_NUMBER: _ClassVar[int]
    TARGET_STATE_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    page_id: str
    fb_ad_id: str
    target_state: bool
    def __init__(
        self,
        session_id: _Optional[str] = ...,
        page_id: _Optional[str] = ...,
        fb_ad_id: _Optional[str] = ...,
        target_state: bool = ...,
    ) -> None: ...

class ToggleAdResponse(_message.Message):
    __slots__ = ("success", "final_state")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    FINAL_STATE_FIELD_NUMBER: _ClassVar[int]
    success: bool
    final_state: str
    def __init__(self, success: bool = ..., final_state: _Optional[str] = ...) -> None: ...

class HumanMoveRequest(_message.Message):
    __slots__ = ("session_id", "page_id", "target_x", "target_y", "profile")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PAGE_ID_FIELD_NUMBER: _ClassVar[int]
    TARGET_X_FIELD_NUMBER: _ClassVar[int]
    TARGET_Y_FIELD_NUMBER: _ClassVar[int]
    PROFILE_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    page_id: str
    target_x: float
    target_y: float
    profile: HumanProfile
    def __init__(
        self,
        session_id: _Optional[str] = ...,
        page_id: _Optional[str] = ...,
        target_x: _Optional[float] = ...,
        target_y: _Optional[float] = ...,
        profile: _Optional[_Union[HumanProfile, _Mapping]] = ...,
    ) -> None: ...

class HumanMoveResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class HumanClickRequest(_message.Message):
    __slots__ = ("session_id", "page_id", "x", "y", "double_check_pause", "profile")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PAGE_ID_FIELD_NUMBER: _ClassVar[int]
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    DOUBLE_CHECK_PAUSE_FIELD_NUMBER: _ClassVar[int]
    PROFILE_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    page_id: str
    x: float
    y: float
    double_check_pause: bool
    profile: HumanProfile
    def __init__(
        self,
        session_id: _Optional[str] = ...,
        page_id: _Optional[str] = ...,
        x: _Optional[float] = ...,
        y: _Optional[float] = ...,
        double_check_pause: bool = ...,
        profile: _Optional[_Union[HumanProfile, _Mapping]] = ...,
    ) -> None: ...

class HumanClickResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class HumanWheelScrollRequest(_message.Message):
    __slots__ = ("session_id", "page_id", "delta_y", "anchor_x", "anchor_y", "profile")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PAGE_ID_FIELD_NUMBER: _ClassVar[int]
    DELTA_Y_FIELD_NUMBER: _ClassVar[int]
    ANCHOR_X_FIELD_NUMBER: _ClassVar[int]
    ANCHOR_Y_FIELD_NUMBER: _ClassVar[int]
    PROFILE_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    page_id: str
    delta_y: int
    anchor_x: float
    anchor_y: float
    profile: HumanProfile
    def __init__(
        self,
        session_id: _Optional[str] = ...,
        page_id: _Optional[str] = ...,
        delta_y: _Optional[int] = ...,
        anchor_x: _Optional[float] = ...,
        anchor_y: _Optional[float] = ...,
        profile: _Optional[_Union[HumanProfile, _Mapping]] = ...,
    ) -> None: ...

class HumanWheelScrollResponse(_message.Message):
    __slots__ = ("final_x", "final_y")
    FINAL_X_FIELD_NUMBER: _ClassVar[int]
    FINAL_Y_FIELD_NUMBER: _ClassVar[int]
    final_x: float
    final_y: float
    def __init__(
        self, final_x: _Optional[float] = ..., final_y: _Optional[float] = ...
    ) -> None: ...

class WaitForToggleConfirmationRequest(_message.Message):
    __slots__ = (
        "session_id",
        "page_id",
        "fb_ad_id",
        "expected_checked",
        "required_reads",
        "poll_delays_seconds",
        "max_scroll_passes_restore",
    )
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PAGE_ID_FIELD_NUMBER: _ClassVar[int]
    FB_AD_ID_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_CHECKED_FIELD_NUMBER: _ClassVar[int]
    REQUIRED_READS_FIELD_NUMBER: _ClassVar[int]
    POLL_DELAYS_SECONDS_FIELD_NUMBER: _ClassVar[int]
    MAX_SCROLL_PASSES_RESTORE_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    page_id: str
    fb_ad_id: str
    expected_checked: str
    required_reads: int
    poll_delays_seconds: _containers.RepeatedScalarFieldContainer[float]
    max_scroll_passes_restore: int
    def __init__(
        self,
        session_id: _Optional[str] = ...,
        page_id: _Optional[str] = ...,
        fb_ad_id: _Optional[str] = ...,
        expected_checked: _Optional[str] = ...,
        required_reads: _Optional[int] = ...,
        poll_delays_seconds: _Optional[_Iterable[float]] = ...,
        max_scroll_passes_restore: _Optional[int] = ...,
    ) -> None: ...

class WaitForToggleConfirmationResponse(_message.Message):
    __slots__ = ("success", "message", "final_aria_checked", "reads_matched")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    FINAL_ARIA_CHECKED_FIELD_NUMBER: _ClassVar[int]
    READS_MATCHED_FIELD_NUMBER: _ClassVar[int]
    success: bool
    message: str
    final_aria_checked: str
    reads_matched: int
    def __init__(
        self,
        success: bool = ...,
        message: _Optional[str] = ...,
        final_aria_checked: _Optional[str] = ...,
        reads_matched: _Optional[int] = ...,
    ) -> None: ...

class ValidateColumnsRequest(_message.Message):
    __slots__ = ("session_id", "page_id")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PAGE_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    page_id: str
    def __init__(self, session_id: _Optional[str] = ..., page_id: _Optional[str] = ...) -> None: ...

class ValidateColumnsResponse(_message.Message):
    __slots__ = ("valid", "missing_columns", "found_columns", "error_message")
    VALID_FIELD_NUMBER: _ClassVar[int]
    MISSING_COLUMNS_FIELD_NUMBER: _ClassVar[int]
    FOUND_COLUMNS_FIELD_NUMBER: _ClassVar[int]
    ERROR_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    valid: bool
    missing_columns: _containers.RepeatedScalarFieldContainer[str]
    found_columns: _containers.RepeatedScalarFieldContainer[str]
    error_message: str
    def __init__(
        self,
        valid: bool = ...,
        missing_columns: _Optional[_Iterable[str]] = ...,
        found_columns: _Optional[_Iterable[str]] = ...,
        error_message: _Optional[str] = ...,
    ) -> None: ...

class ColumnWidth(_message.Message):
    __slots__ = ("key", "title", "surface_key", "width_px", "text_needles")
    KEY_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    SURFACE_KEY_FIELD_NUMBER: _ClassVar[int]
    WIDTH_PX_FIELD_NUMBER: _ClassVar[int]
    TEXT_NEEDLES_FIELD_NUMBER: _ClassVar[int]
    key: str
    title: str
    surface_key: str
    width_px: int
    text_needles: _containers.RepeatedScalarFieldContainer[str]
    def __init__(
        self,
        key: _Optional[str] = ...,
        title: _Optional[str] = ...,
        surface_key: _Optional[str] = ...,
        width_px: _Optional[int] = ...,
        text_needles: _Optional[_Iterable[str]] = ...,
    ) -> None: ...

class CaptureColumnWidthsRequest(_message.Message):
    __slots__ = ("session_id", "page_id")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PAGE_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    page_id: str
    def __init__(self, session_id: _Optional[str] = ..., page_id: _Optional[str] = ...) -> None: ...

class CaptureColumnWidthsResponse(_message.Message):
    __slots__ = ("captured", "column_widths", "matched_columns", "error_message", "total_width_px")
    CAPTURED_FIELD_NUMBER: _ClassVar[int]
    COLUMN_WIDTHS_FIELD_NUMBER: _ClassVar[int]
    MATCHED_COLUMNS_FIELD_NUMBER: _ClassVar[int]
    ERROR_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    TOTAL_WIDTH_PX_FIELD_NUMBER: _ClassVar[int]
    captured: bool
    column_widths: _containers.RepeatedCompositeFieldContainer[ColumnWidth]
    matched_columns: _containers.RepeatedScalarFieldContainer[str]
    error_message: str
    total_width_px: int
    def __init__(
        self,
        captured: bool = ...,
        column_widths: _Optional[_Iterable[_Union[ColumnWidth, _Mapping]]] = ...,
        matched_columns: _Optional[_Iterable[str]] = ...,
        error_message: _Optional[str] = ...,
        total_width_px: _Optional[int] = ...,
    ) -> None: ...

class ApplyColumnWidthsRequest(_message.Message):
    __slots__ = ("session_id", "page_id", "column_widths")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PAGE_ID_FIELD_NUMBER: _ClassVar[int]
    COLUMN_WIDTHS_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    page_id: str
    column_widths: _containers.RepeatedCompositeFieldContainer[ColumnWidth]
    def __init__(
        self,
        session_id: _Optional[str] = ...,
        page_id: _Optional[str] = ...,
        column_widths: _Optional[_Iterable[_Union[ColumnWidth, _Mapping]]] = ...,
    ) -> None: ...

class ApplyColumnWidthsResponse(_message.Message):
    __slots__ = (
        "applied",
        "matched_columns",
        "missing_columns",
        "error_message",
        "adjusted_cells",
        "total_width_px",
    )
    APPLIED_FIELD_NUMBER: _ClassVar[int]
    MATCHED_COLUMNS_FIELD_NUMBER: _ClassVar[int]
    MISSING_COLUMNS_FIELD_NUMBER: _ClassVar[int]
    ERROR_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    ADJUSTED_CELLS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_WIDTH_PX_FIELD_NUMBER: _ClassVar[int]
    applied: bool
    matched_columns: _containers.RepeatedScalarFieldContainer[str]
    missing_columns: _containers.RepeatedScalarFieldContainer[str]
    error_message: str
    adjusted_cells: int
    total_width_px: int
    def __init__(
        self,
        applied: bool = ...,
        matched_columns: _Optional[_Iterable[str]] = ...,
        missing_columns: _Optional[_Iterable[str]] = ...,
        error_message: _Optional[str] = ...,
        adjusted_cells: _Optional[int] = ...,
        total_width_px: _Optional[int] = ...,
    ) -> None: ...

class ScannedAdRow(_message.Message):
    __slots__ = (
        "fb_ad_id",
        "campaign_name",
        "adset_name",
        "ad_name",
        "delivery_status",
        "spend",
        "budget",
        "reach",
        "impressions",
        "clicks",
        "cpc",
        "ctr",
        "outbound_clicks",
        "outbound_ctr",
        "landing_page_views",
        "cost_per_landing_page_view",
        "cost_per_result",
        "cpm",
        "frequency",
        "leads",
        "cost_per_lead",
        "registrations",
        "cost_per_registration",
        "deposits",
        "resolved_offer_code",
    )
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
    def __init__(
        self,
        fb_ad_id: _Optional[str] = ...,
        campaign_name: _Optional[str] = ...,
        adset_name: _Optional[str] = ...,
        ad_name: _Optional[str] = ...,
        delivery_status: _Optional[str] = ...,
        spend: _Optional[str] = ...,
        budget: _Optional[str] = ...,
        reach: _Optional[int] = ...,
        impressions: _Optional[int] = ...,
        clicks: _Optional[int] = ...,
        cpc: _Optional[str] = ...,
        ctr: _Optional[str] = ...,
        outbound_clicks: _Optional[int] = ...,
        outbound_ctr: _Optional[str] = ...,
        landing_page_views: _Optional[int] = ...,
        cost_per_landing_page_view: _Optional[str] = ...,
        cost_per_result: _Optional[str] = ...,
        cpm: _Optional[str] = ...,
        frequency: _Optional[str] = ...,
        leads: _Optional[int] = ...,
        cost_per_lead: _Optional[str] = ...,
        registrations: _Optional[int] = ...,
        cost_per_registration: _Optional[str] = ...,
        deposits: _Optional[int] = ...,
        resolved_offer_code: _Optional[str] = ...,
    ) -> None: ...

class HumanProfile(_message.Message):
    __slots__ = (
        "speed_factor",
        "jitter_factor",
        "pause_factor",
        "overshoot_chance",
        "idle_chance",
        "idle_duration_min",
        "idle_duration_max",
        "bezier_steps_min",
        "bezier_steps_max",
    )
    SPEED_FACTOR_FIELD_NUMBER: _ClassVar[int]
    JITTER_FACTOR_FIELD_NUMBER: _ClassVar[int]
    PAUSE_FACTOR_FIELD_NUMBER: _ClassVar[int]
    OVERSHOOT_CHANCE_FIELD_NUMBER: _ClassVar[int]
    IDLE_CHANCE_FIELD_NUMBER: _ClassVar[int]
    IDLE_DURATION_MIN_FIELD_NUMBER: _ClassVar[int]
    IDLE_DURATION_MAX_FIELD_NUMBER: _ClassVar[int]
    BEZIER_STEPS_MIN_FIELD_NUMBER: _ClassVar[int]
    BEZIER_STEPS_MAX_FIELD_NUMBER: _ClassVar[int]
    speed_factor: float
    jitter_factor: float
    pause_factor: float
    overshoot_chance: float
    idle_chance: float
    idle_duration_min: float
    idle_duration_max: float
    bezier_steps_min: int
    bezier_steps_max: int
    def __init__(
        self,
        speed_factor: _Optional[float] = ...,
        jitter_factor: _Optional[float] = ...,
        pause_factor: _Optional[float] = ...,
        overshoot_chance: _Optional[float] = ...,
        idle_chance: _Optional[float] = ...,
        idle_duration_min: _Optional[float] = ...,
        idle_duration_max: _Optional[float] = ...,
        bezier_steps_min: _Optional[int] = ...,
        bezier_steps_max: _Optional[int] = ...,
    ) -> None: ...
