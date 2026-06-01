# Промт-инжиниринг для генерации креативов (Sora / GPT Image)

Best-practice свод под нашу генерацию (gambling UGC, Tier-3, fake-post/before-after/чат/геймплей).
Обязателен к применению в Фазе 2 (SOP). Цель — не «есть ли элементы из промта», а **чистый
результат с первого-второго раза**: без сломанной анатомии, кракозябр в тексте, «селфи с баннером».

> Контекст: наши факапы батча #01 (сломанная рука CR005, «MDMO» вместо MTN MoMo, CTA в строке
> ввода чата, текст на весь экран телефона) — ровно те места, что этот гайд закрывает.

---

## 0. Перед генерацией — АНАЛИЗ (не сразу промт)
1. **Сверься с реестром:** market_profile (язык/деньги/девайсы/селебы), production_profile (стиль гео), hooks (что генерим).
2. **Заведи reference** (SOP Фаза 2): наш винер / конкурент. Генерация всегда reference-based, не text-to-image.
3. **Определи тип кадра** и его «правила правдоподобия»: пост в ленте / скрин чата / скрин игры / before-after / фото человека. У каждого — своя нативная структура (см. §4).

## 1. Структура промта (порядок важен)
Пиши блоками, от общего к частному. Рекомендованный порядок:
1. **Тип/формат кадра** + «что это»: `authentic Facebook post screenshot, NOT a glossy ad`.
2. **Субъект** (кто/что главное) + **сцена** (где/когда/действие).
3. **Камера/реализм:** `shot on phone, natural light, candid, slightly imperfect`.
4. **Текст на картинке** — точные строки в кавычках + где (см. §3).
5. **Инварианты и запреты** в конце (см. §5).
> GPT Image 2 = reasoning-first: понимает естественный язык, **меньше «хаков», больше ясного описания**. Многочастные инструкции (объекты/цвета/несколько субъектов) выполняет точнее, если разнести по пунктам.

## 2. Анатомия — почему ломается и как бить
Модель не понимает 3D/анатомию, «галлюцинирует» руки (spaghetti fingers), лицо плывёт при <~5% площади кадра.
- **Позитивные якоря** (профилактика > коррекция): `hands natural with five fingers, relaxed pose, correct human proportions`.
- **Упрощай позу:** одна рука в действии, вторая расслаблена/вне кадра. НЕ две руки в сложном взаимодействии (наш CR005-факап).
- **Если рука всё равно ломается — кадрируй её вне кадра** или дай объект в одну руку простым хватом.
- **Лицо:** если человек мелкий/в толпе — не жди детального лица; крупный план или не показывать лицо вовсе.
- **Запреты — умеренно** (Sora/GPT — новые модели, длинный список негативов ВРЕДИТ, в отличие от SD1.5): 2-4 пункта, `no extra fingers, no warped hands, no deformed face`. Без экстремальных весов.

## 3. Текст на картинке (наш частый брак)
- **Пиши точные строки в кавычках** + явно «render exactly»: `the badge must read exactly "MTN MoMo" and "You received GHS 240.00 from Aviator"`.
- **Указывай место** каждого текста: `caption above the photo`, `bottom strip`, `inside the green button` — иначе модель лепит куда попало.
- **Анти-факап «текст не туда»:** явно запрещай неверные места — `the chat input bar must be EMPTY with placeholder "Message", do NOT put promo text inside it`; `text overlay on top, NOT typed inside the game screen`.
- **Non-English/локальные слова рендерятся хуже** (Pidgin/Twi/суахили) → держи их короткими, дублируй критичные суммы цифрами, **готовься к ручной перегенерации** именно из-за текста.
- **Дай якорь против gibberish:** `all on-image text correctly spelled, no random letters, no gibberish`.
- Сложные текст-композиции (плашки, UI, инфографика) — сильная сторона GPT Image 2 (planning-step), но всё равно проверяй глазами.

