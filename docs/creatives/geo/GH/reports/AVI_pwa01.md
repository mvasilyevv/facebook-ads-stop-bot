# PWA GH/Aviator — контент + ассеты (Фаза 2.5)

**Оффер:** GH_AVI (агрегатор, игра-локомотив Aviator) · **гео:** Ghana
**Формат:** Google Play-листинг PWA (каркас как scan2 Aviator/Spribe) + наш trust-слой (MoMo, локальные ганские отзывы, Pidgin)
**Сборка:** ассеты/контент готовлю я → байер вбивает в PWA-билдер. **Девайс/стиль:** Android-листинг, Tier-3 народно (НЕ iPhone, НЕ глянцевый мультяшный персонаж как BIGWINS).

> ⚠️ **PWA = EVERGREEN, БЕЗ БОНУСА.** Бонус/оффер-специфика (суммы депозита, free bets, % match)
> НЕ указываются в PWA — чтобы переиспользовать под разные офферы/бонусы без пересборки.
> **Бонус живёт ТОЛЬКО в FB-креативах** (дёшево пересоздать). PWA — про игру + MoMo-trust (гео, не оффер).
> Разбор референсов: scan1 BIGWINS = промо-агрегатор (греда/фейк-миллиарды/iPhone — НЕ берём).
> scan2 Aviator/Spribe = Play-листинг с локальными отзывами — БЕРЁМ каркас (но без их «20 free flights» бонуса).

---

## 1. App listing (header)
- **Название:** `Aviator`
- **Разработчик:** `Spribe` (оригинальный провайдер — доверие)
- **Иконка:** красный самолёт Aviator на тёмном фоне (как scan2 78.gif) — ассет ICON ниже.
- **Рейтинг:** `4.9 ★` · отзывы `7K+` (правдоподобно, не раздуваем)
- **Installs:** `75,000+` · **Size:** `9.5 MB` · **Age:** `18+` · **No ads**
- **Кнопка:** `Install`

## 2. Главный промо-баннер (под заголовком) — БЕЗ бонуса
> **THE #1 CRASH GAME IN GHANA — CASH OUT TO MTN MoMo! 🔥✈️**

(evergreen: про игру + MoMo-вывод. Бонус/суммы — в FB-креативах, не здесь.)

## 3. Описание (short + full) — evergreen, без сумм/бонуса
**Short:** `Bet, watch the plane fly, cash out to MTN MoMo before it's gone! 💰`

**Full:**
> Aviator is the #1 crash game in Ghana 🇬🇭✈️
>
> Place your bet, watch the multiplier climb, and cash out your wins before the plane flies away! 💸
>
> 📈 Simple, fast gameplay — anyone can play.
> ⚡ Instant withdrawals straight to MTN MoMo & Telecel Cash.
> 🛡️ Fair play, fast payouts, trusted by thousands of Ghanaians.
> 🌍 Play anytime, anywhere on your phone.
>
> Ready to fly? Download Aviator and cash out to your MoMo today! 🛫🏆

## 4. Отзывы (локальные ганцы + MoMo-trust + Pidgin) — НАШ слой
> Реальный соц-пруф под GH. Имена ганские, упор на MoMo-вывод (winner-хук), вкрапления Pidgin.

| Имя | ★ | Дата | Текст |
|---|---|---|---|
| **Kofi Mensah** | ★★★★★ | 18.05.26 | "Chale I no believe at first 😅 Cash out enter my MoMo straight. Withdrawal was sharp sharp! Ɛyɛ 🇬🇭" |
| **Ama Boateng** | ★★★★★ | 12.05.26 | "Best app! My MoMo got the money in 2 minutes. Simple to start and play. Highly recommend 🔥" |
| **Kwame Owusu** | ★★★★ | 06.05.26 | "Simple to play, the plane game is addictive. Cashed out to Telecel Cash with no wahala. Good one." |
| **Yaw Darko** | ★★★★★ | 30.04.26 | "I dey win small small every day. Payout to MTN MoMo is fast pass all. This one na correct app 👌" |
| **Akosua** | ★★★★★ | 22.04.26 | "I play every evening after work. Withdrawals are real, money enters MoMo quick. No stress." |
| **Kojo Asante** | ★★★★ | 15.04.26 | "Fair game, fast cash out. Just catch the multiplier on time and cash out to MoMo 📈" |

## 5. Ассеты для генерации (Sora, по PROMPTING.md)
| Ассет | Размер | Что | Статус |
|---|---|---|---|
| ICON | 1:1 | Красный самолёт Aviator, тёмный фон, без текста | ✅ `ICON.jpg` |
| FEATURE | 3:2 | «AVIATOR» + самолёт + «Cash out to MTN MoMo» + 🇬🇭 | ✅ `FEATURE.jpg` (текст чистый, без бонуса) |
| SCREEN1 | 2:3 | Геймплей: самолёт, «2.45x», CASH OUT, BET-контролы | ✅ `SCREEN1.jpg` (эталон стиля серии) |
| SCREEN2 | 2:3 | MoMo-вывод (trust): галка + «Withdrawal successful» + чистая плашка «MTN MoMo / Payment received» + OK | ✅ `SCREEN2.jpg` (v3: двойное тире исправлено, чёрный full-bleed) |
| SCREEN3 | 2:3 | Lobby Aviator: множители + 2 BET-панеля + 🇬🇭 баланс GHS 3,124 | ✅ `SCREEN3.jpg` (v3: белый кант убран, тёмный full-bleed) |

> **3 скрина в ЕДИНОМ формате (фикс v3, 02.06):** full-bleed тёмный экран приложения (БЕЗ рамки/фона),
> единый Aviator-стиль, ref=SCREEN1. История фиксов: v1 — разнобой (чёрный/белый фон + iPhone на S1);
> v2 — привели к full-bleed, но S3 остался с белым кантом + S2 с двойным тире в плашке; v3 — S2/S3
> перегенерены (S2 чистая MoMo-плашка как trust-акцент на вывод, S3 без белого канта). Старые → `_archive/`.
> Android/Tier-3, **БЕЗ бонуса/депозит-сумм** (evergreen). Формат Sora (1:1/3:2/2:3) → точные размеры
> Play (512²/1024×500/9:16) ресайзит билдер. «GHS 10/5» на скринах = нейтральные дефолт-ставки игры (не бонус).
> Старые v1 скрины — в `_archive/`. Reference стиля — `refs/ref_screen_style.jpg`.

## 6. Трекинг (определяет objective кампании)
- PWA-линк → в кампанию как destination.
- **Если PWA-билдер даёт пиксель/postback** → objective `OUTCOME_SALES`, иначе `OUTCOME_TRAFFIC` на PWA-линк.
- sub-макросы (sub3=code креатива / sub6=angle) прокидываются через URL PWA → AdSet.pro.
- _Финализируем objective AVI_campaign01.md после ответа байера про пиксель PWA._

> Решение байера: ОК на контент/отзывы/баннер? → генерю 5 ассетов в Sora. Что правим в текстах?
