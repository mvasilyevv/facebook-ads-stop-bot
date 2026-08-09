# FB Agent — backlog после локального release candidate

Краткий список работ, которые не должны размывать текущую сборку. Production-switch сюда не входит и выполняется только после отдельной команды owner.

## До production-release

- [ ] Прогнать PostgreSQL integration suite на чистой изолированной БД.
- [ ] Прогнать crash/concurrency/DB-restart и lost-NOTIFY acceptance.
- [ ] Прогнать полный Telegram failure/burst suite.
- [ ] Собрать immutable images в CI и проверить digest-only deploy.
- [ ] Проверить unified desktop runtime в disposable container.
- [ ] Пройти browser/device matrix для web, TMA и remote desktop.
- [ ] Провести restore/PITR drill и подтвердить RPO/RTO.
- [ ] Прогнать load/chaos/a11y gates и реальные Web Vitals.
- [ ] Подготовить rollback и двухчасовой cutover packet.

## Продуктовые улучшения после стабилизации

- [ ] Переименовать internal money-status classifier и удалить неиспользуемый `lane`-аргумент из terminal projection API.
- [ ] Добавить read-only enable-рекомендации как projection текущих данных с owner-preview; без worker, таблицы событий и auto-activate.
- [ ] Собрать полевые метрики UX и скорректировать ranked attention по фактической работе owner.
- [ ] Провести отдельный usability-pass Telegram-карточек на реальном потоке уведомлений.

## Следующая HA-фаза

- [ ] Добавить второй application host после 30 дней выполнения SLO.
- [ ] Провести два restore drill и полный host-failure drill.
- [ ] Добавить PostgreSQL standby/failover только с fencing и quorum/witness.
