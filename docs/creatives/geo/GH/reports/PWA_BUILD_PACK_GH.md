# PWA Build Pack — GH · Chicken Road (2 архетипа)

> Готово к вбиванию в **AdSet.pro → PWA-билдер** (3 шага: Тип PWA / Приложение / Комментарии).
> **EVERGREEN — без бонусов/сумм/free spins** в описании и What's New (бонус живёт только в FB-креативах).
> Суммы выигрышей в отзывах (депозит→кэшаут) — соц-пруф, оставлены.
> Полные тексты, отзывы и план картинок — `CR_pwa01.md` (CR1, тёмный InOut) / `CR2_pwa01.md` (CR2, яркий InOut-хайп).
> Трекинг-URL/домен и пиксель GH-NEW — байер вписывает при сборке (не здесь).

> **Суть сплит-теста:** две PWA = два РАЗНЫХ реально работающих листинга (скопированы с конкурентов),
> а не одна в двух цветах. Оба = реальный InOut: PWA-1 — тёмный чистый CR1, PWA-2 — яркий хайп CR2
> (другая игра + визуал + тон/частота ответов дева).

---

# ▌PWA-1 — Chicken Road  (архетип InOut: чистый/структурный, тёмная тема)

## Шаг «Приложение» — поля

| Поле билдера | Значение |
|---|---|
| Название (App name) | `Chicken Road` |
| Разработчик (Developer) | `InOut Games` |
| Категория | `Gambling` |
| Оценка (Rating) | `4.9` |
| Кол-во оценок | `~250` (ratio 0.25% от 100K installs; 1K было завышено) |
| Кол-во загрузок | `100K+` |
| Размер (Size) | `12 MB` |
| Возраст (Age) | `18+` |
| Ads / покупки | `No ads, no in-app purchases` |
| Авто-редирект | `Нет` (сохраняем push-подписку) |

## Детали приложения (шаг «Приложение» → блок «Детали») — НЕ оставлять дефолты
| Поле | Значение |
|---|---|
| Категория (Play) | `Casino` |
| Версия | `2.4.1` |
| Дата обновления | `12.06.2026` (за пару дней до старта, ≈дата новейшего отзыва; НЕ день-в-день со стартом — иначе «слепили сегодня») |
| Email поддержки | `support@inoutgames.com` (не дефолт `support@app.com`) |
| Адрес разработчика | `InOut Games Ltd, Level 3, Spinola Park, Mrieħel, Malta` (не дефолт «Jan van Wijckstraat 175») |
> Email/адрес — правдоподобные плейсхолдеры под архетип InOut/Malta-iGaming. Реальные контакты байер впишет, если есть. На созданную через API PWA уже залиты.

## Описание (Short)
```
Place your bet, watch the multiplier climb, and cash out to your MTN MoMo before the chicken crashes. Ghana's most trusted crash game.
```

## Описание (Full)  — де-ИИзировано (em-dash 3.19→0/100w, штампы сняты; хуки конкурента оставлены)
```
Chicken Road is Ghana's #1 crash game for players who know how to take a calculated risk and turn their GHS into real cash. Whether you're relaxing after a long day or chasing a quick thrill on your break, Chicken Road lets you grow your money fast.

How it works
Place your bet, watch the chicken run across the grills, and see the multiplier climb: 1.5x, 5x, 20x or higher. But here's the catch. You have to hit CASH OUT before the chicken crashes. Hold for the big payout, or play it safe? The call is yours.

Why Ghanaian players choose Chicken Road:
1) Small bets, big wins. You don't need a huge bankroll. Start with 5 or 10 GHS and watch it climb. One good run is all it takes.
2) Fast payouts. Withdraw straight to your MTN MoMo, Telecel Cash or AirtelTigo wallet. No delays, no excuses.
3) Built for Ghana. Smooth on any local network, from Accra to Tamale. No freezing, no lag.
4) Play anytime. Quick rounds that fit your day, right from your phone.
5) Trusted and secure. Thousands of Ghanaians cash out here every day. Real payouts, every time.

The multiplier is rising. Don't just watch other people cash out, do it yourself. Download Chicken Road, place your bet, and play. Are you cashing out or crashing out?
```

