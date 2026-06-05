# План видео-серии batch01 — GH_AVI (Гана, Aviator)

> ⚠️ **AMENDMENT 2026-06-05 (решение байера, после генерации VID003 v1-v3):**
> 1. **Clean-video:** блоки OVERLAYS из промтов при генерации УБИРАТЬ — текст накладываем
>    детерминированно `scripts/overlay_video.py` ПОСЛЕ генерации (см. `video-gen.md` §Clean-video).
>    Спека оверлеев VID003: `--top "I deposit just GHS 10" --banner "GHS 10 -> 20 FREE BETS on Aviator"`
>    (MoMo-карточка остаётся в кадре из статика-источника — она не оверлей).
>    Уже сгенерённые v1-v3 (с burned-in текстом из промта) — кандидаты как есть; новые варианты — clean.
> 2. **Цены:** по факту UI syntx цена генерации = число на кнопке отправки (Veo Omni Flash 1080p =
>    **28 кр/ролик**, списания подтверждены). Бюджетный блок ниже пересчитать при подтверждении
>    цены Kling на UI (Kling-этап). Семантика «2595/ген» из ревизии 2 — неверна, игнорировать.
> 3. Уроки UI syntx (Enter=отправка, лимит 1 активная Veo) — в `video-gen.md` §Доступ.

> Агент `video`, 2026-06-04. Деливерабл для lead (генерация в syntx по этому плану).
> Вводные байера: 3 ФИНАЛЬНЫХ ролика, 2-3 варианта на концепт, серия = 3 РАЗНЫХ сигнала.
> Welcome-bonus: «Депозит 10 GHS → 20 free bets на Aviator». I2V-first от статиков batch01.
> Источники: `video-gen.md` (playbook), `AVI_video_research.md` (разведка 2026-06-04),
> `market-profile/GH.md`, `slots/AVI.yaml`, раскадровка 3 mp4 Bang (38 кадров, outputs/bang_frames).
> **Статус статик-источников (допущение):** CR004/CR005 (входы I2V) в `AVI.yaml` имеют
> `verdict: testing` — это статики batch01, прошедшие приёмку байера при заливе; формального лейбла
> «Gate A» у них нет. План опирается на это допущение; если байер снимет какой-то статик с теста
> до старта генерации — заменить вход (CR005 → CR002 уже предусмотрено в VID003).

---

## Сводка раскадровки референсов Bang (что копируем)

Раскадровано 3 ролика Bang-0911 (скейлер нашего типа, app-прокладка Google Play):

| Ролик | Длина | Драматургия | Ключевые цифры |
|---|---|---|---|
| `bang0911_1359300286119971` | 19.7с | ставки 600+50 → рост 1.84x→3.59x→6.91x → **FLEW AWAY 15.75x** + Pidgin-каллаут «Oh challe, greed kill me, like I for cash out long time 😭» | баланс GHS 74,641.50; Cash Out растёт синхронно (600→4,146 GHS) |
| `bang0911_766856263088236` | 32.1с | долгий полёт 1.20x→3.16x→8.79x→23.62x→63.43x → **FLEW AWAY 151.77x**; счётчик игроков тает 316→25 | баланс 316,402→318,097 GHS (вырос после раннего кэшаута) |
| `bang0911_983683397917901` | 23.6с | ставки 600+300 → 1.13x→3.04x→8.44x→23.04x → **FLEW AWAY 35.29x**, но баланс ВЫРОС 166,400→194,855 — игрок успел кэшаутнуть | Cash Out синхронен математике: 600×8.44=5,064 ✓ |

**Инварианты паттерна Bang (проверены их деньгами):**
- Чистый UI-скринрекорд 9:16, никаких людей — звуконезависимый скролл-стоппер.
- Хук 0-3с = крупный растущий множитель по центру + жирный баланс GHS в шапке.
- История множителей сверху (соц-пруф «большие иксы случаются»), счётчик live-игроков.
- ДВЕ одновременные ставки — суммы Cash Out растут синхронно с множителем (математика сходится).
- Финал = FLEW AWAY (краш) ИЛИ успешный кэшаут с ростом баланса. Pidgin-каллаут как эмо-крючок.
- Суммы balance 75K–318K GHS — у Bang это работает, но для НАШЕГО low-entry оффера (10 GHS) шапку
  держим скромнее (5K–8K GHS): синхрон с угла «маленький депозит» и с правилом сумм GH-профиля
  (выигрыши per-round GHS 1,500–6,000).

