# Кампания GH/Aviator #01 — структура + тексты (на апрув)

**Offer-код:** GH_AVI · **гео:** Ghana · **bonus:** депозит 10 GHS → 20 free bets ·
**структура:** ABO, 1 кампания × 5 адсетов × 1 креатив (3 uniquify-копии) · **дата:** 2026-06-01
**имя кампании:** `MV | GH | AVI | adset.pro | 01.06`
**бюджет:** **$2.99/адсет/день** (база $3.00 − $0.01) · итого **$14.95/день** на 5 адсетов
**статус:** PRE-FLIGHT READY · create_campaign НЕ запускаем — вечерний запуск после апрува байера, PAUSED → unpause

> **Логика теста:** переменная = ВИЗУАЛ (5 форматов). Текст ЕДИНЫЙ во всех адсетах
> (winner-угол payment-trust), чтобы FTD показал чистого формат-победителя. Тексты-углы — следующий раунд.

## Структура

| Адсет | Креатив | sub3 (code) | sub6 (angle) | uniquify-папка |
|---|---|---|---|---|
| AS1 | CR001 live-пост | GH_AVI_CR001 | `1 \| Proof Post / MoMo` | GH_AVI_CR001_…_3copies |
| AS2 | CR002 before/after | GH_AVI_CR002 | `2 \| Before/After / Free Bets` | GH_AVI_CR002_…_3copies |
| AS3 | CR003 чат | GH_AVI_CR003 | `3 \| FOMO / Friends` | GH_AVI_CR003_…_3copies |
| AS4 | CR004 геймплей | GH_AVI_CR004 | `4 \| Adrenaline / Cashout` | GH_AVI_CR004_…_3copies |
| AS5 | CR005 футбол | GH_AVI_CR005 | `5 \| Football / Black Stars` | GH_AVI_CR005_…_3copies |

- **Бюджет/адсет/день:** **$2.99** (ABO, daily) · итого $14.95/день
- **Гео-таргет:** Ghana · **язык:** English · **плейсмент:** Advantage+ (FB/IG feed+stories) · **возраст:** 21-55, all
- **Objective:** **OUTCOME_SALES** (всегда — цель = депозит, оптимизация под продажи через пиксель PWA). · **status_after_create:** PAUSED

## Тексты (ЕДИНЫЙ winner-угол payment-trust для всех 5 адсетов)

**Primary text (основной):**
> Deposit just GHS 10, play Aviator, and cash out straight to your MTN MoMo 💸✈️
> New players get 20 FREE BETS on your first deposit! 🇬🇭
> Small start, real wins — withdraw to MoMo anytime. Play now!

**Headline:** `Deposit GHS 10 → Get 20 Free Bets on Aviator`
**Description:** `Cash out wins straight to MTN MoMo. Fast & safe.`
**CTA-кнопка:** `Play Game` (или `Sign Up` под лендинг PWA)

> Whitelist: формулировки про деньги/вывод/бонус НЕ смягчены. Без guaranteed/free money/hack/cheat.
> Локальные коды: GHS, MTN MoMo, 20 free bets, 🇬🇭. Pidgin в primary убран намеренно (текст — общий, нейтрально-ганский; Pidgin живёт в визуале креативов).

## PRE-FLIGHT чеклист (готовность к вечернему запуску)

| Пункт | Статус |
|---|---|
| 5 креативов переделаны, чистые (анатомия/текст/логика OK) | ✅ |
| Uniquify 3 копии/креатив (15 JPEG, md5 разные) | ✅ `~/Documents/FB_Agent_Creo/GH_AVI_CR00{1..5}_…_3copies/` |
| Структура ABO 5×1 + sub3/sub6 макросы | ✅ (таблица выше) |
| Тексты (единый winner-угол) | ✅ |
| Бюджет $2.99/адсет/день | ✅ |
| Имя кампании `MV \| GH \| AVI \| adset.pro \| 01.06` | ✅ |
| Objective OUTCOME_SALES (всегда, цель=депозит) | ✅ |
| PWA-ассеты + контент | ✅ (AVI_pwa01.md) |
| Бек поднят (docker 5433 + воркеры) | ⏳ перед запуском: `./run.sh` |
| ad_account_id (act_X) + pixel_id PWA | ⏳ от байера при запуске |
| PWA собран в билдере + линк | ⏳ байер собирает |
| Апрув байера на структуру+тексты | ⏳ |

## План вечернего запуска (по шагам, когда дашь go)
1. Поднять бек: `./run.sh` (docker + миграции + воркеры + meta_api_worker).
2. Байер даёт `ad_account_id` + финальный objective (TRAFFIC/SALES после PWA).
3. `request_create_campaign` (DRAFT, **status_after_create=PAUSED**) — параметры готовы (ниже).
4. Адсеты ×5 + ads с uniquify-копиями + sub3/sub6 — через create_campaign payload / Vision (зависит от того, что поднято).
5. Проверка в Ads Manager (PAUSED) → апрув байера → **unpause**.
6. После unpause: `status: live` в реестре AVI.yaml; через ~неделю — creative_report по FTD.

### Заготовка параметров create_campaign (campaign-уровень)
```
name: "MV | GH | AVI | adset.pro | 01.06"
objective: OUTCOME_SALES           # всегда — цель депозит, оптимизация под продажи
special_ad_categories: [NONE]      # gambling whitelist, не SAC
optimization_goal: OFFSITE_CONVERSIONS   # под депозит-событие пикселя PWA
status_after_create: PAUSED
# бюджет на адсетах (ABO): daily $2.99 каждый, НЕ на кампании
ad_account_id: act_XXXXX           # от байера
pixel_id: XXXXX                    # пиксель PWA (deposit event) — от байера
```

> create_campaign **НЕ запускаем сейчас** — pre-flight ready. БД сейчас лежит (порт 5433);
> поднять `./run.sh` перед запуском. Решение байера: ОК на структуру+тексты / правки?
