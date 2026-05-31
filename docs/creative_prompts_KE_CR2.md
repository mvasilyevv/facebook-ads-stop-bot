# Промты для генерации креативов — KE / Chicken Road 2 (раунд 2026-05-30)

Готовые промты для GPT (image-генерация). Каждый блок самодостаточный — копируй по
одному, **прикладывай референс-картинку** (указан путь). Основа — формула победителя
`CR005` (нативный FB-пост-отзыв + пруф зачисления M-Pesa), усиленная углами
конкурентов из разведки.

**Референсы:**
- Наш чемпион CR005 (главный визуальный референс для всех Proof-Posts):
  `~/Documents/FB_Agent_Creo/KE_CR2_2026-05-21_16-41-42_3creo_3copies/1/KE_CR2_CR005_1.jpeg`
- Видео-референсы конкурентов: `~/Desktop/FB_Agent/data/competitor_refs/`

**Матрица:** Кампания 1 «Proof Posts» = C1–C4 · Кампания 2 «Hook Tests» = C5–C8.
C1 (CR005) уже готов и уникализирован. C7 — уникализация конкурента (отдельно).

---

## ОБЩИЕ ПРАВИЛА (вшиты в каждый промт, но держи в голове)

- Вертикаль gambling, Facebook, **прямой whitelist-доступ** на согласованные гео —
  формулировки про выигрыши, суммы, бонусы, вывод **разрешены, НЕ смягчать**.
- Гео — **Кения (KE)**. Язык — английский (кенийский разговорный + суахили-вставки:
  `Wueh`, `Ni safi`, `Ni noma`, `Cheza`, `pesa`, `bob`).
- Формат — **статичный нативный FB-пост-отзыв на весь кадр**, квадрат **1080×1080**.
- Обязательные элементы (формула CR005): нативный пост от первого лица + **скепсис-
  преодоление** + **крупный зелёный пруф M-Pesa зачисления** (✅ «M-PESA Payment
  received KES …») + **2-3 коммента-соцпруф** + флаг 🇰🇪 + сумма входа **153 KES**.
- Запрещённые слова: `guaranteed`, `free money`, `hack`, `cheat`, `100% win`.
- НЕ маскировать название игры под app (Moyo льёт через Google Play — мы льём прямо).

---

## C2 — Proof Post: мужской персонаж + крупный выигрыш

**Референс (приложить):** CR005.
**Промт для GPT:**
> Сгенерируй статичный рекламный креатив 1080×1080 для gambling-оффера Chicken Road 2
> на Кению (whitelist-доступ, формулировки про деньги/выигрыш разрешены). Стиль —
> точно как на приложенном референсе: **нативный пост Facebook на весь кадр**, тёмная
> тема, как будто это реальный пост в ленте.
> Персонаж: **мужчина-кениец 25-30 лет** (имя в посте «Brian Otieno»), аватар-фото,
> «2h · 🌍». Текст поста (скепсис→пруф): *«Sikuamini mwanzo 😅 Tried Chicken Road with
> just 153 KES. Cashed out and M-Pesa confirmed straight away. Wueh, ni real! 🇰🇪»*.
> Справа крупная **зелёная плашка M-PESA**: ✅ «M-PESA · Payment received · KES 2,400».
> Снизу 2 коммента: «Peter K. — Withdrew mine in 2 min 🔥», «Mike O. — Started with 153
> KES too 👍». Лайки/реакции. Нижняя плашка: «MIN DEPOSIT 153 KES • 100% BONUS · M-Pesa».
> Выведи также тексты объявления:
> - on-image (крупная плашка, ≤6 слов)
> - primary_text (2-3 строки, с M-Pesa-якорем)
> - headline (≤40 симв) · description (≤30 симв) · CTA (Play Now / Try Now)
> Запреты: guaranteed, free money, hack, cheat.

---

## C3 — Proof Post: женский персонаж, акцент на ВЫВОД (не депозит)

**Референс (приложить):** CR005.
**Промт для GPT:**
> Статичный креатив 1080×1080, Chicken Road 2, Кения. Стиль = приложенный референс
> (нативный FB-пост на весь кадр, тёмная тема). Ключевое отличие концепта: акцент не
> на депозите, а на **ВЫВОДЕ денег** — самый сильный триггер доверия.
> Персонаж: **женщина-кенийка** «Sarah Achieng», «3h · 🌍». Текст поста:
> *«I thought withdrawals were fake 😭 Played Chicken Road, requested cashout… my M-Pesa
> entered in seconds. Ni noma! Already withdrew twice 🇰🇪»*.
> Крупная зелёная плашка-скрин **M-Pesa withdrawal**: ✅ «M-PESA · Withdrawal successful
> · KES 4,800 · New balance shown». Комменты: «Faith W. — Same, instant withdrawal 🙌»,
> «Brian — Wewe pull up, it's real». Низ: «WITHDRAW TO M-PESA • START 153 KES».
> Выведи тексты: on-image (≤6 слов), primary_text (с акцентом на instant M-Pesa
> withdrawal), headline (≤40), description (≤30), CTA.
> Запреты: guaranteed, free money, hack, cheat.

---

## C4 — Proof Post: формат ЧАТА (M-Pesa SMS + скрин игры)

