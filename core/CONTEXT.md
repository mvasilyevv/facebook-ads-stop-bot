# Safety and control context

## Назначение

Контекст превращает наблюдения Meta и команды оператора в однозначный,
идемпотентный lifecycle с PostgreSQL как единственным источником истины.
`apps/*_worker` являются runtime-entrypoints, а бизнес-инварианты принадлежат
модулям `core/`.

## Владеет

- `task_queue`, lanes, priority, deadlines, leases и fencing tokens;
- `CommandService` и единым lifecycle действий UI, TMA, Telegram и automation;
- per-cabinet observer actors и `cabinet_runtime`;
- Meta mutation outcomes `CONFIRMED | REJECTED | UNKNOWN` и reconciliation;
- server-side cabinet day, currency evidence, freshness и money semantics;
- созданием incident/notification intent в транзакции доменного изменения.

Не владеет доставкой Telegram, отображением operator UI, browser session или
production release lifecycle — это отдельные контексты.

## Инварианты

- PostgreSQL — authority; Redis и `LISTEN/NOTIFY` только ускоряют пробуждение.
- Money lane claim-ит только `autopause_worker`.
- Финализация задачи требует точного `task_id + lease_owner + lease_token`.
- Deadline проходит до browser-agent; неоднозначный внешний результат не
  повторяется вслепую.
- `null` означает unknown, а `0` — подтверждённый ноль.
- Currency и IANA timezone должны иметь свежие durable evidence до money-work.

## Glossary

- **Task** — durable единица исполнения в `task_queue`.
- **Lane** — класс изоляции `money | interactive | bulk | background`.
- **Lease** — ограниченное во времени право worker выполнять задачу.
- **Fencing token** — версия владения, запрещающая stale-owner финализацию.
- **Command receipt** — результат постановки команды; `queued` не означает
  успешное внешнее действие.
- **UNKNOWN** — внешний side effect мог произойти, но подтверждения нет.
- **Cabinet runtime** — durable состояние actor конкретного Meta-кабинета.
