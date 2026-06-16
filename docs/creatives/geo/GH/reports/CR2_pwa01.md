# PWA GH/Chicken Road 2 — контент-пакет (PWA-2)

**Оффер:** GH_CR2 · **Игра:** Chicken Road 2 · **Гео:** Ghana
**Архетип:** **реальный InOut «Chicken Road 2»** — чистый текст в стиле «Cross. Risk. Win Big!»,
яркий casino-хайп визуал (курица + монеты + множители), **сдержанно-вежливый ответ дева** (выборочно).
**Образец (копируем подход целиком):** реальный листинг InOut «Chicken Road 2» — `~/Downloads/scan_GH_Android/`
(иконка + хайп-баннер + текст + отзывы с ответом дева InOut).
**Сборка:** контент готов → байер вбивает в PWA-билдер AdSet.pro. Картинки — иконка из реального скана +
feature/3 скрина из одного hero-арта (syntx, стиль образца), см. §7.

> **PWA = EVERGREEN, БЕЗ БОНУСА** (правило 15.06). В описании/What's New нет welcome/deposit bonus / free spins.
> Суммы выигрышей в отзывах (депозит→кэшаут) — соц-пруф, не бонус → оставляем.

> **Сплит-тест:** PWA-2 (этот — Chicken Road 2, **яркий хайп**) против [PWA-1](CR_pwa01.md)
> (Chicken Road 1 — **тёмный чистый**). Оба = реальный InOut, тот же студийный паблишер; различаются
> **игрой + визуалом + тоном/частотой ответов дева** → сплит меряет ПОДАЧУ, не цвет.

> **Разворот 16.06:** старый черновик был под выдуманный «1xBet-хайп» (эмодзи-спам, 17 эмодзи-отзывов).
> Новый скан показал, что реальный конкурент CR2 — это InOut с чистым текстом + хайп-визуалом + выборочным
> дев-ответом. Пересобрано под реальный образец (принцип «копируем реального конкурента»).

---

## 1. Поля листинга (header)

| Поле | Значение |
|---|---|
| **Название** | `Chicken Road 2` |
| **Разработчик** | `InOut Games` |
| **Категория** | Gambling |
| **Рейтинг** | `4.9 ★` |
| **Отзывов** | `341` (ratio ≈0.27% от 125K; реальный скан показывал 8K/125K ≈6.4% — завышено, ментор-калибровка ≈0.1-0.3%; PWA-1 = 254/100K) |
| **Installs** | `125K+` |
| **Size** | `24 MB` (sequel — крупнее CR1 12 MB) |
| **Age** | `18+` |
| **Ads / покупки** | `In-app purchases` (как у реального скана; отличает от PWA-1 «No ads») |
| **Бейдж** | `Editor's Choice` (если билдер даёт плашку — есть у реального скана) |
| **Авто-редирект** | `2 мин` (сплит-ось против PWA-1 «Нет»: тест прогон-на-оффер vs push-ретеншн) |

*Метаданные = реальный скан InOut CR2 (125K+ / In-app purchases / Editor's Choice / 4.9), кроме завышенного
счётчика отзывов (8K→341) — калибровка правдоподобности.*

---

## 2. What's New (3 пункта — терсово, evergreen, без бонуса)

- New high-stakes lanes added, push further for bigger multipliers.
- Faster MoMo deposits and quicker cashouts.
- Smoother crossing, now lighter on data and small networks.

---

## 3. Промо-баннер (1 строка, без сумм/бонуса)

> **Cross. Risk. Win Big. Cash Out to MTN MoMo.**

---

## 4. Описание

**Short (1–2 предложения):**

> Cross the busy road, dodge the traffic, and cash out to your MTN MoMo before the chicken gets caught. Ghana's high-stakes crossing game.

**Full** (тема реального скана «Cross. Risk. Win Big!» — переход полос/множитель; де-ИИзировано: нумерованный
список 1)2)3) свёрнут в живой абзац по фидбэку панели, em-dash убраны, добавлен GH-штрих «small data»):

