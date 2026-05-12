# -*- coding: utf-8 -*-
"""Тесты для campaign_creator — базовый интерфейс шагов."""

from __future__ import annotations


def test_base_step_has_required_methods():
    """Каждый Step должен иметь name, is_checkpoint, execute()."""
    from core.campaign_creator.steps.base import BaseStep

    assert hasattr(BaseStep, "name")
    assert hasattr(BaseStep, "is_checkpoint")
    assert hasattr(BaseStep, "execute")
