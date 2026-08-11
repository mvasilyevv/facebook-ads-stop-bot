# Production launch runbook

Downtime разрешён. Старый runtime не используется как fallback, архив и
backup/restore gate не создаются.

## До запуска

- [ ] CI verification и content-addressed image manifest зелёные.
- [ ] `adoption-bundle-v1.json` и текущие shared secrets находятся на host.
- [ ] `desktop-profile-seed` проверен.
- [ ] `fbctl doctor` проходит.
- [ ] Preflight нового deploy проходит до остановки runtime.

## Запуск

1. Один раз выполнить `fbctl bootstrap --manifest release.json`.
2. Выполнить `fbctl deploy --manifest release.json`.
3. Дождаться шагов pull → stop → infra → migrate → desktop → app → workers →
   system-ready → webhook → smoke → promote.
4. Проверить UI, TMA, Telegram webhook, desktop и нужные cabinet tabs.
5. Проверить отсутствие `failed/unknown` money actions и false-green данных.

## Ошибка

Deploy печатает точный `step`; money workers не запускаются до полной
готовности контура. Исправить причину и повторить тот же manifest. Не запускать
старые scripts и не менять task state вручную.

## После подтверждения

- удалить candidate staging и старые control bundles;
- убедиться, что удалённые backup/rollback assets не восстановились;
- удалить только явно проверенные legacy DB volumes/dumps;
- сохранить новые `pgdata`, Redis, campaign uploads, desktop config и monitoring.
