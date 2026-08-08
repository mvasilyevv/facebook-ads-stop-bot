# Campaign operations context

## Назначение

Контекст превращает offer и launch intent оператора в проверяемый план создания
или дублирования Meta-объектов, сохраняя identity и прогресс для безопасного
resume после остановки.

## Владеет

- offer/catalog semantics и campaign presets;
- campaign creation и duplicate preview/plan integrity;
- launch identity, creative sequence и account context;
- resumable execution, manual review и итоговым launch result;
- validation бизнес-входов до browser/Meta side effects.

## Инварианты

- Preview и execution используют один immutable plan identity.
- Create/duplicate после `UNKNOWN` не повторяются вслепую.
- Money и account context подтверждены свежими server-side evidence.
- Browser-agent исполняет transport-шаг, но не определяет бизнес-успех.
- Mobile может запускать, возобновлять и отменять run, но сложное создание
  остаётся desktop-first.

## Glossary

- **Offer** — доменная конфигурация продукта, GEO, page/pixel и economics.
- **Launch plan** — валидированный immutable набор будущих Meta-объектов.
- **Launch identity** — ключ, связывающий retry/resume с тем же планом.
- **Manual review** — явная остановка run, требующая решения оператора.
