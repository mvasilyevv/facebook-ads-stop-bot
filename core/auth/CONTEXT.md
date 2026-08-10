# Identity and access context

## Назначение

Контекст устанавливает проверенную identity и права для web, TMA, desktop и
внутренних service-вызовов до доступа к operator data или commands.

## Владеет

- panel sessions, invites, roles и owner roster;
- backend-проверкой Telegram `initData`;
- desktop access и forward-auth boundaries;
- service/API authentication middleware;
- transport-safe передачей auth в HTTP и WebSocket.

## Инварианты

- `initDataUnsafe` используется только для display.
- Capability/action/navigation tokens имеют минимальный scope и срок жизни.
- Raw credentials и bot tokens не попадают в URL, exceptions, traces или logs.
- UI visibility не является authorization; решение всегда принимает backend.
- Reconnect и token rotation не смешивают identity разных пользователей.

## Glossary

- **Principal** — подтверждённая пользовательская или service identity.
- **Role** — набор разрешённых operator actions.
- **Panel session** — серверная web-session оператора.
- **Capability token** — opaque ограниченное право на конкретное действие.