## What's New
```
Enhanced multiplier engine — bigger runs and bigger GHS cashouts.
Smoother transactions — faster MoMo deposits and instant withdrawals.
Bug fixes and a speed boost — no lag on any Ghanaian network.
```

## Промо-баннер
```
Ghana's #1 Crash Game — Cash Out to MTN MoMo Instantly!
```

## Фото → поле билдера  (реальные InOut-ассеты + feature на GPT Image 2; см. `CR_pwa01.md` §6)
Источник реальных: `~/Downloads/scan_GH_Android (1)/`. Вывод: `data/syntx_out/GH_CR_pwa/`. Все ✅.

| Поле билдера | Источник | Файл |
|---|---|---|
| Иконка (App icon) | реальная `6a283a…jpg` → edit_image (banana): checkerboard-фон → solid black, остальное не тронуто | `CR_ICON_final.jpg` |
| Постер (Feature) | GPT Image 2 (референс = иконка): курица+лого + «GHANA #1 CRASH GAME / CASH OUT TO MTN MOMO» + x20, без бонуса. **16:9 1024×576** (слот постера AdSet.pro = 16:9, иначе режет бока) | `CR_FEATURE_final.jpg` |
| Скриншот 1 | реальный геймплей `0x720.png` (люки/множители/CASH OUT 1119 GHS) → 500×871 | `CR_SCREEN1_gameplay_reuse.jpg` |
| Скриншот 2 | реальный WIN-попап `0x720_2.png` (x55.97 +1119.4 GHS) → 500×871 | `CR_SCREEN2_win_reuse.jpg` |
| Скриншот 3 | реальный hero `0x720_1.png` (CHICKEN ROAD + огонь) → 500×869 | `CR_logo_banner_reuse.jpg` |

> Порядок: геймплей → win → hero. Выдуманные экраны (вывод MoMo / лидерборд) НЕ делаем — у InOut таких нет.

## Шаг «Комментарии» — отзывы
- Полный текст — `CR_pwa01.md` §5 (**15 шт.**). Формат `Name,Stars,Date,Review,Developer Reply`.
- Рейтинг-микс: 13×5★ + 2×4★ (→ 4.9). Суммы депозит→выигрыш, **ответ InOut Games на 14/15** (рабочий паттерн InOut).

---

# ▌PWA-2 — Chicken Road 2  (архетип реальный InOut CR2: яркий casino-хайп, чистый текст)

## Шаг «Приложение» — поля

| Поле билдера | Значение |
|---|---|
| Название (App name) | `Chicken Road 2` |
| Разработчик (Developer) | `InOut Games` |
| Категория | `Gambling` |
| Оценка (Rating) | `4.9` |
| Кол-во оценок | `341` (ratio ≈0.27% от 125K; реальный скан 8K — завышено) |
| Кол-во загрузок | `125K+` |
| Размер (Size) | `24 MB` |
| Возраст (Age) | `18+` |
| Ads / покупки | `In-app purchases` |
| Бейдж | `Editor's Choice` (если билдер даёт плашку) |
| Авто-редирект | `2 мин` (тест push-ретеншн vs прогон) |

## Детали приложения — НЕ оставлять дефолты
| Поле | Значение |
|---|---|
| Категория (Play) | `Casino` |
| Версия | `4.75` (реальный скан: «upd 4.75b») |
| Дата обновления | `13.06.2026` (≈дата новейшего отзыва Jun 14; НЕ день старта) |
| Email поддержки | `support@inoutgames.com` |
| Адрес разработчика | `InOut Games Ltd, Level 3, Spinola Park, Mrieħel, Malta` (тот же паблишер, что PWA-1) |

## Описание (Short)
```
Cross the busy road, dodge the traffic, and cash out to your MTN MoMo before the chicken gets caught. Ghana's high-stakes crossing game.
```

