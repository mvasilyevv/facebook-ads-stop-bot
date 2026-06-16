# PWA GH/Chicken Road — контент-пакет (PWA-1)

**Оффер:** GH_CR · **Игра:** Chicken Road · **Гео:** Ghana
**Архетип:** **InOut Games** — чистый/структурный листинг, инди-вайб, без эмодзи-перегруза, упор на MoMo-trust и конкретные кэшауты.
**Образец (копируем подход):** реальный листинг InOut Games «Chicken Road» — `~/Downloads/scan_GH_Android (1)/` (текст + скриншоты + аватары).
**Сборка:** контент готов → байер вбивает в PWA-билдер AdSet.pro. Картинки — гибрид (реальные ассеты InOut + докрутка), см. §6.

> **PWA = EVERGREEN, БЕЗ БОНУСА** (правило подтверждено 15.06).
> В **описании и What's New** НЕ зашивать welcome bonus / free spins / deposit bonus.
> Суммы **выигрышей в отзывах** (депозит→кэшаут) — это соц-пруф, не бонус → оставляем (как в образце).
> Бонус живёт только в FB-креативах.

> **Сплит-тест:** PWA-1 (этот, архетип InOut — чистый) против [PWA-2](CR2_pwa01.md) (архетип 1xBet — брендовый хайп). Тестируем ПОДАЧУ, не цвет.

---

## 1. Поля листинга (header)

| Поле | Значение |
|---|---|
| **Название** | `Chicken Road` |
| **Разработчик** | `InOut Games` |
| **Категория** | Gambling |
| **Рейтинг** | `4.9 ★` |
| **Отзывов** | `~250` (ratio 0.25% от installs; 1K было завышено — ментор-калибровка ≈0.1-0.3%, см. вариант A 16.06) |
| **Installs** | `100K+` |
| **Size** | `12 MB` |
| **Age** | `18+` |
| **Ads** | No ads, no in-app purchases |

*Метаданные = как у образца InOut (No ads / 100K+ / 12 MB / 4.9) — копируем рабочее.*

---

## 2. What's New (3 пункта — деловой тон, без бонуса)

- Enhanced multiplier engine — bigger runs and bigger GHS cashouts.
- Smoother transactions — faster MoMo deposits and instant withdrawals.
- Bug fixes and a speed boost — no lag on any Ghanaian network.

---

## 3. Промо-баннер (1 строка, без сумм/бонуса)

> **Ghana's #1 Crash Game — Cash Out to MTN MoMo Instantly!**

---

## 4. Описание

**Short (1–2 предложения):**

> Place your bet, watch the multiplier climb, and cash out to your MTN MoMo before the chicken crashes. Ghana's most trusted crash game.

**Full (evergreen, без бонуса; де-ИИзировано: убраны em-dash-перебор и штампы, рабочие хуки конкурента оставлены):**

> Chicken Road is Ghana's #1 crash game for players who know how to take a calculated risk and turn their GHS into real cash. Whether you're relaxing after a long day or chasing a quick thrill on your break, Chicken Road lets you grow your money fast.
>
> How it works
> Place your bet, watch the chicken run across the grills, and see the multiplier climb: 1.5x, 5x, 20x or higher. But here's the catch. You have to hit CASH OUT before the chicken crashes. Hold for the big payout, or play it safe? The call is yours.
>
> Why Ghanaian players choose Chicken Road:
> 1) Small bets, big wins. You don't need a huge bankroll. Start with 5 or 10 GHS and watch it climb. One good run is all it takes.
> 2) Fast payouts. Withdraw straight to your MTN MoMo, Telecel Cash or AirtelTigo wallet. No delays, no excuses.
> 3) Built for Ghana. Smooth on any local network, from Accra to Tamale. No freezing, no lag.
> 4) Play anytime. Quick rounds that fit your day, right from your phone.
> 5) Trusted and secure. Thousands of Ghanaians cash out here every day. Real payouts, every time.
>
> The multiplier is rising. Don't just watch other people cash out, do it yourself. Download Chicken Road, place your bet, and play. Are you cashing out or crashing out?

---

## 5. Отзывы (16 шт. — с РАБОТОЙ С ВОЗРАЖЕНИЯМИ: негативы + закрытие + нудж на депозит)

