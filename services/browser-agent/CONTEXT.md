# Browser and Vision context

## Назначение

Browser-agent выполняет DOM scan и session-tunneled Meta Graph операции внутри
явно выбранного Vision/Kasm profile. Он предоставляет transport evidence, но не
владеет задачами и не определяет business success.

## Владеет

- gRPC browser-session contract;
- scan page и отдельной control page;
- Meta fetch через активную browser session;
- `AbortController`, gRPC deadlines и cancellation;
- session/profile identity, health evidence и browser metrics;
- локальной сериализацией операций над page.

## Инварианты

- Каждая browser/Graph операция имеет абсолютный deadline и cancellation.
- Python control plane держит `BrowserOperationFence`; stale lease не может
  авторизовать persistence или mutation finalization.
- Reload/error scan page не блокирует control page.
- Session/profile mismatch является unavailable, а не fallback к текущей вкладке.
- Ответ transport не превращается в `CONFIRMED` без проверки внешнего результата.

## Glossary

- **Vision profile** — каноническая browser identity конкретного кабинета.
- **Browser operation fence** — shared renewable lease, видимый maintenance.
- **Control page** — независимая page для pause/activate и других mutations.
- **Scan page** — page чтения Ads Manager таблицы.
- **Transport outcome** — результат RPC; он не равен domain outcome автоматически.
