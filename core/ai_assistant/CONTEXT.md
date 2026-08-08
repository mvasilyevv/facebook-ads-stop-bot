# AI assistant context

## Назначение

Контекст помогает оператору исследовать состояние и подготовить действия,
сохраняя детерминированные safety-решения и подтверждение за control plane.

## Владеет

- assistant prompts и tool registry;
- evidence-aware ответы по operator snapshot и domain queries;
- нормализацию tool results для web assistant;
- границы между read-only analysis, draft и исполняемой командой.

## Инварианты

- AI не определяет severity, suppression, correlation или money safety.
- Tool не обходит `CommandService`, authorization или action lifecycle.
- `queued` не описывается как подтверждённый внешний результат.
- Ответ отделяет source evidence от inference и сохраняет correlation context.
- Недоступные или stale данные обозначаются явно; они не заполняются догадкой.

## Glossary

- **Tool** — типизированный backend-вызов, доступный assistant.
- **Evidence** — данные и freshness, на которых основан вывод.
- **Draft** — предложение без права выполнить side effect.
- **Inference** — интерпретация evidence, явно отделённая от факта.