> Правило (pwa-tracker.md §96): отзывы должны **закрывать возражения**, не быть сплошь 5★ (палится).
> **Доработано 16.06 (панель Grok-4.3 + ревью):** добавлены **3 негатива (1-2★)** с реальными жалобами
> и **объекшн-хэндлингом** — (а) дев-ответ defuse'ит («сбой 8.06 устранён, перезапросите вывод»),
> (б) дев на «не выигрываю с 10 GHS» нуджит **больший депозит** («попробуй 40-50, кэшаут с 3-5x»),
> (в) пир-отзыв (Akua) реframe'ит возражение и пушит депозит («с 10 не идёт — закинул 60, снял 480»).
> **Лайки — по конверсии (не по «реализму апвоутов»!):** 5★ высокие (17–91, позитив всплывает наверх в «most relevant»), пир-нудж Akua = топ (91), 4★ ~14–19, **негативы 1-2★ — минимум (6–9, чтобы тонули и НЕ усиливали возражение)**. Высокий лайк на негативе = own-goal (он всплывёт и закрепит «деньги застряли»). Распределение видимых: 11×5 + 2×4 + 2×2 + 1×1.
> Агрегат остаётся 4.9 (254 — это headline-счётчик, видимые 16 = «most relevant» сэмпл, как в Google Play).
> Имена микс муж/жен, лёгкий Pidgin, де-ИИзировано. ⚠️ Перед заливкой — вычитать вручную (тон/гео).

| # | Ник | ★ | Дата | Текст отзыва | Ответ InOut Games |
|---|---|---|---|---|---|
| 1 | Kwame Mensah | ★★★★★ | Jun 10, 2026 | Deposited 50 GHS and cashed out 800 to my MTN MoMo the same evening. This game actually pays. | Glad the cashout was smooth, Kwame. |
| 2 | Yaw Darko | ★★ | Jun 9, 2026 | Colourful and fun, but I put 10 GHS and lost it fast. No win for me. | Crash eats small stakes quick, Yaw — try 40-50 GHS and cash out around 3-5x, payouts run smoother after the last update. |
| 3 | Kojo Osei | ★★★★★ | Jun 8, 2026 | Best crash game I've played in Ghana. Smooth and the withdrawals come through. | Appreciate it, Kojo. Reliable payouts are the priority. |
| 4 | Akua Sarpong | ★★★★★ | Jun 8, 2026 | Saw people say you can't win with 10 GHS. I put 60 instead, caught 8x and cashed 480 to MTN. Small money just won't pay here. | — |
| 5 | Emmanuel Tetteh | ★ | Jun 7, 2026 | Won 180 GHS but the withdrawal didn't move for two days. App is fine but my money was stuck. | That was a payment glitch on Jun 8, fixed the same day — please retry your withdrawal now, it'll hit your wallet in minutes. |
| 6 | Kofi Appiah | ★★★★★ | Jun 6, 2026 | Turned my last 20 GHS into 350 today. Telecel Cash withdrawal was quick. | Nice run, Kofi. Small start, solid cashout. |
| 7 | Ama Boaten | ★★★★★ | Jun 5, 2026 | Cashed out three times today, MTN MoMo each time, no wahala. Easy to use. | Love the consistency, Ama. |
| 8 | Daniel Boadu | ★★ | Jun 4, 2026 | Graphics are sweet but the cash out button froze after two wins. Annoying. | That button issue was sorted on Jun 10 — log in and try again, cashouts are moving fine now. |
| 9 | Akosua Owusu | ★★★★★ | Jun 3, 2026 | Deposited 30 GHS, walked away with 410. Telecel was instant. Honestly impressed. | Fantastic, Akosua. |
| 10 | Kwabena Frimpong | ★★★★ | Jun 2, 2026 | Good game and the payouts come. Just learn to cash out early and don't chase 50x. | Wise advice, Kwabena. Discipline wins. |
| 11 | Kwaku Owusu | ★★★★★ | Jun 1, 2026 | Was doubting at first but this paid for my weekend. 1,200 GHS into my AirtelTigo. | We always pay our winners, Kwaku. Enjoy. |
| 12 | Adwoa Sarpong | ★★★★★ | May 31, 2026 | Clean app, no ads. Won 600 GHS last night and cashed straight to MoMo. | Clean and fast by design. Thanks, Adwoa. |
| 13 | Prince Asare | ★★★★★ | May 29, 2026 | Hit 18x this morning before it crashed. MoMo credited in under a minute. | 18x, steady hands, Prince. |
| 14 | Nana Kweku | ★★★★★ | May 27, 2026 | I dey play from Kumasi, money enters same time. This one pay. | Kumasi cashing out. Thank you, Nana. |
| 15 | Gifty Mensah | ★★★★ | May 24, 2026 | Genuine game. Sometimes you lose a round, that na the risk, but I'm up this week. | Honest and fair, Gifty. Cash out steady. |
| 16 | Comfort Asante | ★★★★★ | May 20, 2026 | Started with 40 GHS, cashed out 520 to MTN MoMo. Didn't expect it to actually pay. | It pays, Comfort. Welcome aboard. |

