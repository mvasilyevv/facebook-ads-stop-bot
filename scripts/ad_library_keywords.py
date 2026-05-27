"""Словари ключевых слов + score-классификатор для gambling/casino креативов.

Источники:
- https://traffhub.media/articles/creatives/kak-iskat-gembling-kreativy-v-fb-ads/
- Cropink Ad Library 2026 guide
- MagicClick.partners Top Gambling Creatives Turkey & Italy
- AffiliateFix gambling threads
- Реальные данные PoC v3 (388 gambling из 709 raw ads на 6 GEO)
"""

from __future__ import annotations

import re

# ─── Универсальные брендовые слоты ────────────────────────────────────────
# Эти крео крутятся почти везде. Топ-15 по индустрии 2026.
UNIVERSAL_SLOT_BRANDS: list[str] = [
    "Aviator",  # Spribe crash-game — №1 в Африке/LatAm/CIS
    "Chicken Road",  # 50% всех крео в Турции, растёт в Африке
    "Plinko",  # Slot — глобально, в любом GEO
    "Sweet Bonanza",  # Pragmatic Play — топ в EU/CIS/TR
    "Gates of Olympus",  # Pragmatic Play — топ в EU/IT
    "Fortune Tiger",  # PGSoft slot — топ в LatAm/SEA/MZ
    "Book of Ra",  # Novomatic — топ в EU/MENA
    "JetX",  # Smartsoft crash — замена Aviator в португалоязычной Африке
    "Big Bass Bonanza",
    "Sugar Rush",
    "Mines",
    "Lucky Jet",
]

# ─── Keywords по GEO ──────────────────────────────────────────────────────
GEO_KEYWORDS: dict[str, list[str]] = {
    # Кения — узкие gambling-специфичные фразы.
    # Принцип: НЕ generic "Aviator" / "casino" (даёт очки/мусор), а конкретные
    # фразы которые используют ТОЛЬКО casino/букмекеры в Кении.
    #
    # Источники: traffhub.media + adligator (Africa gambling), наш PoC v3 на KE.
    "KE": [
        "M-Pesa bonus",  # M-Pesa = главная платёжка KE для casino/букмекеров
        # Расширим до 3-5 после валидации одного запроса
    ],
    "GH": ["Aviator", "Plinko", "SportyBet", "Bet9ja", "BetWay"],
    "CD": ["Aviator", "Plinko", "Premier Bet", "1xBet", "casino en ligne"],
    "MZ": ["Aviator", "Plinko", "PlayPix", "cassino", "bônus"],
    "CI": ["Aviator", "Plinko", "1xBet", "Premier Bet", "paris sportifs"],
    "SN": ["Aviator", "Plinko", "1xBet", "casino en ligne", "Premier Bet"],
    "TR": ["slot", "casino", "Sweet Bonanza", "bonus", "Aviator"],
    "IT": ["casinò", "slot online", "Sweet Bonanza", "bonus benvenuto", "Aviator"],
}


# ─── Score-классификатор ──────────────────────────────────────────────────
#
# Сигналы получают вес, общий score сравнивается с threshold.
# Цель: precision 85%+ на нашем PoC-наборе.

# Точные слот/crash-бренды → сильный сигнал (+5).
_BRAND_SLOTS = {
    "aviator",
    "chicken road",
    "plinko",
    "sweet bonanza",
    "gates of olympus",
    "fortune tiger",
    "fortune ox",
    "fortune rabbit",
    "book of ra",
    "jetx",
    "big bass bonanza",
    "sugar rush",
    "mines",
    "lucky jet",
    "mahjong ways",
    "starlight princess",
    "dog house",
    "wild west gold",
    "buffalo king",
    "treasures of aztec",
    "caishen wins",
    "hand of anubis",
}

# Локальные букмекеры/casino-бренды → сильный сигнал (+5).
_BOOKIE_BRANDS = {
    # Африка
    "betika",
    "sportpesa",
    "odibet",
    "mozzart",
    "1xbet",
    "22bet",
    "betpawa",
    "elitebet",
    "sportybet",
    "betway",
    "msport",
    "bet9ja",
    "premier bet",
    "playpix",
    "888bets",
    "bantubet",
    "elephant bet",
    "placard",
    # Турция
    "mostbet",
    "pin-up",
    "pinup",
    "bahsegel",
    "bets10",
    "misli",
    "1win",
    # Италия (ADM-лицензированные + грей)
    "sisal",
    "snai",
    "eurobet",
    "goldbet",
    "lottomatica",
    "starcasinò",
    # Generic global
    "vavada",
    "stake",
    "roobet",
    "bc.game",
    "bcgame",
}

