# -*- coding: utf-8 -*-
"""creator_recorder — записывает планы создания кампаний через CreatorService.

Триггеры через Redis pubsub:
  fb_agent:creator:record_start → StartRecording
  fb_agent:creator:record_stop  → StopRecording + INSERT в creator_plans (is_archived=false)

TG-команды старта/остановки записи (`/record_plan`, `/stop_record`, `/plans`)
реализованы в `core/telegram/handlers/creator.py` и публикуют в эти же каналы.
"""