---

## Серия: 3 концепта = 3 разных сигнала

| Код | Сигнал | Паттерн | Модель | Вариантов | Кредиты |
|---|---|---|---|---|---|
| VID001 | адреналин краш-механики (gameplay) | Геймплей-экран (Bang) | Kling v3.0 Keyframes, 2 сегмента | 2 | ~138 кр |
| VID002 | payment-trust: MoMo-зачисление (proof) | Геймплей→MoMo-пруф (Bang + luckstrategy UGC) | Kling v3.0 Keyframes | 3 | ~104 кр |
| VID003 | low-entry + эмоция: 10 GHS → выигрыш (human) | UGC-реакция (Ghmjngh/luckstrategy типаж) | Veo Omni Flash | 3 | ~278 кр |
| | | | | **ИТОГО** | **~520 кр** |

Серия в совокупности: чистый геймплей (без людей) / системный UI-пруф денег / живое лицо с эмоцией —
три независимых сигнала, не один ролик ×3.

---

## VID001 — «Catch am before e fly» (геймплей-адреналин)

### 1. Сигнал серии
Хук-угол `avi_crash_adrenaline` из AVI.yaml («успей кэшаут до вылета, растущий множитель»).
Чистый скринрекорд-стиль без людей — скролл-стоппер на гипнозе цифры.

### 2. Паттерн + обоснование
**Паттерн №1 разведки — «Геймплей-экран» (Bang-0911, активно скейлит, 8+ дней ротации, app-прокладка).**
Раскадрованы все 3 ролика (Library ID `1359300286119971`, `766856263088236`, `983683397917901`).
Копируем связку: UI Aviator целиком (история множителей + баланс + 2 ставки) · растущий множитель
как единственная динамика · синхронный рост Cash Out сумм · финал-развязка (кэшаут до краша → следом
FLEW AWAY — «успел!») · Pidgin-каллаут. НЕ копируем: баланс 318K (для low-entry оффера неправдоподобен),
UFC-партнёрку и бейдж TAX FREE (чужой бренд-атрибут Bangbet).

### 3. Модель + режим + варианты + бюджет
**Kling v3.0, режим Keyframes (первый+последний кадр), 1080p.** Дефолт I2V по playbook: контролируемая
драматургия — фиксируем «до» и «после», модель анимирует рост множителя между ними.
Целевая длина паттерна 16–24с при лимите модели 15с → **2 сегмента × ~10с, склейка ffmpeg concat**
(одинаковые кодек/разрешение; кадр-стык: last frame сегмента 1 == first frame сегмента 2).
Вариантов финала: **2** (по 2 сегмента) = 4 генерации × ~34.6 кр (Kling = 75 ген с баланса 2 595 кр)
= **~138 кр**. Между вариантами меняем ОДНУ переменную — финальный множитель краша:
- **Вариант 1 (промт ниже):** кэшаут на 8.70x → краш FLEW AWAY **11.36x**.
- **Вариант 2:** кэшаут на 8.70x → краш FLEW AWAY **15.70x** («жадные улетели дальше» — больший
  контраст «успел/не успел»). Меняется ТОЛЬКО финальный множитель краша в сегменте 2; кэшаут-точка,
  тостер и баланс идентичны варианту 1 (цифры варианта 2 — в §5 после промта сегмента 2).

