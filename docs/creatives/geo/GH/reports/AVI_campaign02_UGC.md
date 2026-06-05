# Кампания GH/Aviator #02 UGC (видео) — ТЗ на сборку

**Offer-код:** GH_AVI · **гео:** Ghana · **bonus:** депозит 10 GHS → 20 free bets
**имя кампании:** `MV | GH | AVI | UGC | adset.pro | 05.06` (дата = next-day, старт 05.06)
**статус:** ТЗ зафиксировано 2026-06-04. Сборка — ПОСЛЕ Gate AV видео + uniquify. Запуск — после go байера + `ad_account_id` + `pixel_id`.

## Бюджет/структура (ВАЖНО: CBO, не ABO)
- **CBO — Advantage Campaign Budget**, `daily_budget` = **$8.99/день на уровне КАМПАНИИ** (FB распределяет между адсетами сам). НЕ ABO, НЕ $2.99/адсет — это было в #01 статиков.
- **Структура 1-3-3 (вариант A — тест ТЕКСТА):** 1 кампания (CBO) / **3 адсета = 3 разных текста-угла** / в КАЖДОМ адсете все 3 видео (VID001+VID002+VID003) = **9 объявлений**.
  - Переменная теста = ТЕКСТ (набор видео одинаков во всех адсетах). Зеркало #01 (там тест формата при едином тексте).

| Адсет | Текст-угол | Креативы (ads) |
|---|---|---|
| AS1 | Текст 1 — payment-trust (MoMo вывод) | VID001, VID002, VID003 |
| AS2 | Текст 2 — low-entry (10 GHS → 20 free bets) | VID001, VID002, VID003 |
| AS3 | Текст 3 — FOMO / social-proof | VID001, VID002, VID003 |

## Настройки (как #01)
- **Objective:** OUTCOME_SALES · **optimization_goal:** OFFSITE_CONVERSIONS (депозит-событие пикселя PWA).
- **Гео:** Ghana · **язык:** English · **плейсмент:** Advantage+ (FB/IG feed+stories) · **возраст:** 21-55, all.
- **Destination:** PWA-линк (evergreen PWA из AVI_pwa01.md, переиспользуется). **Трекинг:** sub3=code креатива (VID001/002/003), sub6=angle (текст-угол) через URL PWA → AdSet.pro.
- **special_ad_categories:** [NONE] (gambling whitelist). **status_after_create:** PAUSED → проверка в Ads Manager → unpause после go.

## Тексты (3 угла — НАБРОСОК на апрув байера)

**Текст 1 — Payment-trust / MoMo (winner-угол #01):**
> Primary: Deposit just GHS 10, play Aviator, and cash out straight to your MTN MoMo 💸✈️ New players get 20 FREE BETS on your first deposit! 🇬🇭 Small start, real wins — withdraw to MoMo anytime. Play now!
> Headline: Deposit GHS 10 → Get 20 Free Bets on Aviator
> Description: Cash out wins straight to MTN MoMo. Fast & safe.

**Текст 2 — Low-entry / objection-kill:**
> Primary: Start with just GHS 10 and get 20 FREE BETS on Aviator! 🇬🇭✈️ No big budget needed — watch the plane fly, cash out before it's gone. Win small, win often, withdraw to MoMo. Try it today!
> Headline: Just GHS 10 → 20 Free Bets on Aviator
> Description: Low start, real wins to MTN MoMo.

**Текст 3 — FOMO / social-proof:**
> Primary: Ghanaians are cashing out to MoMo every day with Aviator! 🇬🇭🔥 Deposit GHS 10, grab your 20 FREE BETS, and catch the multiplier before it flies. Don't watch others win — join the game now! ✈️💸
> Headline: Everyone's Winning on Aviator 🇬🇭
> Description: GHS 10 → 20 free bets. Cash out to MoMo.

**CTA-кнопка:** `Play Game` (или `Sign Up`). Whitelist: без guaranteed/free money/hack/cheat; деньги/вывод/бонус не смягчены.

## PRE-FLIGHT чеклист
| Пункт | Статус |
|---|---|
| 3 видео (VID001/002/003) сгенерены, 9:16, Seedance 2.0 Pro | ⏳ в работе (VID003 первым) |
| Gate AV приёмка видео | ⏳ |
| Uniquify видео (если нужно под 9 ads — копии/ад) | ⏳ |
| Тексты ×3 апрув байера | ⏳ |
| CBO $8.99/день, структура 1-3-3 (A) | ✅ ТЗ |
| Имя `MV \| GH \| AVI \| UGC \| adset.pro \| 05.06` | ✅ |
| PWA-линк + пиксель | ⏳ от байера (`ad_account_id` + `pixel_id`) |
| go байера | ⏳ |
