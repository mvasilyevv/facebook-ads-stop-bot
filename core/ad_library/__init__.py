# -*- coding: utf-8 -*-
"""Ad Library модуль: scanner + classifier + media + enricher + tier_ranker + report.

Архитектурное правило (НЕЛЬЗЯ нарушать):
- slot и country приходят от пользователя ДОСЛОВНО.
- Не подменяем keyword, не добавляем variations без явного флага.
- Не «помогаем» расширением запроса.
- Если pool пустой — empty result с честным reason, не fallback.
"""

from __future__ import annotations
