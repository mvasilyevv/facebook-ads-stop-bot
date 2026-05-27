# -*- coding: utf-8 -*-
"""creator_worker — исполнитель планов создания кампаний (Vision-creator fallback).

См. META_INTEGRATION_PLAN.md § 3.9 — Vision-creator остаётся для gambling.
Поллит task_queue task_type='plan_run', читает план из creator_plans,
дёргает gRPC CreatorService.RunPlan и стримит события.
"""
