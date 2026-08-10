"""Enable optional process-wide OpenTelemetry before application imports."""

from __future__ import annotations

try:
    from core.telemetry import initialize_telemetry

    initialize_telemetry()
except Exception:  # telemetry must never prevent safety/control startup
    pass
