# FB Agent — backlog после локального release candidate

Краткий список работ, которые не должны размывать текущую сборку. Production-switch сюда не входит и выполняется только после отдельной команды owner.

## До production-release

- [x] Прогнать PostgreSQL integration suite на чистой изолированной БД.
- [x] Прогнать crash/concurrency/DB-restart и lost-NOTIFY acceptance.
- [x] Прогнать полный Telegram failure/burst suite.
- [x] Собрать immutable images в CI и проверить digest-only manifest.
- [ ] Проверить unified desktop runtime в disposable container.
- [ ] Пройти browser/device matrix для web, TMA и remote desktop.
- [ ] Провести локальный restore/PITR drill; удалённые backups исключены решением owner.
- [ ] Получить release CI для load/chaos/a11y и реальные field Web Vitals; локальные load/chaos/a11y зелёные.
- [x] Подготовить rollback и двухчасовой cutover packet.

## Продуктовые улучшения после стабилизации

- [ ] Переименовать internal money-status classifier и удалить неиспользуемый `lane`-аргумент из terminal projection API.
- [ ] Добавить read-only enable-рекомендации как projection текущих данных с owner-preview; без worker, таблицы событий и auto-activate.
- [ ] Собрать полевые метрики UX и скорректировать ranked attention по фактической работе owner.
- [ ] Провести отдельный usability-pass Telegram-карточек на реальном потоке уведомлений.

## Следующая HA-фаза

- [ ] Добавить второй application host после 30 дней выполнения SLO.
- [ ] Провести два restore drill и полный host-failure drill.
- [ ] Добавить PostgreSQL standby/failover только с fencing и quorum/witness.
