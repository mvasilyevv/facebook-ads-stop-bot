# -*- coding: utf-8 -*-
"""creator_recorder — записывает планы создания кампаний через CreatorService.

Триггеры через Redis pubsub:
  fb_agent:creator:record_start → StartRecording
  fb_agent:creator:record_stop  → StopRecording + INSERT в creator_plans (is_archived=false)

Полная TG-команда /record_plan — отдельный TODO для следующего раунда.
"""
