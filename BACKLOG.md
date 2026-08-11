# FB Agent — backlog после production

Только необязательные улучшения. Они не входят в gate первого запуска.

## UX и устройства

- [ ] Пройти physical-device matrix web/TMA/desktop на iOS, Android, Safari и Firefox.
- [ ] Провести usability-pass Telegram-карточек на реальном потоке.
- [ ] Собрать field Web Vitals и snapshot gzip telemetry.
- [ ] Добавить в System UI release digest и доказательства readiness.
- [ ] Скорректировать ranked attention по фактической работе owner.

## Продукт

- [ ] Добавить read-only enable-рекомендации с owner-preview, без auto-activate.
- [ ] Упростить internal money-status classifier и удалить неиспользуемый `lane`-аргумент.

## Platform

- [ ] Подключить Renovate с одним еженедельным grouped PR для Actions, Docker, uv и pnpm.
- [ ] Включить `pg_stat_statements` и провести измеряемый index/query audit.
- [ ] Добавлять Sentry только если Grafana/OTel не дадут достаточной диагностики.
- [ ] Проектировать HA только при подтверждённой необходимости; первый runtime single-host.

Backup/restore automation, release archives и runtime rollback намеренно не
возвращаются.