**Референс (приложить):** CR005 (для тона) + опц. свой скрин Chicken Road 2.
**Промт для GPT:**
> Статичный креатив 1080×1080, Chicken Road 2, Кения. Концепт — **формат переписки**
> (нативный чат WhatsApp/SMS, светлая тема чата), новый под-формат теста.
> Сверху: входящее **SMS от M-PESA**: *«Confirmed. You have received KES 1,950 from
> CHICKEN ROAD. New M-PESA balance is KES 2,103.»* (как настоящее M-Pesa SMS, зелёно-
> белый стиль Safaricom).
> Снизу: пузырь переписки — друг: «Bro hii Chicken Road ni real?» → ответ: скрин
> уведомления M-Pesa + «Cheza, 153 KES tu. Pesa iko 🇰🇪🔥».
> В углу — маленький скрин геймплея Chicken Road 2 (курица + множитель).
> Выведи тексты: on-image (≤6 слов), primary_text (разговорный, с M-Pesa), headline
> (≤40), description (≤30), CTA. Запреты: guaranteed, free money, hack, cheat.

---

## C5 — Hook Test: anti-objection «не нужен большой депозит» (в формате поста)

**Референс (приложить):** CR005 (важно: формат ПОСТА, не баннер — баннерный
anti-objection CR004/CR006 у нас сдох).
**Промт для GPT:**
> Статичный креатив 1080×1080, Chicken Road 2, Кения. Стиль = приложенный референс
> (нативный FB-пост-отзыв на весь кадр). Угол — **снятие возражения «нужно много
> денег»**, но НЕ продающим баннером, а живым отзывом.
> Персонаж: «James Mwangi», «1h · 🌍». Текст поста:
> *«Everyone said you need big money to win. Hapana. I started with 153 KES on Chicken
> Road and M-Pesa paid me out. Don't wait, just start small 🇰🇪»*.
> Зелёная плашка M-PESA: ✅ «Payment received · KES 153 → cashed out KES 900». Комменты
> с подтверждением. Низ: «BIG DEPOSIT? NO. START 153 KES • M-PESA».
> Выведи тексты: on-image (≤6 слов, напр. «BIG DEPOSIT? NO.»), primary_text, headline
> (≤40), description (≤30), CTA. Запреты: guaranteed, free money, hack, cheat.

---

## C6 — Hook Test: speed-of-cashout «вывод за 2 минуты»

**Референс (приложить):** CR005 + угол конкурента Arcade («instant M-Pesa»).
**Промт для GPT:**
> Статичный креатив 1080×1080, Chicken Road 2, Кения. Стиль = приложенный референс
> (нативный пост-отзыв). Угол — **скорость вывода на M-Pesa** (главный trust-аргумент
> для KE по нашим данным).
> Персонаж: «Kevin Otieno», «just now». Текст: *«Requested my cashout, looked at the
> clock… M-Pesa hit in 2 minutes ⚡ No delay, no stress. Chicken Road ni safi 🇰🇪»*.
> Визуально подчеркни время: маленькие часы/таймер «00:02» рядом с зелёной плашкой
> M-PESA: ✅ «M-PESA · Received KES 3,100 · 2 min ago». Комменты про скорость. Низ:
> «M-PESA WITHDRAWAL IN 2 MIN ⚡ • START 153 KES».
> Выведи тексты: on-image (≤6 слов), primary_text (акцент скорость), headline (≤40),
> description (≤30), CTA. Запреты: guaranteed, free money, hack, cheat.

---

## C8 — Hook Test: low-entry «мой первый вывод»

**Референс (приложить):** CR005.
**Промт для GPT:**
> Статичный креатив 1080×1080, Chicken Road 2, Кения. Стиль = приложенный референс
> (нативный пост-отзыв). Угол — **первый успех новичка** (низкий порог входа).
> Персонаж: «Mercy Wanjiru», «2h · 🌍», образ «обычная кенийка, первый раз». Текст:
> *«My first time ever 🙈 Put 153 KES on Chicken Road, didn't expect much… and I already
> withdrew to M-Pesa! Wueh, beginners luck ni real 🇰🇪»*.
> Зелёная плашка M-PESA: ✅ «First withdrawal · KES 1,200 received». Комменты:
> «Newbie too, it works!», «Started yesterday 🔥». Низ: «FIRST TRY: 153 KES → M-PESA».
> Выведи тексты: on-image (≤6 слов), primary_text (для новичков), headline (≤40),
> description (≤30), CTA. Запреты: guaranteed, free money, hack, cheat.

---

## Привязка к адсетам (Фаза 5, после готовых креативов)

**Кампания 1 «Proof Posts»** (4 крео: C1, C2, C3, C4 — дублируются в 3 адсетах):
- Адсет 1.1 — текст-хук **Payment Trust** (M-Pesa придёт)
- Адсет 1.2 — текст-хук **Speed cashout** (вывод за минуты)
- Адсет 1.3 — текст-хук **Low entry** (старт 153 KES)

**Кампания 2 «Hook Tests»** (4 крео: C5, C6, C7, C8 — дублируются в 3 адсетах):
- Адсет 2.1 — текст-хук **Anti-objection** (не нужен большой деп)
- Адсет 2.2 — текст-хук **Adrenaline/FOMO** (Kenya's fastest growing — угол Chicken Dash)
- Адсет 2.3 — текст-хук **Scale/social proof** (тысячи кенийцев, 1M+ — угол Moyo)

После открута сравнивать по **FTD** (трекер, разрез `ext_sub6`), не по кликам.