# Generic gambling-термины на разных языках → средний сигнал (+3 локальные, +2 en).
_LOCAL_GAMBLING = {
    # Русский / украинский
    "казино",
    "слот",
    "ставк",
    "выигр",
    "депозит",
    "фриспин",
    "джекпот",
    # Французский
    "casino en ligne",
    "paris sportifs",
    "machine à sous",
    "bonus de bienvenue",
    "loto",
    "parifoot",
    "gagne",
    "tirage",
    # Португальский
    "cassino",
    "apostas",
    "ganhar",
    "bônus",
    "casas de apostas",
    "bónus de boas-vindas",
    "metical",
    "rodadas grátis",
    # Испанский
    "tragamonedas",
    "apuesta",
    "ganar",
    "bonificación",
    "giros gratis",
    # Турецкий
    "bahis",
    "kazan",
    "kumar",
    "casino siteleri",
    "slot oyna",
    "çevrimsiz",
    # Итальянский
    "casinò",
    "scommesse",
    "vincita",
    "puntata",
    "giri gratis",
    "bonus benvenuto",
    # Swahili
    "bahati",
    "shinda",
    "bonasi",
    "mchezo",
}

_EN_GAMBLING_GENERIC = {
    "casino",
    "slot",
    "jackpot",
    "spin",
    "wager",
    "odds",
    "betting",
    "free spins",
    "welcome bonus",
    "deposit bonus",
    "no deposit",
    "withdrawal",
    "promo code",
    "cashback",
    "free bet",
    "live bet",
}

# Gambling-эмодзи → слабый сигнал (+1 each, cap +3).
_GAMBLING_EMOJI = {
    "🎰",
    "🎲",
    "💰",
    "💎",
    "🎁",
    "🤑",
    "💸",
    "🍀",
    "🎯",
    "🏆",
    "👑",
    "💵",
    "7️⃣",
    "🍒",
    "🚀",
    "✈️",
}

# Cloak-домены → +1 (часто в gambling-рекламе).
_CLOAK_DOMAIN_RE = re.compile(
    r"\b(?:bit\.ly|cutt\.ly|tinyurl|t\.co|lnkd\.in|is\.gd|clck\.ru|vk\.cc)\b|"
    r"\.(?:club|fun|icu|top|cyou|life|online|app)\b",
    re.IGNORECASE,
)

# Цифровые маркеры → +2 (характерные для gambling-бонусов).
_DIGIT_BONUS_RE = re.compile(
    r"[+x]\s*\d+\s*%|"  # +500%, x40
    r"\d+\s*(?:FS|free spins|spins|fritspins|giri|тур[а-я]*)\b|"  # 150 FS
    r"\d{2,}\s*(?:EUR|USD|GBP|KES|GHS|TRY|TL|RUB|UAH|MT|CDF|XOF|NGN|₽|€|\$)\b",  # 150 EUR
    re.IGNORECASE,
)

# Селебрити/deepfake-маркеры → +3 (TR/IT популярная техника).
_CELEBRITY_NAMES = {
    "erdogan",
    "ronaldo",
    "messi",
    "elon musk",
    "khabib",
    "ramos",
    "andrea pirlo",
    "del piero",
    "francesco totti",
}
_DEEPFAKE_HOOKS = {
    "secret method",
    "новый метод",
    "metodo nuovo",
    "kazanma yöntemi",
    "leaked secret",
    "billionaire reveals",
    "разоблачил",
    "раскрыл секрет",
}

# Жёсткие anti-маркеры (по page_name) → −10, моментальный fail.
_NON_GAMBLING_PAGES = {
    # Книжные / новелльные апы
    "dramabox",
    "drama box",
    "lovely books",
    "novel king",
    "goodnovel",
    "dreame",
    "webnovel",
    "webfic",
    "meganovel",
    "inkitt",
    "novelcat",
    "mybooks",
    "radish",
    "pocketfm",
    "wehear",
    "audible",
    "kindle",
    "wattpad",
    # Дорама-апы
    "shortmax",
    "flexreels",
    "reelshort",
    "goodshort",
    "dramawave",
    "kdrama",
    "chineseshort",
    "quickdrama",
    "quick drama",
    # Match-3 / casual игры
    "royal match",
    "gardenscapes",
    "homescapes",
    "match factory",
    "monopoly go",
    "solitaire",
    "bingo blitz",
    "wordscapes",
    "candy crush",
    # Финансовые услуги (insurance, loans — НЕ gambling)
    "family first life",
    "life insurance",
    "term life",
    # E-commerce / dropshipping
    "shein",
    "temu",
    "aliexpress",
    "amazon",
    "wildberries",
    "ozon",
    "dealggo",  # phone case shop из PoC
    # Лайфстайл
    "headspace",
    "calm",
    "duolingo",
    "babbel",
    "noom",
    "decordin",
}

