# -*- coding: utf-8 -*-
"""tracker_reconciliation_worker — durable processing AdSet.pro postback'ов.

В реальном времени применяет inbox-события к click-state и периодически сверяет
локальный inbox с provider API.
"""
