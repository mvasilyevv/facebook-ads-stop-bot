# План батча GH/AVI #01 — промты под генерацию (Фаза 1)

**Гео:** GH · **слот:** AVI (Aviator, агрегатор) · **bonus:** депозит 10 GHS → 20 free bets ·
**модель:** Sora (GPT) Image (syntx Дизайн) · **формат:** 1080×1080 · **дата:** 2026-05-31
**production_profile:** Tier-3 народное (НЕ глянец), ганский типаж, MTN MoMo, GHS, Pidgin/Twi, 🇬🇭, правдоподобные суммы.

> Whitelist: формулировки про деньги/вывод/бонус НЕ смягчаем. Запретные слова (guaranteed/free money/hack/cheat) — нет.
> Реальные лица игроков (Kudus/Partey и др.) НЕ используем (права + Partey токсичен) — обобщённый фанат в джерси.

---

## CR001 — ЯДРО · подлинный UGC proof-post (формула CR005-KE)
**Хуки:** gh_momo_payout, gh_low_entry, gh_pidgin_touch, vis_native_post(GH), vis_momo_proof(GH)
**angle (sub6):** `1 | Proof Post / MoMo payout`
**Промт:**
> A realistic Facebook post screenshot, mobile feed style, NOT a polished ad. A young Ghanaian man (casual red hoodie) holding his phone showing the Aviator game: red plane, rising multiplier "12.4x", green CASH OUT button "GHS 240". Overlaid like a real phone notification — yellow MTN MoMo SMS banner: "MTN MoMo: You have received GHS 240.00 ✅". Post caption in Ghanaian English with pidgin: "Chale I no believe am at first 😅 I deposit small GHS 10, play Aviator, cash out GHS 240 straight to my MoMo! Ɛyɛ 🇬🇭". Below: 2 Facebook comments — "Withdrew mine sharp sharp 🔥", "Abeg how you take do am?". Authentic, slightly imperfect, native social-media look, not glossy. 1080x1080.

## CR002 — free-bets low-entry (bonus message-match)
**Хуки:** gh_low_entry, avi_freebets, gh_momo_payout
**angle (sub6):** `2 | Free Bets / Low Entry GHS 10`
**Промт:**
> Realistic mobile screenshot / native social style. Focus: a yellow MTN MoMo notification "You have received GHS 185.00" next to the Aviator red plane. Bold but authentic text: "Deposit GHS 10, get 20 FREE BETS on Aviator 🛩️". An ordinary happy Ghanaian person, Accra street/market background, holding phone. Small Ghana flag 🇬🇭. Pidgin touch: "Small money, big win — Ɛyɛ!". Tier-3 authentic, not glossy. 1080x1080.

## CR003 — FOMO нативно + Pidgin (заряд Luck&strategy, но как реальный чат)
**Хуки:** avi_fomo_greed, gh_pidgin_touch
**angle (sub6):** `3 | FOMO / Friends Winning`
**Промт:**
> Native phone screenshot look — a WhatsApp/Facebook group chat where friends share Aviator wins: green chat bubbles + yellow MTN MoMo "received GHS 320", "GHS 150". Caption pidgin: "Chale everybody dey win for Aviator, why you dey wait? 😮 My guys cash out every day 💸🇬🇭". Authentic chat-screenshot style, real phone, not an ad poster. 1080x1080.

## CR004 — adrenaline cashout (формат-тест)
**Хуки:** avi_crash_adrenaline
**angle (sub6):** `4 | Adrenaline / Cash Out`
**Промт:**
> Dynamic but authentic mobile capture of Aviator gameplay: red plane climbing, multiplier "8.7x" rising, green CASH OUT button highlighted "GHS 210". A young Ghanaian hand holding the phone, tense excited face partly visible. Text overlay pidgin: "Catch am before e fly! Cash out sharp 🛩️🔥". Energetic, slightly raw, real screen-record feel, not polished poster. 1080x1080.

## CR005 — football-anchor (свободный угол, никто не делает)
**Хуки:** gh_pidgin_touch, avi_football_anchor
**angle (sub6):** `5 | Football / Black Stars Hype`
**Промт:**
> Authentic native post. A Ghanaian football fan in a red-yellow-green jersey (Black Stars colours, NO real player faces) celebrating, holding phone showing an Aviator win and a yellow MTN MoMo "received GHS 260". Caption: "Match day + Aviator = double win! Deposit GHS 10, cash out to MoMo 🇬🇭⚽". Street/stadium viewing background, World Cup 2026 hype vibe. Authentic, not glossy. 1080x1080.

---

## Скоринг каждого (SOP Фаза 2.6) — критерии
- стиль = Tier-3 народное (НЕ глянец-баннер как Betano/Betway);
- читаемость текста на картинке, корректность GHS-сумм (правдоподобные, низкие);
- MoMo-плашка узнаваема (жёлтый MTN MoMo);
- нативность (реальный пост/скрин/чат, не дизайн-постер) — главный дифференциатор;
- нет запретных слов; формат 1080×1080.
> Лимит перегенераций: 2–3, потом флаг байеру.