### 4. Статик-вход
**First frame сегмента 1 — экран Aviator из CR004** (рука с Android, экран: множитель 8.7x, CASH OUT
GHS 210, бейдж «Cash out to MoMo», плашка «Deposit GHS 10 · 20 Free Bets»). Статус CR004 в AVI.yaml —
`verdict: testing` (принят байером при заливе, формального «Gate A» нет — см. допущение в шапке).
Это единственный статик batch01 с живым геймплей-UI — прямое попадание в паттерн Bang. Для сегмента 1 кадрируем ТОЛЬКО экран
телефона (full-screen UI, без руки/улицы) и ставим стартовый множитель 1.21x — рука и фон в
I2V-анимации UI дадут морфинг, чистый UI стабильнее.
- Сегмент 1: first = UI с 1.21x / ставка GHS 20 → last = UI с 4.85x, Cash Out GHS 97.00.
- Сегмент 2: first = last сегмента 1 → last = экран «FLEW AWAY 11.36x» + зелёный тостер
  «✅ Cashed out GHS 174.00 at 8.7x» (успели до краша — победный сценарий из ролика 3 Bang).

### 5. Полный промт генерации (EN)

**Segment 1 (Kling v3.0 Keyframes, first frame = crop CR004 phone screen, ~10s):**

```
Vertical 9:16 screen recording of the Aviator crash game mobile app, dark UI,
exactly continuing from the provided first frame. NOT a glossy ad, looks like
a raw phone screen capture.

SCENE: The small red plane flies up-left along a rising red curve. The big white
multiplier counter in the center ticks up smoothly from "1.21x" to "4.85x" over
the clip. The bottom bet panel shows one active bet of "20.00" GHS with an orange
button whose label updates in sync with the multiplier: starts at "Cash Out 24.20 GHS"
and ends at "Cash Out 97.00 GHS" (button amount always = 20 x current multiplier).

PERSISTENT UI (keep identical the whole clip, do not redraw): top bar with red
"Aviator" logo on the left and wallet balance "6,418.50 GHS" on the right; a row
of previous round multipliers under it reading "2.27x  1.64x  21.55x  3.23x  1.42x";
small live players counter near the plane decreasing slowly from "214" to "168".

MOTION: only the plane, the red curve, the multiplier digits and the cash-out
amount animate. No camera movement, no zoom, static phone-screen framing.

TEXT RULES: render all numbers and labels exactly as written in quotes, digits
crisp and legible, no gibberish, correctly spelled, no extra random text, no
watermarks, no brand logos other than "Aviator".
```

**Segment 2 (first frame = last frame of segment 1, ~10s):**

```
Vertical 9:16 screen recording of the Aviator crash game mobile app, dark UI,
exact continuation of the provided first frame (same layout, same colors, same
top bar with balance "6,418.50 GHS" and history row "2.27x 1.64x 21.55x 3.23x 1.42x").

SCENE: The multiplier keeps climbing from "4.85x". At around "8.70x" a green toast
notification slides in over the lower part of the play area reading exactly:
"✅ Cashed out 174.00 GHS at 8.70x" and the orange button switches to a red
"Cancel / Waiting for next round" state. The plane keeps flying, multiplier
continues to "11.36x", then the plane darts off the top-right corner and the
screen flashes the round end: big red text "FLEW AWAY!" above red "11.36x" in
the center. The wallet balance in the top bar updates from "6,418.50 GHS" to
"6,572.50 GHS" right after the cash-out toast.

MOTION: plane, curve, multiplier digits, toast slide-in, balance number change.
Static framing, no zoom, no camera shake.

TEXT RULES: render every quoted string exactly, digits crisp, no gibberish,
no watermarks, no third-party logos. The cash-out amount "174.00 GHS" must be
identical in the toast and consistent with 20 GHS x 8.70.
```

**Вариант 2 — дельта к промту сегмента 2 (сегмент 1 и все остальные цифры идентичны варианту 1):**
- краш-множитель: `"11.36x"` → `"15.70x"` (обе позиции — «multiplier continues to» и красная цифра
  под «FLEW AWAY!»);
- тостер кэшаута БЕЗ изменений: `"✅ Cashed out 174.00 GHS at 8.70x"` (20 GHS × 8.70 = 174.00 ✓);
- баланс БЕЗ изменений: `"6,418.50 GHS"` → `"6,572.50 GHS"` (старый + 154 GHS чистыми: +174 выигрыш
  − 20 ставка).
Сверка на отсмотре варианта 2: краш = 15.70x, тостер = 174.00 @ 8.70x, баланс = 6,572.50.