# Слабые anti-маркеры в тексте → −5 (физтовары, доставка, книги, очки).
_NON_GAMBLING_TEXT_MARKERS = {
    "free shipping",
    "shipping worldwide",
    "доставка",
    "phone case",
    "chapter",
    "novel",
    "ebook",
    "kindle edition",
    "audiobook",
    "courses",
    "diet plan",
    "weight loss",
    "kids learn",
    "school",
    "life insurance",
    "term life",
    "policy",
    "premium",
    # Очки/eyewear (борьба с "Aviator sunglasses" false positives)
    "sunglasses",
    "eyewear",
    "frame ",
    "frames ",
    "lenses",
    "polarized",
    "uv-blocking",
    "uv protection",
    "shades ",
    "очки",
    "occhiali",
    "lunettes",
    "óculos",
    "gözlük",
    # Прочие физтовары
    "shockproof",
    "screen protector",
    "kickstand",
    "s pen slot",
    "samsung galaxy",
    "iphone case",
    "earbuds",
    "headphones",
}


def _normalize(text: str) -> str:
    """Нижний регистр + сжатие пробелов."""
    return " ".join((text or "").lower().split())


def score_gambling(*, page_name: str, creative_text: str) -> tuple[int, list[str]]:
    """Считает gambling-score для рекламы. Возвращает (score, reasons).

    Threshold по умолчанию — 5: >= 5 → gambling.
    """
    page = _normalize(page_name)
    text = _normalize(creative_text)
    full = f"{page} {text}"
    raw_text = creative_text or ""

    score = 0
    reasons: list[str] = []

    # ─── Жёсткий blacklist по странице (−10, моментальный fail) ──
    for marker in _NON_GAMBLING_PAGES:
        if marker in page:
            return -10, [f"BLACKLIST page: '{marker}'"]

    # ─── Слабые anti-маркеры в тексте ──
    for marker in _NON_GAMBLING_TEXT_MARKERS:
        if marker in text:
            score -= 5
            reasons.append(f"−5 anti-text: '{marker}'")
            break  # один штраф достаточно

    # ─── Бренд-слоты (+5) ──
    for brand in _BRAND_SLOTS:
        if brand in full:
            score += 5
            reasons.append(f"+5 slot-brand: '{brand}'")
            break

    # ─── Букмекерские бренды (+5) ──
    for bookie in _BOOKIE_BRANDS:
        if bookie in full:
            score += 5
            reasons.append(f"+5 bookie: '{bookie}'")
            break

    # ─── Локальные gambling-термины (+3) ──
    local_hits = [m for m in _LOCAL_GAMBLING if m in text]
    if local_hits:
        bonus = min(len(local_hits), 2) * 3  # cap +6
        score += bonus
        reasons.append(f"+{bonus} local-gambling: {local_hits[:3]}")

    # ─── Английские generic-термины (+2) ──
    en_hits = [m for m in _EN_GAMBLING_GENERIC if m in text]
    if en_hits:
        bonus = min(len(en_hits), 2) * 2  # cap +4
        score += bonus
        reasons.append(f"+{bonus} en-generic: {en_hits[:3]}")

    # ─── Эмодзи (+1 each, cap +3) ──
    emoji_hits = [e for e in _GAMBLING_EMOJI if e in raw_text]
    if emoji_hits:
        bonus = min(len(emoji_hits), 3)
        score += bonus
        reasons.append(f"+{bonus} emoji: {emoji_hits[:3]}")

    # ─── Cloak-домены (+1) ──
    if _CLOAK_DOMAIN_RE.search(raw_text):
        score += 1
        reasons.append("+1 cloak-domain")

    # ─── Цифровые бонус-маркеры (+2) ──
    if _DIGIT_BONUS_RE.search(raw_text):
        score += 2
        reasons.append("+2 digit-bonus")

    # ─── Селебрити + deepfake-hook (+3) ──
    has_celeb = any(c in text for c in _CELEBRITY_NAMES)
    has_hook = any(h in text for h in _DEEPFAKE_HOOKS)
    if has_celeb and has_hook:
        score += 3
        reasons.append("+3 deepfake-celeb")

    return score, reasons


# ─── Старый API для обратной совместимости с PoC ──────────────────────────
GAMBLING_THRESHOLD = 5


def is_gambling_ad(*, page_name: str, creative_text: str) -> tuple[bool, str]:
    """Heuristic-классификатор. Возвращает (is_gambling, reason).

    Использует score_gambling под капотом. Threshold = 5.
    """
    score, reasons = score_gambling(page_name=page_name, creative_text=creative_text)
    if score >= GAMBLING_THRESHOLD:
        return True, f"score={score}: " + ", ".join(reasons[:3])
    return False, f"score={score} < {GAMBLING_THRESHOLD}"
