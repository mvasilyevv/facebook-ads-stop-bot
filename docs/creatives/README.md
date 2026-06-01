# Creative System — как работать с креативами и заливами

Точка входа. **Цель: находить ЛУЧШИЙ креатив, а не реплицировать прошлый** — замыкая петлю
«хук → креатив → FTD». Винер — один из референсов; конкуренты анализируются всегда; чемпион
переизбирается по данным.

## С чего начать
1. **Процесс целиком** → [`SOP.md`](SOP.md) (Фазы 0–4: разведка → план → производство → залив → анализ).
2. **Контекст вертикали и находки** → [`../creative_kb.md`](../creative_kb.md) (раздел 0 — whitelist/правила, читать первым).
3. **Контракт полей реестра** → [`_schema.md`](_schema.md).

## Что где лежит

| Путь | Назначение |
|---|---|
| `SOP.md` | процесс залива A→Z (cold/warm, чемпион/челленджеры, гейты) |
| `PROMPTING.md` | best-practice промт-инжиниринга для генерации (Sora/GPT Image): анатомия, текст, UGC-реализм |
| `_schema.md` | контракт полей реестра |
| `hooks.yaml` | хуки-атомы (geo/slot/visual/text) с вердиктами |
| `geo/<GEO>/geo.yaml` | рынок гео, geo-хуки, **production_profile** (как генерить под гео) |
| `geo/<GEO>/slots/<SLOT>.yaml` | слот: идеи, референсы (свои+конкуренты), креативы |
| `reports/_TEMPLATE.md` | шаблон research-отчёта — **гейт перед генерацией** |
| `geo/<GEO>/reports/<SLOT>_<дата>.md` | сами отчёты разведки (с картинками) |
| `../creative_kb.md` | нарратив/история находок (проза) |
| `scripts/recon_adlib.py` | разведка Ad Library (изолированный playwright) |
| `scripts/creative_report.py` | петля «хук→FTD» (реестр ⨝ AdSet.pro) |
| `core/creatives/registry.py` | загрузчик + валидатор реестра |

## Быстрые команды
```bash
# Разведка Ad Library (первый раз — вход в FB-расходник):
python scripts/recon_adlib.py --geo KE --query "chicken road 2" --headed --login
python scripts/recon_adlib.py --geo KE --query "chicken road"        # дальше headless

# Петля результатов (что заходит по FTD):
python scripts/creative_report.py --days 30 --write

# Уникализация отобранных:
# core.creatives.uniquify_creatives → ~/Documents/FB_Agent_Creo/
```

## Текущее состояние
- **Гео:** KE (warm — есть FTD) · **слот:** CR2 (Chicken Road 2).
- **Чемпион:** `KE_CR2_CR005` (нативный пост + M-Pesa-пруф, 2 FTD/30 кликов).
- **Round 2:** `KE_CR2_CR013…CR019` — `draft` (под генерацию на syntx).
- **Разведка:** `recon_adlib.py` работает; конкуренты (Plinko 2, Moyo, Arcade, 1XBET…) — в `geo/KE/slots/CR2.yaml`.

## Принципы (кратко)
- **Найти лучший**, не реплицировать: чемпион vs челленджеры, leaderboard переизбирает по FTD.
- **Знание только с цифрами** (FTD) — иначе `testing`/`unknown`.
- **whitelist:** формулировки про деньги/вывод/бонусы НЕ смягчаем (см. `creative_kb.md` §0).
- **Разведка** — изолированный playwright (НЕ боевой Vision), один браузер на Ad Library + syntx.
- **production_profile** — как генерить под гео (напр. Tier-3 → народное > вылизанное), анализируется раз.
