# -*- coding: utf-8 -*-
"""Исполнитель типизированных Marketing API-команд из PostgreSQL task queue.

Worker соблюдает lane, lease, fencing token, абсолютный deadline и фиксирует
только ``CONFIRMED | REJECTED | UNKNOWN`` через единый lifecycle команд.
"""