### 6. Длина / формат / хук 0-3с
**~20с (2×10с concat), 9:16, 1080p.** Хук 0-3с: крупный белый множитель уже тикает вверх по центру
+ баланс GHS в шапке — цифра-первой, как у Bang. Звуконезависим полностью (весь смысл в UI).

### 7. QA-критерии отсмотра (критичные для концепта)
- **Синхрон математики:** Cash Out = ставка × текущий множитель в КАЖДОМ кадре раскадровки (Bang это
  держит — мы обязаны); баланс после кэшаута = старый + 154 GHS ровно.
- **UI наполнен:** история множителей, баланс, счётчик игроков, кнопки — не пустой шаблон.
- **Текст:** «FLEW AWAY!», суммы, тостер — без gibberish; цифры не плывут при анимации (главный риск
  I2V — морфинг цифр между кадрами).
- **Стык сегментов:** кадр склейки без скачка (UI идентичен).
- **Хук 0-3с:** множитель читается крупно с первого кадра.
- Без вотермарок генератора, без чужих лого (Bangbet/UFC/SPRIBE не утащить из референса).

---

## VID002 — «GHS 240 enter my MoMo sharp» (payment-trust пруф)

### 1. Сигнал серии
Хук-угол `avi_momo_cashout` (+`gh_momo_payout`): выигрыш в Aviator реально доезжает на MTN MoMo.
Сигнал «деньги выводятся» — главный trust-барьер Tier-3, отличен от адреналина VID001.

### 2. Паттерн + обоснование
Гибрид паттерна №1 (геймплей Bang — раскадровка выше) и связки №1 рынка GH —
**luckstrategy UGC «MoMo-пуш как пруф» (бенчмарк скейла ×20+, главный образец market-profile)**.
Видео-версия формулы чемпиона: короткий момент кэшаута в игре → СРАЗУ системный MoMo-пуш с той же
суммой. Bang доказал, что UI-экран работает в видео; luckstrategy доказал, что MoMo-пруф — сильнейший
конвертер статики GH. Склейка двух проверенных сигналов в одном ролике, цифра одна и та же на обоих
экранах. Статик CR001 (наш Gate-A, формула чемпиона KE) задаёт сумму GHS 240 — её и анимируем.

### 3. Модель + режим + варианты + бюджет
**Kling v3.0 Keyframes, 1080p, один сегмент до 15с.** Keyframes идеален: first = игра в момент полёта,
last = домашний экран Android с MoMo-пушем. Контроль обоих «до/после» убирает главный риск — рассинхрон
сумм. Вариантов: **3** (меняем одну переменную — сумму кэшаута: 240.00 / 183.50 / 416.00 GHS,
first/last кадры под каждую) = 3 × ~34.6 кр = **~104 кр**.

### 4. Статик-вход
**First frame — экран Aviator по мотивам CR004** (тот же UI-стиль, что VID001 — консистентность серии),
но с цифрами под CR001: множитель 6.0x, ставка GHS 40, Cash Out GHS 240.00, бейдж «Cash out to MoMo»
(этот бейдж уже есть на CR004 — мостик к пуш-финалу).
**Last frame — НЕ из batch01, описание для генерации статика** (запросить у syntx или сгенерить
кадр в том же чате): Android-домашний экран (тёмные обои, нижний док с иконками), сверху системная
push-нотификация в нативном стиле MTN MoMo: жёлтая иконка MTN, заголовок «MTN MoMo», текст
«Payment received: GHS 240.00 from Aviator», таймстамп «just now». Референс стиля пуша — зелёная
карточка «MTN MoMo Payment received GHS 240.00» из CR001 (наш аппрувнутый визуал), но встроенная
в системный UI как требует QA-чеклист (пуш/тостер, не дизайн-плашка).
**Обязательный шаг (last frame = новый генерат, не из batch01):** ДО использования как keyframe
прогнать кадр по QA-чеклисту статики `creative-gen.md`: пуш — системный Android UI-баннер (нативный
стиль шторки/нотификации), НЕ дизайн-плашка поверх обоев; сумма «GHS 240.00» дословно и = first frame
(и 183.50 / 416.00 для вариантов 2-3); текст без gibberish/обрезанных слов; статус-бар Android
консистентен с first frame. Брак кадра → перегенерация статика ДО трат Kling-кредитов на сегмент.