---

## 6. Картинки — реальные ассеты InOut (переиспользование) + feature на GPT Image 2

Решение 15.06: **реальные ассеты InOut как есть** (ресайз) — у них нет чужого бренда, это рабочий настоящий UI, его не перегенеришь лучше. Сгенерён только недостающий горизонтальный feature-баннер (GPT Image 2, референс = реальная иконка → персонаж и лого «CHICKEN ROAD» взяты прямо с неё, текст не поехал). Источник реальных — `~/Downloads/scan_GH_Android (1)/`. Вывод — `data/syntx_out/GH_CR_pwa/`.

| Поле билдера | Формат | Источник / действие | Файл |
|---|---|---|---|
| ICON | 1:1 500×500 | реальная `6a283a….jpg` → edit_image (banana): checkerboard-фон → solid black (читался как «битый PNG»), курица/лого/монеты не тронуты | ✅ `CR_ICON_final.jpg` |
| FEATURE/постер | **16:9 1024×576** | GPT Image 2 (референс = иконка): курица+лого+монеты слева, хук «GHANA #1 CRASH GAME» / «CASH OUT TO MTN MOMO» справа, бейдж x20, **без бонуса**. Постер AdSet.pro = слот 16:9 → холст расширен чёрным до 16:9 (иначе слот режет бока текста) | ✅ `CR_FEATURE_final.jpg` |
| SCREEN 1 | 2:3 500×871 | реальный геймплей `0x720.png` (множители 55/94/172x, люки, CASH OUT 1119 GHS, ставки) → ресайз | ✅ `CR_SCREEN1_gameplay_reuse.jpg` |
| SCREEN 2 | 2:3 500×871 | реальный WIN-попап `0x720_2.png` (x55.97 +1119.4 GHS) → ресайз | ✅ `CR_SCREEN2_win_reuse.jpg` |
| SCREEN 3 | 2:3 500×869 | реальный hero `0x720_1.png` (CHICKEN ROAD + курица + огонь) → ресайз | ✅ `CR_logo_banner_reuse.jpg` |

> Порядок скринов в листинге: геймплей → win → hero. **Выдуманные экраны** («вывод MoMo», «лидерборд») НЕ делаем — у InOut таких нет.

---

## 7. Служебные заметки

- **Архетип InOut (чистый/структурный)** — противопоставление [PWA-2 CR2](CR2_pwa01.md) (1xBet — брендовый эмодзи-хайп). Тестируем подачу одной механики.
- Текст и отзывы построены по реальному образцу InOut (`scan_GH_Android (1)/text.txt`): структура «5 причин», суммы депозит→выигрыш, ответ дева почти на каждый отзыв.
- **Без бонуса** в описании/What's New (правило 15.06). Суммы выигрышей в отзывах — соц-пруф, оставлены.
- Трекинг-URL/домен: НЕ вписан (определяет байер при сборке в AdSet.pro).
- Отзывов 16 шт. с работой с возражениями (11×5 + 2×4 + 2×2 + 1×1): 3 негатива + объекшн-хэндлинг + пир-нудж на депозит (см. §5). Агрегат-счётчик 254 / 4.9. CSV: `data/syntx_out/GH_CR_pwa/CR_reviews_GH-EN.csv`.
- После ✅ qa → трекинг-ссылку передать агенту `fb`.
