# -*- coding: utf-8 -*-
"""Observer — минимальный сканер для новой схемы.

Без эскалаторов хрупкости, без adaptive CPA — только базовый FSM на ad_alert_state +
оценка правил + outbox в task_queue. Все обвязки усложнения добавляются отдельно
после того как minimum viable observer стабилизируется в проде.
"""

from __future__ import annotations
