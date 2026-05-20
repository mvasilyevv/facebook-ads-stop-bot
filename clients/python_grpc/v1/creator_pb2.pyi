from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar
from typing import Optional as _Optional
from typing import Union as _Union

from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message

DESCRIPTOR: _descriptor.FileDescriptor

class RunPlanRequest(_message.Message):
    __slots__ = ("session_id", "page_id", "plan_json", "variables_json")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PAGE_ID_FIELD_NUMBER: _ClassVar[int]
    PLAN_JSON_FIELD_NUMBER: _ClassVar[int]
    VARIABLES_JSON_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    page_id: str
    plan_json: str
    variables_json: str
    def __init__(
        self,
        session_id: _Optional[str] = ...,
        page_id: _Optional[str] = ...,
        plan_json: _Optional[str] = ...,
        variables_json: _Optional[str] = ...,
    ) -> None: ...

class PlanEvent(_message.Message):
    __slots__ = ("started", "finished", "failed", "skipped", "complete", "checkpoint")
    STARTED_FIELD_NUMBER: _ClassVar[int]
    FINISHED_FIELD_NUMBER: _ClassVar[int]
    FAILED_FIELD_NUMBER: _ClassVar[int]
    SKIPPED_FIELD_NUMBER: _ClassVar[int]
    COMPLETE_FIELD_NUMBER: _ClassVar[int]
    CHECKPOINT_FIELD_NUMBER: _ClassVar[int]
    started: StepStarted
    finished: StepFinished
    failed: StepFailed
    skipped: StepSkipped
    complete: PlanComplete
    checkpoint: CheckpointDetected
    def __init__(
        self,
        started: _Optional[_Union[StepStarted, _Mapping]] = ...,
        finished: _Optional[_Union[StepFinished, _Mapping]] = ...,
        failed: _Optional[_Union[StepFailed, _Mapping]] = ...,
        skipped: _Optional[_Union[StepSkipped, _Mapping]] = ...,
        complete: _Optional[_Union[PlanComplete, _Mapping]] = ...,
        checkpoint: _Optional[_Union[CheckpointDetected, _Mapping]] = ...,
    ) -> None: ...

class StepStarted(_message.Message):
    __slots__ = ("step", "index", "timestamp_ms")
    STEP_FIELD_NUMBER: _ClassVar[int]
    INDEX_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_MS_FIELD_NUMBER: _ClassVar[int]
    step: str
    index: int
    timestamp_ms: int
    def __init__(
        self,
        step: _Optional[str] = ...,
        index: _Optional[int] = ...,
        timestamp_ms: _Optional[int] = ...,
    ) -> None: ...

class StepFinished(_message.Message):
    __slots__ = ("step", "index", "timestamp_ms", "detail_json")
    STEP_FIELD_NUMBER: _ClassVar[int]
    INDEX_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_MS_FIELD_NUMBER: _ClassVar[int]
    DETAIL_JSON_FIELD_NUMBER: _ClassVar[int]
    step: str
    index: int
    timestamp_ms: int
    detail_json: str
    def __init__(
        self,
        step: _Optional[str] = ...,
        index: _Optional[int] = ...,
        timestamp_ms: _Optional[int] = ...,
        detail_json: _Optional[str] = ...,
    ) -> None: ...

class StepFailed(_message.Message):
    __slots__ = ("step", "index", "error", "timestamp_ms")
    STEP_FIELD_NUMBER: _ClassVar[int]
    INDEX_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_MS_FIELD_NUMBER: _ClassVar[int]
    step: str
    index: int
    error: str
    timestamp_ms: int
    def __init__(
        self,
        step: _Optional[str] = ...,
        index: _Optional[int] = ...,
        error: _Optional[str] = ...,
        timestamp_ms: _Optional[int] = ...,
    ) -> None: ...

class StepSkipped(_message.Message):
    __slots__ = ("step", "index", "reason", "timestamp_ms")
    STEP_FIELD_NUMBER: _ClassVar[int]
    INDEX_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_MS_FIELD_NUMBER: _ClassVar[int]
    step: str
    index: int
    reason: str
    timestamp_ms: int
    def __init__(
        self,
        step: _Optional[str] = ...,
        index: _Optional[int] = ...,
        reason: _Optional[str] = ...,
        timestamp_ms: _Optional[int] = ...,
    ) -> None: ...

class PlanComplete(_message.Message):
    __slots__ = ("ok", "error", "total_steps", "duration_ms")
    OK_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    TOTAL_STEPS_FIELD_NUMBER: _ClassVar[int]
    DURATION_MS_FIELD_NUMBER: _ClassVar[int]
    ok: bool
    error: str
    total_steps: int
    duration_ms: int
    def __init__(
        self,
        ok: bool = ...,
        error: _Optional[str] = ...,
        total_steps: _Optional[int] = ...,
        duration_ms: _Optional[int] = ...,
    ) -> None: ...

class CheckpointDetected(_message.Message):
    __slots__ = ("url", "detail")
    URL_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    url: str
    detail: str
    def __init__(self, url: _Optional[str] = ..., detail: _Optional[str] = ...) -> None: ...

class StartRecordingRequest(_message.Message):
    __slots__ = ("session_id", "page_id", "plan_name")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PAGE_ID_FIELD_NUMBER: _ClassVar[int]
    PLAN_NAME_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    page_id: str
    plan_name: str
    def __init__(
        self,
        session_id: _Optional[str] = ...,
        page_id: _Optional[str] = ...,
        plan_name: _Optional[str] = ...,
    ) -> None: ...

class StartRecordingResponse(_message.Message):
    __slots__ = ("started", "message")
    STARTED_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    started: bool
    message: str
    def __init__(self, started: bool = ..., message: _Optional[str] = ...) -> None: ...

class StopRecordingRequest(_message.Message):
    __slots__ = ("session_id", "page_id")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PAGE_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    page_id: str
    def __init__(self, session_id: _Optional[str] = ..., page_id: _Optional[str] = ...) -> None: ...

class StopRecordingResponse(_message.Message):
    __slots__ = ("stopped", "plan_json", "recorded_steps")
    STOPPED_FIELD_NUMBER: _ClassVar[int]
    PLAN_JSON_FIELD_NUMBER: _ClassVar[int]
    RECORDED_STEPS_FIELD_NUMBER: _ClassVar[int]
    stopped: bool
    plan_json: str
    recorded_steps: int
    def __init__(
        self,
        stopped: bool = ...,
        plan_json: _Optional[str] = ...,
        recorded_steps: _Optional[int] = ...,
    ) -> None: ...

class GetRecorderStatusRequest(_message.Message):
    __slots__ = ("session_id", "page_id")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PAGE_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    page_id: str
    def __init__(self, session_id: _Optional[str] = ..., page_id: _Optional[str] = ...) -> None: ...

class GetRecorderStatusResponse(_message.Message):
    __slots__ = ("recording", "plan_name", "recorded_steps")
    RECORDING_FIELD_NUMBER: _ClassVar[int]
    PLAN_NAME_FIELD_NUMBER: _ClassVar[int]
    RECORDED_STEPS_FIELD_NUMBER: _ClassVar[int]
    recording: bool
    plan_name: str
    recorded_steps: int
    def __init__(
        self,
        recording: bool = ...,
        plan_name: _Optional[str] = ...,
        recorded_steps: _Optional[int] = ...,
    ) -> None: ...
