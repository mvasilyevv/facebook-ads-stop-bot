# Telegram notification context

## Назначение

Контекст доставляет короткие incident-карточки и принимает operator actions,
не превращая Telegram в источник бизнес-истины.

## Владеет

- `telegram_updates_inbox` и HTTPS webhook commit-before-204;
- `notification_events`, `notification_deliveries` и delivery retries;
- `telegram_message_slots` для редактируемой карточки incident;
- opaque `telegram_action_tokens` и navigation tokens;
- recipient preferences, thresholds, suppression и quiet hours;
- единственным HTML Bot API gateway.

## Инварианты

- Бизнес-код не вызывает Bot API напрямую.
- Event создаётся в транзакции с task/FSM/incident.
- Callback содержит только `a:<opaque-token>`; raw IDs и capability tokens не
  попадают в URL или текст.
- `429` переносит `scheduled_at`, `401/403/invalid HTML` имеют явный lifecycle.
- Потерянная карточка создаёт явный `incident_snapshot_reissued`, а не скрытый
  fallback send.
- Severity, correlation и suppression детерминированы кодом, а не AI.

## Glossary

- **Incident** — коррелированный операторский риск с generation и lifecycle.
- **Notification event** — immutable факт, который нужно спроецировать.
- **Delivery** — попытка доставки event конкретному recipient.
- **Message slot** — текущая Telegram-карточка incident для recipient.
- **Action token** — одноразовая owner/recipient/generation-bound capability.