## Описание (Full) — тема реального скана «Cross. Risk. Win Big!», evergreen, де-ИИзировано (AI-tells 0/100)
```
Chicken Road 2 - Cross. Risk. Win Big!

Send your chicken across the busy road and your multiplier climbs with every lane it clears. The longer it survives, the more your GHS grows. Push too far and one wrong step takes the lot, so the smart play is knowing when to cash out.

How it works
Place your bet and send the chicken across. Every lane it clears lifts the multiplier: 2x, 5x, 15x and beyond. Cash out before it gets caught and the winnings are yours. Hold for the big one or bank a safe run, your call.

Why players in Ghana keep coming back
Start small and climb fast: drop 5 or 10 GHS, clear a few lanes and watch it grow. Withdraw straight to MTN MoMo, Telecel Cash or AirtelTigo, quick and clean with no stories. It runs smooth on any network from Accra to Tamale and works even on small data, so a quick run fits any break in your day. Thousands of players across Ghana cross and cash out here every day.

The traffic is waiting and the multiplier is climbing. Send the chicken, clear the lanes, and cash out. How far will you push it?
```

## What's New
```
New high-stakes lanes added, push further for bigger multipliers.
Faster MoMo deposits and quicker cashouts.
Smoother crossing, now lighter on data and small networks.
```

## Промо-баннер
```
Cross. Risk. Win Big. Cash Out to MTN MoMo.
```

## Фото → поле билдера  (реальная иконка + одно hero-арт под нарезку; см. `CR2_pwa01.md` §7)
Источник: `~/Downloads/scan_GH_Android/` (реальный InOut CR2). Вывод: `data/syntx_out/GH_CR2_pwa/`. Все ✅.
**Подход:** одно широкое hero (GPT Image 2, референс = реальная иконка) → FEATURE 16:9 + нарезка на 3 среза 9:16 = панорама в карусели (как в образце: свайп 1→2→3 = одна сцена).

| Поле билдера | Источник | Файл |
|---|---|---|
| Иконка (App icon) | реальная `public.avif` (курица в кепке + машина + лого «CHICKEN ROAD 2») → 500×500 | `CR2_ICON_final.jpg` |
| Постер (Feature) | полный hero: курица через дорогу, бейджи 6x/20x, BIG WIN, монеты, лого. **16:9 1024×576** | `CR2_FEATURE_final.jpg` |
| Скриншот 1 | левый срез hero: «6X» + красная машина на дороге + монеты | `CR2_SCREEN1_final.jpg` |
| Скриншот 2 | центр hero: курица + логотип «CHICKEN ROAD» | `CR2_SCREEN2_final.jpg` |
| Скриншот 3 | правый срез hero: «20X» + «BIG WIN» + монеты | `CR2_SCREEN3_final.jpg` |

> Порядок свайпа: 6X/машина → курица/лого → 20X/BIG WIN (= одна сцена). EVERGREEN: множители/«BIG WIN» — игровая подача, без бонуса/сумм-подарков. Чужих лого нет.

## Шаг «Комментарии» — отзывы
- Полный текст — `CR2_pwa01.md` §6 (**16 шт.**). CSV: `data/syntx_out/GH_CR2_pwa/CR2_reviews_GH-EN.csv` (формат AdSet.pro).
- Рейтинг-микс: 11×5★ + 2×4★ + 2×2★ + 1×1★; **ответ дева на 9/16** (все 3 негатива + 6 позитивов); headline 4.9 / 341. Прогнано через панель syntx (2 прохода).

---

## Различия PWA-1 vs PWA-2 (две реальные игры InOut — зачем)
| Ось | PWA-1 (Chicken Road / InOut) | PWA-2 (Chicken Road 2 / InOut CR2) |
|---|---|---|
| Образец | реальный InOut CR1 (тёмный/чистый) | реальный InOut CR2 (яркий casino-хайп) |
| Тон текста | структурный, «5 причин», без эмодзи | чистый «Cross. Risk. Win Big», без эмодзи |
| Отзывы | суммы депозит→выигрыш, тёплый дев-ответ почти на каждый | разброс/половина без сумм, сдержанный дев-ответ выборочно (9/16) |
| Картинки | тёмный реальный геймплейный UI | яркое hero-арт, нарезанное на панораму (3 среза) |
| Мета | No ads, 100K+, 12 MB | In-app purchases, 125K+, 24 MB |
| Авто-редирект | Нет | 2 мин |

> Сплит-тест: два реально работающих листинга целиком (текст+картинки) → видно, какой лучше конвертит install→FTD. Бонусов нет ни в одном (правило evergreen); разница — в ИГРЕ + ПОДАЧЕ.
