# PWA Build Pack — GH · Chicken Road (2 архетипа)

> Готово к вбиванию в **AdSet.pro → PWA-билдер** (3 шага: Тип PWA / Приложение / Комментарии).
> **EVERGREEN — без бонусов/сумм/free spins** в описании и What's New (бонус живёт только в FB-креативах).
> Суммы выигрышей в отзывах (депозит→кэшаут) — соц-пруф, оставлены.
> Полные тексты, отзывы и план картинок — `CR_pwa01.md` (архетип InOut) / `CR2_pwa01.md` (архетип 1xBet).
> Трекинг-URL/домен и пиксель GH-NEW — байер вписывает при сборке (не здесь).

> **Суть сплит-теста:** две PWA = два РАЗНЫХ реально работающих архетипа (скопированы с конкурентов),
> а не одна в двух цветах. PWA-1 — чистый инди-InOut. PWA-2 — брендовый казино-хайп 1xBet.

---

# ▌PWA-1 — Chicken Road  (архетип InOut: чистый/структурный, тёмная тема)

## Шаг «Приложение» — поля

| Поле билдера | Значение |
|---|---|
| Название (App name) | `Chicken Road` |
| Разработчик (Developer) | `InOut Games` |
| Категория | `Gambling` |
| Оценка (Rating) | `4.9` |
| Кол-во оценок | `1K` |
| Кол-во загрузок | `100K+` |
| Размер (Size) | `12 MB` |
| Возраст (Age) | `18+` |
| Ads / покупки | `No ads, no in-app purchases` |
| Авто-редирект | `Нет` (сохраняем push-подписку) |

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

# ▌PWA-2 — Chicken Road 2  (архетип 1xBet: брендовый эмодзи-хайп, казино-баннеры)

## Шаг «Приложение» — поля

| Поле билдера | Значение |
|---|---|
| Название (App name) | `Chicken Road 2` |
| Разработчик (Developer) | `InOut Games` |
| Категория | `Gambling` |
| Оценка (Rating) | `4.9` |
| Кол-во оценок | `3K` |
| Кол-во загрузок | `500K+` |
| Размер (Size) | `28 MB` |
| Возраст (Age) | `18+` |
| Ads / покупки | `Contains ads` |
| Авто-редирект | `2 мин` (тест push-ретеншн vs прогон) |

## Описание (Short)
```
🐔🔥 The sequel is here! Higher multipliers, bigger GHS runs — cash out to your MTN MoMo before the chicken crashes! 💸
```

## Описание (Full)
```
🐔🔥 Chicken Road 2 is now live in Ghana! 🇬🇭💸
Get ready for the most thrilling crash game in the country and chase massive wins every single day! 🎰💰
📲 Download now – place your bet – cash out before the crash! 🎉

⚡ Fast & secure payments — instant deposits and withdrawals to MTN MoMo, Telecel Cash & AirtelTigo
🎰 Higher multipliers • thrilling gameplay • bigger winning runs than part 1
📱 Play anytime, anywhere, straight from your smartphone
🐔 The sequel to Ghana's favourite crash game — same chicken, hotter grills, bigger cashouts
🛡️ Real, verified payouts — join the players already cashing out across Ghana

🚀 Join Chicken Road 2 today and start your winning run! Are you cashing out or crashing out?
```

## What's New
```
🚀 New high-multiplier rounds added — bigger runs than ever!
🐔 Smoother gameplay and faster MoMo cashouts.
🔥 Chicken Road 2 — the sequel hits harder than part 1.
```

## Промо-баннер
```
🐔🔥 Chicken Road 2 is HERE — Higher Multipliers, Instant MoMo Cashouts! 🇬🇭
```

## Фото → поле билдера  (реальные 1xBet-баннеры БЕЗ их лого; см. `CR2_pwa01.md` §6)
Источник реальных: `~/Downloads/scan_GH_Android/`. Яркие казино-баннеры. **Обязательно снять «1XBET» + апскейл** (превью мелкие). Композицию сохранять, не догенеривать выдуманное.

| Поле билдера | Источник |
|---|---|
| Иконка (App icon) | `c502c3dd…w174h174.png` (курица+множители+флаг) → снять 1XBET + апскейл 500×500 |
| Feature graphic | `ba6f…w0h408.png` (816×408 горизонт: OFFICIAL GAME + PLACE YOUR BET/CASH OUT) → снять 1XBET + апскейл 1008×672 |
| Скриншот 1 | `edaceadc…w0h408.png` (золотые яйца 20.5x/10.2x, флаг) → снять 1XBET + апскейл 500×888 |
| Скриншот 2 | `967d13ab…w0h408.png` (CASH OUT / телефон / курицы) → снять 1XBET + апскейл 500×888 |
| Скриншот 3 | `09c56d8d…w0h408.png` (reels / OFFICIAL GAME) → снять 1XBET + апскейл 500×888 |

> `1174f847` (GAME BONUS) НЕ берём — правило без бонуса.

## Шаг «Комментарии» — отзывы
- Полный текст — `CR2_pwa01.md` §5 (**17 шт.**). Формат `Name,Stars,Date,Review,Developer Reply`.
- Рейтинг-микс: 14×5★ + 2×4★ + 1×3★ (→ ≈4.9). Длинные истории + суммы выигрышей, **ответ team на каждый**.

---

## Различия PWA-1 vs PWA-2 (два архетипа — зачем)
| Ось | PWA-1 (Chicken Road / InOut) | PWA-2 (Chicken Road 2 / 1xBet-стиль) |
|---|---|---|
| Архетип-образец | инди InOut Games | брендовый 1xBet |
| Тон текста | структурный, без эмодзи, «5 причин» | эмодзи в каждой строке, хайп |
| Отзывы | суммы депозит→выигрыш, деловой | длинные истории, эмодзи, ответ team на каждый |
| Картинки | тёмный геймплейный UI | яркие казино-баннеры (PLACE YOUR BET / CASH OUT) |
| Мета | No ads, 100K+, 12 MB | Contains ads, 500K+, 28 MB |
| Авто-редирект | Нет | 2 мин |

> Сплит-тест: два реально работающих подхода целиком (текст+картинки) → видно, какой архетип лучше конвертит install→FTD. Бонусов нет ни в одном (правило evergreen); вся разница — в ПОДАЧЕ.