### 5. Полный промт генерации (EN)

**(Kling v3.0 Keyframes; first frame = Aviator UI 6.0x / Cash Out 240, last frame = Android home
screen with MoMo push, ~12s):**

```
Vertical 9:16 mobile screen recording, two-beat story on one phone screen,
raw screen-capture look, NOT a polished motion-design ad.

BEAT 1 (0-6s): The provided first frame — Aviator crash game, dark UI, red plane
climbing along a red curve. Center multiplier ticks up from "6.00x" to "6.80x".
Bottom panel: single bet "40.00" GHS, orange button label synced to multiplier,
reaching "Cash Out 272.00 GHS"; a yellow badge "Cash out to MoMo" sits under the
top bar. At "6.00x"-moment a thumb-tap ripple hits the orange button: button turns
green for a second with "CASHED OUT 240.00 GHS" confirmation, the plane keeps
flying without the player.

BEAT 2 (6-12s): Quick natural transition — the app minimizes (screen swipe up)
revealing the Android home screen from the provided last frame: dark wallpaper,
app icons in a bottom dock. A system push notification banner slides down from
the top in native Android style: yellow MTN logo icon, title "MTN MoMo", message
text exactly "Payment received: GHS 240.00 from Aviator", timestamp "just now".
The banner stays readable for the final 3 seconds.

CONSISTENCY: the cashed-out amount and the push amount are the SAME number,
"240.00" — render it identically in both beats.

PERSISTENT UI: top status bar with clock "17:45", 4G and battery icons, kept
through both beats.

MOTION: multiplier digits, button state change, swipe-up transition, banner
slide-down. No camera moves, no zoom, static phone framing.

TEXT RULES: every quoted string rendered exactly, crisp legible digits,
no gibberish, correctly spelled, no watermarks, no extra logos beyond
"Aviator" and the MTN MoMo notification icon.
```

### 6. Длина / формат / хук 0-3с
**~12с, 9:16, 1080p.** Хук 0-3с: множитель 6.00x + кнопка «Cash Out 240 GHS» — сразу цифра и деньги.
Развязка (MoMo-пуш) на ~7й секунде — ключевой момент не позже середины (QA-чеклист). Звуконезависим.

### 7. QA-критерии отсмотра (критичные для концепта)
- **ОДНА цифра везде:** 240.00 на кнопке кэшаута = в подтверждении = в MoMo-пуше (главный пункт
  чеклиста статики, применённый к каждому кадру; рассинхрон = REJECT).
- **MoMo-пруф = системный UI** (нативный Android-пуш), не дизайн-плашка поверх.
- **Переход beat 1 → beat 2:** свайп без морфинг-каши, домашний экран не «плывёт».
- **Суммы в диапазоне GH:** 240 / 183.50 / 416 — некруглые, правдоподобные (НЕ миллионы).
- Телефон/UI = Android (статус-бар, шторка) — Tier-3 правило.
- Текст пуша дословный, без артефактов; таймстамп один и не скачет.

---

## VID003 — «I deposit just GHS 10...» (low-entry + живая эмоция)

### 1. Сигнал серии
Хук-угол `avi_freebets_lowentry` (+`gh_low_entry`, `gh_momo_payout`): «депни 10 GHS → 20 free bets»
— бонус как есть, objection-kill «дорого/боюсь начать». Третий сигнал — ЧЕЛОВЕК и эмоция
(в VID001/002 людей нет), живое лицо = trust-якорь Tier-3.

