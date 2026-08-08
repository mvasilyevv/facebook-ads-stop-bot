# Playbook: запуск Facebook Ads кампании

Источник правды для operator-controlled campaign creation. Production-запуск
выполняется desktop-first через web UI и `campaign_creator_worker`; TMA и
mobile web показывают progress, result и доступные lifecycle actions, но не
содержат отдельный wizard.

Прямые одноразовые launch scripts и запись/replay UI-сценария не являются
production-путём. Не использовать их как скрытый резерв при ошибке worker.

## Безопасный lifecycle

```text
validate → operator review → queued → preparing → uploading → creating
        → confirmed | failed | unknown | cancelled
```

- HTTP `202` подтверждает только постановку в очередь.
- Один launch использует стабильный idempotency key и correlation ID.
- После начала внешнего create ошибка может означать `unknown`; весь запуск не
  повторяется вслепую.
- При `unknown` сначала сверяются созданные Meta objects и только затем
  оператор выбирает дальнейшее действие.
- Все создаваемые campaign/ad set/ad остаются `PAUSED` до ручного review.
- Отмена допустима только пока contract явно показывает cancelable state.

## Перед запуском

Оператор подтверждает:

1. Точный `ad_account_id` для каждого кабинета.
2. Offer, destination link и доступность prelanding из нужного browser profile.
3. Page и Pixel, реально расшаренные на выбранный кабинет.
4. Свежий server-owned снимок кабинета: IANA timezone, ISO currency, её minor-unit
   exponent, время наблюдения и начало следующего cabinet-local day.
5. Objective, optimization event, attribution window, budget model и limits в
   major units подтверждённой валюты.
6. GEO, age, placements и creative feature settings.
7. Campaign/ad set/ad naming и tracking parameters.
8. Полный creative QA: формат, текст, цифры, язык, offer, отсутствие дублей.
9. Все создаваемые объекты имеют initial status `PAUSED`.

Если любой обязательный источник `partial`, `stale` или `unavailable`, validate
не должен превращать неизвестное значение в default. Запуск откладывается до
следующего подтверждённого снимка источника.

## Бизнес-правила теста

- Тест креативов: разные visuals при одном тексте и чистой атрибуции варианта.
- Тест текстовых углов: один выигравший visual, разные тексты отдельным запуском.
- ABO/CBO, budgets, bid strategy и attribution выбираются под задачу и явно
  отображаются в preview.
- Start time вычисляется сервером в timezone кабинета; локальная дата браузера
  не является источником истины.
- Клиент не передаёт timezone, currency или evidence timestamp. Суммы передаются
  decimal strings в major units и переводятся в integer minor units только
  сервером по явному exponent; неизвестная валюта блокирует запуск.
- Page не выбирается автоматически как первая доступная.
- Tracking содержит стабильный Meta Ad ID и согласованные sub-параметры; raw
  internal task/action IDs в destination URL не передаются.

## Creative QA gate

До постановки в очередь:

- все файлы открываются и соответствуют заявленному media type;
- aspect ratio и длительность допустимы для placements;
- текст на изображении совпадает с ad copy и валютой;
- нет чужого offer, старого промокода, инвертированного UI или повторного кадра;
- каждый concept имеет уникальный стабильный code;
- preview показывает точное количество campaigns, ad sets, creatives и ads;
- большие uploads укладываются в deadline либо разбиты на явные resumable jobs.

QA не заменяется автоматическим анализом. Финальное подтверждение остаётся у
оператора.

## Во время запуска

Web UI показывает:

- stage и процент progress;
- количество подготовленных, загруженных и созданных объектов;
- correlation ID;
- deadline и freshness последнего update;
- конкретную ошибку без raw traceback/secret;
- доступные `resume`, `abort` или manual reconciliation только если backend
  действительно поддерживает действие в этом state.

Не закрывать incident как resolved по факту успешной постановки в очередь.
Финальный green допустим только после подтверждённых Meta IDs и результата
`confirmed`.

## Partial failure и UNKNOWN

1. Остановить повторные launch attempts с новым key.
2. Открыть run detail и сохранить correlation ID.
3. Сверить Meta по account, naming window и already-created IDs.
4. Классифицировать объекты: complete, incomplete, orphan, ambiguous.
5. Не удалять campaign, если у неё есть spend или неизвестна история.
6. Cleanup нулевых orphan objects — отдельное destructive действие с точным
   списком IDs и подтверждением оператора.
7. После сверки закрыть run как confirmed/failed/unknown штатным lifecycle, а
   не прямым SQL.

## После запуска

- Проверить структуру, statuses, budgets, schedule, Page, Pixel, creatives и
  tracking непосредственно в Ads Manager.
- Сопоставить созданные Meta IDs с run result.
- Убедиться, что observer видит нужный cabinet и новые ads без stale/partial.
- Передать оператору список объектов на review; activation — отдельная
  осознанная command.
- После накопления данных анализировать creative performance по устойчивому
  creative code и latest-per-ad метрикам.

## Диагностика worker

```bash
sudo /opt/fb-agent/current/scripts/platform-compose.sh status
sudo /opt/fb-agent/current/scripts/platform-compose.sh logs campaign_creator
sudo /opt/fb-agent/current/scripts/platform-desktop-compose.sh status
```

Если browser channel недоступен, следовать
[RUNBOOKS.md](RUNBOOKS.md#browservision-недоступен). Перезапуск worker не
является разрешением повторить неоднозначную внешнюю операцию.
