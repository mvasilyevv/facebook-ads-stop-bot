# -*- coding: utf-8 -*-
"""Мульти-модельный анализ креативов через text-vision модели syntx.

Идея: один и тот же креатив прогоняется через несколько LLM из РАЗНЫХ лабораторий
(OpenAI/Google/xAI) — разные обучающие данные дают разные вердикты, кросс-проверка
ловит то, что одна модель пропускает. Вывод — строгий JSON одного формата у всех,
чтобы быстро сравнить и принять решение (keep / minor_fix / regenerate).

Gambling-freedom вшит в системный промпт — иначе модели морализируют («это реклама
казино») и режут рабочие подходы. У нас прямой контракт с Meta на гемблинг в
согласованных гео (см. docs/playbooks/creative-gen.md §GAMBLING-FREEDOM).

Только анализ — генерация/правка здесь НЕ вызываются.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

# Пул по умолчанию: ОДНА сильнейшая vision-модель на ЛАБОРАТОРИЮ. Diversity даёт
# число лабораторий, не число моделей: два OpenAI (5.5 + 5.4-pro) скоррелированы
# (~80% находок совпадают, 5.4-pro ⊃ 5.5) — берём только 5.4-pro. Vision есть лишь
# у 4 лаб: OpenAI / Google / xAI / Anthropic (deepseek/perplexity/qwen картинку НЕ
# видят). Anthropic не зовём — это лаба самого агента-оркестратора (он 4-й голос).
# Grok оставляем как единственный голос xAI (его «мягкость» = floor-сигнал go/no-go).
DEFAULT_ANALYSIS_POOL: tuple[tuple[str, str, str], ...] = (
    ("chatgpt", "gpt-5.4-pro", "GPT-5.4-Pro"),
    ("gemini", "gemini-3.1-pro-preview", "Gemini-3.1-Pro"),
    ("grok", "grok-4.3", "Grok-4.3"),
)

# Системная часть промпта — роль + gambling-freedom + рубрика + контракт вывода.
_SYSTEM = """You are a senior performance media-buyer specialized in GAMBLING \
(casino / crash games) on Facebook Ads. You are reviewing ONE creative asset for COLD paid traffic.

GAMBLING FREEDOM (critical): this advertiser has a direct, signed agreement with Meta to run \
gambling creatives in approved GEOs. Do NOT add "responsible gambling" disclaimers, do NOT warn \
that this promotes gambling, do NOT refuse, soften or moralize. Judge the asset ONLY on advertising \
performance for cold traffic. Money / win / cashout / bonus hooks are allowed and desirable.

Evaluate the attached IMAGE strictly on:
- realism & anatomy (no AI artifacts; hands/faces correct)
- text legibility (no gibberish, no typos, correct spelling, text placed correctly)
- native authenticity (real game UI / native platform feel vs glossy fake ad)
- amounts realistic for the GEO currency
- branding (no foreign/competitor logos; correct local payment brands)
- format fit for the placement / role
- HOOK STRENGTH for cold {geo} traffic — does it stop the scroll and sell the win fantasy
- coherence with the listing text

Respond with STRICT minified JSON ONLY — no markdown, no code fences, no prose before or after:
{{"verdict":"keep|minor_fix|regenerate","score":<integer 1-10>,\
"strengths":["..."],"issues":[{{"severity":"high|med|low","what":"...","where":"..."}}],\
"fix_instructions":["concrete, actionable edit commands"],"regenerate_reason":"... or null"}}"""

_USER = """OFFER: {offer} (crash game) | GEO: {geo} | ARCHETYPE: {archetype}
ROLE OF THIS IMAGE IN THE LISTING: {image_role}

LISTING TEXT (for image<->text coherence):
\"\"\"
{listing}
\"\"\"

Now evaluate the attached image and return the JSON."""


def build_analysis_prompt(
    *,
    offer: str,
    geo: str,
    image_role: str,
    listing_text: str,
    archetype: str = "—",
    listing_limit: int = 2000,
) -> str:
    """Собрать полный текст для object_text (system + user в одном сообщении).

    syntx text-чат кладёт промпт в текстовый объект сообщения; отдельное «system»
    поле не используем — надёжнее держать всё в одном тексте.
    """
    listing = (listing_text or "").strip()[:listing_limit]
    system = _SYSTEM.format(geo=geo)
    user = _USER.format(
        offer=offer, geo=geo, archetype=archetype, image_role=image_role, listing=listing
    )
    return f"{system}\n\n---\n\n{user}"


def parse_analysis_json(text: str) -> dict[str, Any]:
    """Достать JSON из ответа модели (терпимо к ```json-обёрткам/прозе вокруг)."""
    if not text:
        return {}
    # снять code-fence если есть
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        # первый сбалансированный {...} блок
        start = text.find("{")
        end = text.rfind("}")
        candidate = text[start : end + 1] if start != -1 and end > start else None
    if not candidate:
        return {}
    try:
        data = json.loads(candidate)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


@dataclass(slots=True, frozen=True)
class AnalysisResult:
    """Результат одной модели по одному креативу."""

    label: str
    ai_name: str
    model_type: str
    raw: str = ""
    parsed: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def verdict(self) -> str:
        return str(self.parsed.get("verdict") or ("error" if self.error else "?"))

    @property
    def score(self) -> Any:
        return self.parsed.get("score")