## 4. Аутентичность UGC (НЕ глянец) — для Tier-3 критично
Главный сдвиг: **описывай реальный момент, а не фотосессию.** AI по умолчанию тянет в «идеально» — активно дави обратно.
- **Модификаторы аутентичности:** `shot on phone / casual camera-roll photo, natural imperfections, off-center framing, candid, real social-media look, NOT a professional photo, NOT studio lighting`.
- **«Грязные» детали реализма:** бытовой фон (киоск M-Pesa/MoMo, matatu, Accra-улица, домашний интерьер), `natural skin texture, visible pores, subtle imperfections` — против пластикового глянца.
- **Эмоция конкретная, не «happy»:** `skeptical then relieved, excited but believable, tired but proud, caught-in-the-act`.
- **Платформенная нативность:** скрин FB-поста / WhatsApp-чата / Stories — повторяй родной UI платформы (шапка, бабблы, реакции, timestamps). Случайные артефакты (шапка «WORK») = разрушают правдоподобие.
- **НЕ пили «quality words»** (`ultra realistic, 8k, masterpiece, flawless, luxury`) — они делают КАРТИНКУ ИСКУССТВЕННЕЕ. Для нас лучше `realistic phone photo, slightly imperfect`.

## 5. Инварианты при перегенерации (editing/iterate)
Гайд OpenAI: **отделяй что МЕНЯЕТСЯ от того, что ОСТАЁТСЯ, и повторяй инварианты на каждой итерации** (иначе дрейф).
- Меняешь одну вещь за раз (текст / сумма / поза / фон), остальное явно фиксируй: `keep the same layout, profile, colours; only fix the payment badge text`.
- **Рестартай чат между батчами** — модель тащит контекст прошлых генераций внутри сессии (мы и так открываем «Новый чат» на каждый креатив — правильно).

## 6. Воркфлоу-правила (процесс)
- **2-3 варианта на креатив, отбор чистого** — анатомия/текст AI нестабильны, с первого раза не гарантия. (Наше правило из [[feedback-look-at-generated-critically]].)
- **Менять одну переменную за раз** между вариантами — не переписывать промт с нуля.
- **Смотреть КАЖДЫЙ генерат критически** (анатомия/текст/логика/реализм), не по галочкам промта. Брак = REJECT.
- **Reference обязателен**, телефон=Android для Tier-3 (см. [[feedback-creative-gen-essentials]]).

---

## Чеклист перед «ready» (применять к каждому генерату)
- [ ] Руки/пальцы/поза анатомичны? (5 пальцев, нет вывернутых кистей)
- [ ] Весь текст осмысленный, без кракозябр, в логичном месте?
- [ ] CTA/суммы корректны (GHS, бонус, не фейково-гигантские)?
- [ ] Tier-3 народно, не глянец? Девайс = Android?
- [ ] Похоже на реальный пост/скрин, а не на AI-рекламу?
- [ ] Нет случайных артефактов (чужие надписи, шапки, лого)?

## Источники
- [OpenAI GPT Image prompting guide (cookbook)](https://cookbook.openai.com/examples/multimodal/image-gen-1.5-prompting_guide) · [developers.openai.com guide](https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide)
- [Fixing deformed AI generations (learnprompting)](https://learnprompting.org/docs/image_prompting/fix_deformed_generations) · [AI hands/anatomy fixes 2026 (GensGPT)](https://www.gensgpt.com/blog/ai-hands-anatomy-body-fixes-common-errors-2026-guide)
- [AI prompting guide for UGC (AdLibrary)](https://adlibrary.com/guides/ai-prompting-guide-ugc-content-creators) · [ChatGPT Image 2 UGC prompts (ugcmaker)](https://ugcmaker.org/blog/detail/ChatGPT-Image-2-UGC-Prompt-Guide-Make-Ads-That-Feel-Real-Scrollable-and-Fun-4332d52c2edd/)