### 2. Паттерн + обоснование
Эмо-ядро от **split-UGC паттерна №2** (Samuel African Trader — самый горячий: 13 копий/4 дня — сила
в живом человеке), но БЕЗ его 47-секундного talking-head формата: playbook прямо предупреждает —
AI-аватар на 47с = риск артефактов, аватары только для коротких сегментов. Поэтому берём короткую
(8с) эмоциональную реакцию без липсинка-монолога. Типаж и сцена — от скейлеров статики:
luckstrategy UGC (реальное фото человека + эмоция + MoMo) и Ghmjngh (локация Аккры + человек +
MoMo-пруф). Low-entry угол подтверждён самим Bang («GHS 10 → Mega Wins! Risk small, cash out big»).
Текстовая рамка — из нашего аппрувнутого CR001 (Pidgin-фраза «Chale e dey work»).

### 3. Модель + режим + варианты + бюджет
**Veo Omni Flash (I2V, ~8с, со звуком).** Из таблицы playbook — её ниша ровно это: «фотореализм,
лицо, эмоция, липсинк-короткие». 8с достаточно для одной реакции; Kling на лице слабее, Seedance
дороже за вариант без выигрыша в лицах. Veo — самая дорогая модель серии (~92.7 кр/ролик: 28 ген
с баланса 2 595 кр против ~34.6 кр у Kling), поэтому вариантов ровно **3**, без запасных (меняем
одну переменную — пик эмоции: сдержанное «не верю» → широкая улыбка / вскочил с кулаком вверх /
показывает экран в камеру) = 3 × ~92.7 кр = **~278 кр**.

### 4. Статик-вход
**First frame — CR005** (фанат Black Stars в жёлто-красно-зелёной джерси, кулак радости, толпа
стадиона, MoMo-карточка «received GHS 260.00 from Aviator»). Статус CR005 в AVI.yaml — `verdict:
testing` (принят байером при заливе, не «Gate A» — см. допущение в шапке). Почему он: единственный
статик batch01 с живым человеком и сильной эмоцией, анатомия аппрувнута (v3a фиксил руку), типаж локальный,
футбол-якорь (нац. идентичность GH) бонусом тащит угол `avi_football_anchor` вторым слоем.
CR002 (женщина before/after) — запасной first frame, если CR005 в движении даст артефакты толпы.
Last frame не нужен (Omni Flash — обычный I2V без Keyframes); финальное состояние описываем промтом.

### 5. Полный промт генерации (EN)

**(Veo Omni Flash, I2V from CR005, ~8s, with native audio):**

```
Vertical 9:16 candid phone-shot video, handheld with slight natural shake,
shot on a mid-range Android phone, NOT studio quality, NOT a glossy commercial.

SCENE: Continue from the provided image — a young Ghanaian man in a yellow-red-green
Black Stars football jersey in a lively stadium crowd. He looks at his phone in his
left hand, eyes widen, then he pumps his right fist and jumps once, laughing and
shouting with pure joy, people around him keep celebrating the match. His happy
shout is short and natural (crowd noise dominates the audio).

OVERLAYS (static text, burned into the video, keep position fixed):
- top caption in bold white with thin black outline: "I deposit just GHS 10 😅"
- bottom banner strip, dark background, yellow bold text:
  "GHS 10 → 20 FREE BETS on Aviator"
The on-image MTN MoMo card from the source frame stays visible and unchanged in
the upper right area, reading "You received GHS 260.00 from Aviator".

CAMERA: single continuous handheld shot, no cuts, slight zoom-in toward his face
at the fist pump. Natural daylight, stadium atmosphere.

REALISM ANCHORS: hands natural with five fingers, relaxed then celebrating pose,
correct proportions, face consistent with the source image across all frames,
no morphing. Crowd in background stays soft-focus.

TEXT RULES: render the quoted overlay strings exactly, correctly spelled,
no gibberish, no watermarks, no brand logos beyond the MoMo card already present.

AUDIO: stadium crowd roar, one short joyful shout from the man, no music,
no voice-over.
```

### 6. Длина / формат / хук 0-3с
**~8с, 9:16.** Хук 0-3с: лицо + взрыв эмоции (вскинутый кулак) + оверлей «I deposit just GHS 10 😅»
+ MoMo-карточка GHS 260 уже в кадре — эмоция и цифра одновременно. Оверлеи делают ролик
звуконезависимым (аудио — бонус для reels со звуком).

