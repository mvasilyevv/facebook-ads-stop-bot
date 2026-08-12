# Production launch runbook

Downtime разрешён. Старый runtime не используется как fallback, архив и
backup/restore gate не создаются.

## До запуска

- [ ] CI verification и content-addressed image manifest зелёные.
- [ ] `adoption-bundle-v1.json` и текущие shared secrets находятся на host.
- [ ] `PROD_ENV_B64` содержит обе `PANEL_BASIC_AUTH_*` либо ни одной. Явная
      пара всегда приоритетна; при пустой паре bootstrap использует fallback из
      root-owned `/etc/fb-agent/caddy.env` (0600). API/desktop credentials
      оттуда не читаются.
- [ ] Для bootstrap identity migration старый файл находится только по пути
      `/opt/fb-agent/shared/.env`, принадлежит root и имеет mode `0600`;
      `TELEGRAM_CHAT_ID` не используется как identity.
- [ ] Remote identity preflight Release workflow прошёл до image build; тот же
      bootstrap запускается с `--migrate-existing-bootstrap-identity`.
- [ ] `desktop-profile-seed` проверен.
- [ ] `fbctl doctor` проходит.
- [ ] Preflight нового deploy проходит до остановки runtime.

## Запуск

1. Один раз выполнить `sudo python3 -B /path/to/reviewed/fbctl.pyz bootstrap`
   с `--source-env-stdin`, проверенными adoption/profile путями и согласованными
   bootstrap-флагами из Release workflow. Manifest уже вложен в zipapp.
2. Выполнить `sudo python3 -B /path/to/reviewed/fbctl.pyz deploy
   --enable-scanning`.
3. Дождаться шагов pull → stop → infra → migrate → desktop → app → webhook →
   workers → system-ready → smoke → promote.
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