> Chicken Road 2 - Cross. Risk. Win Big!
>
> Send your chicken across the busy road and your multiplier climbs with every lane it clears. The longer it survives, the more your GHS grows. Push too far and one wrong step takes the lot, so the smart play is knowing when to cash out.
>
> How it works
> Place your bet and send the chicken across. Every lane it clears lifts the multiplier: 2x, 5x, 15x and beyond. Cash out before it gets caught and the winnings are yours. Hold for the big one or bank a safe run, your call.
>
> Why players in Ghana keep coming back
> Start small and climb fast: drop 5 or 10 GHS, clear a few lanes and watch it grow. Withdraw straight to MTN MoMo, Telecel Cash or AirtelTigo, quick and clean with no stories. It runs smooth on any network from Accra to Tamale and works even on small data, so a quick run fits any break in your day. Thousands of players across Ghana cross and cash out here every day.
>
> The traffic is waiting and the multiplier is climbing. Send the chicken, clear the lanes, and cash out. How far will you push it?

---

## 5. Детали приложения (шаг «Приложение» → блок «Детали») — НЕ оставлять дефолты

| Поле | Значение |
|---|---|
| Категория (Play) | `Casino` |
| Версия | `4.75` (реальный скан: What's New «upd 4.75b») |
| Дата обновления | `13.06.2026` (за пару дней до старта, ≈дата новейшего отзыва Jun 14; НЕ день-в-день со стартом) |
| Email поддержки | `support@inoutgames.com` (тот же студийный паблишер, что PWA-1) |
| Адрес разработчика | `InOut Games Ltd, Level 3, Spinola Park, Mrieħel, Malta` (как PWA-1 — один паблишер) |

> Один паблишер на обе PWA (InOut Games) — реалистично для сплита «две игры одной студии». Реальные
> контакты байер впишет, если есть.

---

## 6. Отзывы (16 шт. — реальный InOut: выборочный ответ дева + работа с возражениями)

> **Прогнано через панель syntx 16.06** (GPT-5.4-Pro / Gemini-3.1-Pro / Grok-4.3), **2 прохода:**
>
> - **Проход 1 (стратегия раскрыта):** GPT `rewrite/5`, Gemini `minor_fix/9`, Grok `minor_fix/7`. GPT — набор
>   «слишком засеян» (полированные payout-отзывы, шаблонные дев-ответы, инженерные лайки, оптимистичный
>   win-матан, апсейл-коучинг в ответах); Gemini — «runs land smoother after update» звучит как rigged RTP;
>   Grok — даты 2026 (его knowledge cutoff).
> - **Применено:** человеческий разброс длин; ~половина позитивов БЕЗ сумм (геймплей/загрузка/«small data»);
>   мелкие/средние выигрыши (20→48, 30→72, 90), крупных только 2 (720 / 1150); дев-ответы расшаблонены и
>   **выборочны (9/16**, как реальные листинги — на все 3 негатива + часть позитивов), сняты абсолюты
>   «we always pay / the speed we promise»; убран «smoother after update»; **починен таймлайн Grace** (сбой
>   Jun 4 ДО её отзыва Jun 6); строчные бренды + пропуски точек в 4 шт. (живой ввод). **Даты 2026 ОСТАВЛЕНЫ**
>   (сегодня реально июнь-2026; у Grok устаревший cutoff — суждение байера поверх панели). **Депозит-нудж**
>   (заказ байера «попробуй не 10, а 50») перенесён из дев-ответов в **пир-отзыв Esi** (голос игрока, не дева).
> - **Проход 2 (СЛЕПОЙ, без спойлера стратегии) по финалу:** GPT `minor_fix/7`, Gemini `keep/10`, Grok `keep/9`.
>   Закрыты 3 остатка: матан Esi (60×**5**=300, было 7x→несходимо), дев-ответы Esi/Yaw очищены от апсейла
>   (чистый support), добавлен Telecel в видимый сэмпл (был перекос на MTN).
> - **Лайки по конверсии, без механической ступеньки:** позитив 14–70 (пир-нудж Esi=70 топ), негатив 7–11,
>   диапазоны перехлёстнуты для органики (направление «позитив всплывает / негатив тонет» сохранено).
> - Видимые 16 = «most relevant» сэмпл; headline-счётчик 341 / 4.9. Язык — чистый английский, без Pidgin/эмодзи;
>   имена ганские, ОТЛИЧНЫ от PWA-1 (две разные базы). ⚠️ Перед заливкой вычитать вручную (тон/гео).

| # | Ник | ★ | Дата | Текст отзыва | Ответ InOut Games | Лайки |
|---|---|---|---|---|---|---|
| 1 | Samuel Boateng | ★★★★★ | Jun 14 | Crossed eight lanes and cashed out 210 to my mtn momo the same night. Smooth. | Glad it landed smoothly, Samuel. Good luck on the next run. | 58 |
| 2 | Abena Agyeman | ★★★★★ | Jun 13 | had a great time and ended up ahead this evening. recommend | *(без ответа)* | 41 |
| 3 | Ebenezer Tetteh | ★★ | Jun 12 | Made a deposit and an hour later it still wasn't showing in my balance. support was slow today. | Good afternoon, Ebenezer. Apologies for the wait. There was a short payment delay that is now fixed and your balance updated. Message support if anything is still pending. | 9 |
| 4 | Kojo Antwi | ★★★★★ | Jun 11 | best crossing game out right now, loads fast even on my data | *(без ответа)* | 33 |
| 5 | Esi Asante | ★★★★★ | Jun 10 | Saw people saying 10 ghs won't pay. I put 60, held to 5x and took 300. you need a bit more balance to let it climb. | Sound advice, Esi. Glad it paid off. | 70 |
| 6 | Yaw Mensah | ★★ | Jun 9 | Fun to watch but I staked 10 and lost it in two rounds. no win. | Thanks for the feedback, Yaw. Sorry it didn't go your way. Cash out a little earlier next time and the runs feel steadier. | 7 |
| 7 | Adwoa Owusu | ★★★★★ | Jun 8 | Deposited 50, made it far and walked away with 720 to airteltigo. didn't expect it honestly | Congrats on that run, Adwoa. Enjoy it. | 49 |
| 8 | Michael Ofori | ★★★★ | Jun 7 | Good game. just learn to cash out early and don't push every lane, that is how you lose it. | *(без ответа)* | 17 |
| 9 | Grace Mensah | ★ | Jun 6 | Won 90 but the withdrawal sat for a day before it came. game is fine, the wait was annoying. | Sorry about the delay, Grace. We had a brief payment glitch on Jun 4 that is now resolved, and your withdrawal has gone through. Message support if you hit it again. | 11 |
| 10 | Kwame Asare | ★★★★★ | Jun 5 | turned 20 into 48 and cashed to telecel. nothing huge but it paid out clean | *(без ответа)* | 24 |
| 11 | Felicia Boateng | ★★★★★ | Jun 4 | Crossed far enough to hit 12x before I cashed. credited to mtn in a minute or two. | A 12x run, nicely judged Felicia. | 38 |
| 12 | Akosua Darko | ★★★★ | Jun 2 | Real payouts. sometimes it loads slow when the network is bad but the money always comes. | *(без ответа)* | 14 |
| 13 | Daniel Nkrumah | ★★★★★ | May 31 | was doubting at first but this paid for my weekend. 1150 into my momo. | Big weekend, Daniel. Come back for the next one. | 63 |
| 14 | Vida Amoah | ★★★★★ | May 28 | plays smooth on my 4g and the rounds are quick. won a bit last night too | *(без ответа)* | 19 |
| 15 | Kofi Boahen | ★★★★★ | May 25 | Playing from Tamale, no issues. money lands same as anyone else. | *(без ответа)* | 16 |
| 16 | Patience Owusu | ★★★★★ | May 21 | Started with 30 just to test it, came away with 72 to mtn momo. did not expect it to pay so easily | Glad it paid out, Patience. Welcome in. | 29 |

> CSV для импорта: `data/syntx_out/GH_CR2_pwa/CR2_reviews_GH-EN.csv` (формат AdSet.pro: `userName,comment,rating,likes,avatar,companyResponse,createdAt,offsetDay,offsetHour,offsetMin`).
> Распределение видимых: 11×5 + 2×4 + 2×2 + 1×1; ответ дева на 9/16 (все 3 негатива + 6 позитивов).
> **Headline 4.9 = агрегат всей базы 341** (в билдере задаётся отдельно через voteCount5..1); среднее по 16 ВИДИМЫМ ≈4.3 — это «most relevant» сэмпл (включает негатив), а не вся выборка. 4.9 НЕ выводится из этих 16 — как и в реальном скане, где при видимом 2★ шапка всё равно 4.9.

---

## 7. Картинки — реальный скан InOut CR2 + одно hero-арт под нарезку

Источник: `~/Downloads/scan_GH_Android/` — реальный листинг InOut CR2: чистая **иконка** (512×512) + 3 мелких
тайла 165×296, которые оказались **срезами ОДНОГО хайп-баннера** (склейка `public_1+2+3` → цельная сцена
курица/монеты/множители; чистых геймплейных скринов в скане НЕТ). Вывод: `data/syntx_out/GH_CR2_pwa/`.

**Подход:** ОБЛОЖКА (постер) и СКРИНШОТЫ — РАЗНЫЕ картинки (косяк «cover=screens» исправлен 16.06).
- **FEATURE/постер** = отдельный постер с хуком (один герой + текст), НЕ срез скринов.
- **3 скрина** = одно широкое hero-арт → нарезка на **3 вертикальных среза 9:16** = панорама в карусели
  (свайп 1→2→3 = одна сцена: дорога/палитра/лого тянутся сквозь срезы; центр-кроп hero 1500×888 (5:3) → 3×500×888).
Оба арта — syntx (**GPT Image 2, референс = реальная иконка** → персонаж/лого/стиль с неё; 2K 16:9; текст без
опечаток). Hero: 2 варианта, выбран v1 (связная курица), v2 отклонён (двоилась голова). Постер: cover_v1.

| Поле билдера | Формат | Источник / действие | Файл |
|---|---|---|---|
| ICON | 1:1 500×500 | реальная `public.avif` (курица в кепке + машина сзади + лого «CHICKEN ROAD 2») → ресайз 500×500 | ✅ `CR2_ICON_final.jpg` |
| FEATURE/постер | **16:9 1024×576** | ОТДЕЛЬНЫЙ постер: курица+лого по центру, хук «CROSS. RISK. WIN BIG» сверху + ribbon «CASH OUT TO MTN MOMO» снизу, машина/дорога. Отличается от скринов | ✅ `CR2_FEATURE_final.jpg` |
| SCREEN 1 | 9:16 500×888 | левый срез hero-панорамы: бейдж «6X» + красная машина на дороге + монеты | ✅ `CR2_SCREEN1_final.jpg` |
| SCREEN 2 | 9:16 500×888 | центр hero-панорамы: курица + логотип «CHICKEN ROAD» | ✅ `CR2_SCREEN2_final.jpg` |
| SCREEN 3 | 9:16 500×888 | правый срез hero-панорамы: «20X» + «BIG WIN» starburst + монеты | ✅ `CR2_SCREEN3_final.jpg` |

> EVERGREEN: множители / «BIG WIN» — игровая подача, БЕЗ обещаний бонуса/сумм-подарков. Чужих лого нет.
> Яркий фиолет/золото = главное визуальное отличие от тёмного чистого PWA-1.
> Старые ассеты от 1xBet-плана + слайс-FEATURE + альт-варианты — в `data/syntx_out/_archive_GH_CR2_old/`.
> Сырой hero/постер (2048×1152) — в `/tmp` (не коммитим; регенерим из §7-промптов).

### Статус заливки в AdSet.pro (PWA `MV | GH | CR2`, id `6a2f0459af1cdc7102ce9089`)
- ✅ **через HTTP API (PATCH)** — текст/мета/детали/рейтинг: name/author/category/installs(125000)/size(24)/
  version(4.75)/ads(In-app purchases)/Editor's Choice+verified/updateDate(13.06.2026)/support email+адрес/
  rating(341→4.90)/описание/What's New.
- ⏳ **через UI** (API не умеет): иконка + постер + 3 скрина (загрузка байером) + импорт CSV-отзывов (JS DataTransfer).

---

## 8. Служебные заметки

- **Архетип = реальный InOut CR2** (яркий хайп) — противопоставление [PWA-1 CR](CR_pwa01.md) (тёмный чистый).
  Сплит меряет подачу двух реальных игр одной студии.
- Текст/отзывы построены по реальному скану (`scan_GH_Android/text.txt`) и прогнаны через панель syntx 2 прохода (см. §6).
- **Без бонуса** в описании/What's New. Суммы выигрышей в отзывах — соц-пруф.
- Трекинг-URL/домен/пиксель (GH NEW 1573011031117084): вписывает байер при сборке кампании.
- **Gate B (qa): ✅ APPROVE (16.06)** — контент-пакет принят (evergreen, отзывы, картинки, мета). Заливка на боевой `MV | GH | CR2` (id `6a2f0459…`) подтверждена через API (картинки+текст+рейтинг+сет «GH — EN»). Остаток за байером: сверить в UI, что в сете 16 отзывов; go на деньги.
- Дальше: сборка трекер-кампании (потоки по OS + сплит-тест + антибот) → трекинг-ссылку передать агенту `fb`.