### 7. QA-критерии отсмотра (критичные для концепта)
- **Лицо/руки в движении:** без морфинга между кадрами раскадровки, пальцы — пять, кулак не ломается
  (самый рискованный концепт по анатомии — потому 3 варианта).
- **Консистентность с first frame:** тот же человек/джерси/толпа от первого до последнего кадра.
- **UGC, не глянец:** ручная камера, бытовая фактура; если выйдет «рекламный полиш» — REJECT.
- **Синхрон сумм:** MoMo-карточка 260 ≠ оверлей 10 — это РАЗНЫЕ числа по смыслу (депозит vs выигрыш),
  проверить, что генератор не «подровнял» их друг под друга и не исказил.
- **Оверлеи:** дословно, без обрезанных слов, позиция фиксирована (не «плавающий» текст).
- **Хук 0-3с:** эмоция читается с первого кадра, не «разгон» из статики полролика.

---

## Бюджет (кредиты) и порядок генерации

Баланс на 2026-06-04: **~2 595 кр**. Семантика playbook: «ген» модели = СКОЛЬКО роликов можно
сделать с этого баланса (больше ген = дешевле модель) → цена ролика = 2 595 / ген.
Kling v3.0: 75 ген → **~34.6 кр/ролик**; Veo Omni Flash: 28 ген → **~92.7 кр/ролик**.

| Концепт | Модель | Генераций | Кр/шт | Итого |
|---|---|---|---|---|
| VID001 (2 варианта × 2 сегмента) | Kling v3.0 Keyframes | 4 | ~34.6 | ~138 кр |
| VID002 (3 варианта) | Kling v3.0 Keyframes | 3 | ~34.6 | ~104 кр |
| VID003 (3 варианта) | Veo Omni Flash | 3 | ~92.7 | ~278 кр |
| **Серия (план)** | | **10** | | **~520 кр (~20% баланса)** |
| Резерв на перегенерацию брака (~30%) | | ~3 | | ~155 кр |
| **Потолок серии** | | | | **~675 кр (~26% баланса)** |

Остаётся ≥1 900 кр — запас на следующие батчи и срезанные Meta креативы (playbook: «закладывать
запас вариантов»).

**Порядок генерации (для lead):**
1. **VID003 первым** — НЕ потому что Veo дешевле (наоборот: ~92.7 кр/ролик, ~2.7× дороже Kling),
   а как дешёвая ПО ВРЕМЕНИ калибровка самого рискованного входа: CR005 (человек + толпа) — главный
   анатомический риск серии, и Veo-вариантов всего 3 (фиксированный потолок ~278 кр). Если CR005
   в движении сыпется по анатомии — переключаем first frame на CR002 ДО старта Kling-этапа,
   не ломая очередь VID001/002.
2. **VID002** (один сегмент, проще VID001): обкатка Keyframes-режима + генерация last frame
   (Android+MoMo-пуш) в том же чате; паттерн «UI-цифры в анимации» проверяем здесь.
3. **VID001 последним** (самый дорогой, 2 сегмента + склейка): к этому моменту известно, как Kling
   держит цифры; брак сегмента 1 → не генерим сегмент 2 впустую.
4. Дисциплина syntx: проект `GH_AVI`, **новый чат на каждый концепт** (3 чата), варианты концепта —
   в его же чате; каждый чат сразу `⋯ → «В проект» → GH_AVI`.
5. Самоотсмотр раскадровкой (`ffmpeg -vf fps=1`) КАЖДОГО генерата по QA-чеклисту → REJECT брака →
   Gate AV для qa (mp4 + раскадровки + промты + статик-источники + Library ID референсов).
6. После ✅ Gate AV: `python scripts/uniquify_video.py <mp4...> --offer GH_AVI --copies 3`
   (для VID001 со склейкой — `--no-speed`, длительность сегментов критична для стыка).

**Референсы (Library ID, для Gate AV):** Bang-0911 `1359300286119971` / `766856263088236` /
`983683397917901` (геймплей-паттерн); Samuel `4260063760872820` (эмо-ядро, формат НЕ копируем);
luckstrategy / Ghmjngh — статика-образцы MoMo-пруфа и типажа из `market-profile/GH.md`.
