# Issue tracker: GitHub

Задачи и спецификации этого репозитория живут в GitHub Issues проекта
`mvasilyevv/facebook-ads-stop-bot`. Все операции выполняются через `gh` из
корня клона, чтобы репозиторий определялся по `origin`.

## Доска — обязательная сверка

Очередь и состояние работ видны на доске
<https://github.com/users/mvasilyevv/projects/2>. Правила — [board.md](board.md).

Перед тем как завести задачу, агент смотрит доску: если карточка уже есть,
он дописывает в неё, а не создаёт вторую. Взяв работу, переводит карточку в
«В работе» и назначает себя; закончив — в «Готово». Найденное по ходу, но не
сделанное, заводится карточкой сразу.

```bash
gh project item-list 2 --owner mvasilyevv --format json
gh project item-edit --id <item> --project-id PVT_kwHOBErqos4Bg0k7 \
  --field-id PVTSSF_lAHOBErqos4Bg0k7zhfyrmY --single-select-option-id <статус>
```

Каждая задача получает метки `area:*` и `prio:*` и те же значения в полях
«Область» и «Приоритет». Задача, трогающая и бэкенд, и интерфейс, делится на
две связанные карточки.

## Основные операции

- Создать issue: `gh issue create --title "..." --body "..."`. Для
  многострочного body использовать heredoc.
- Прочитать issue: `gh issue view <number> --comments` с получением labels.
- Получить очередь: `gh issue list --state open --json number,title,body,labels,comments`.
- Добавить комментарий: `gh issue comment <number> --body "..."`.
- Изменить labels: `gh issue edit <number> --add-label "..."` или
  `--remove-label "..."`.
- Закрыть: `gh issue close <number> --comment "..."`.

## Pull requests как входящая очередь

**PRs as a request surface: no.** Внешние PR не считаются задачами для triage,
пока это значение явно не будет изменено на `yes`.

GitHub использует общее пространство номеров для issues и PR. Для неоднозначного
`#42` сначала выполнить `gh pr view 42`, затем при отсутствии PR —
`gh issue view 42`.

## Контракты навыков

- «Publish to the issue tracker» означает создать GitHub issue.
- «Fetch the relevant ticket» означает выполнить
  `gh issue view <number> --comments`.
- Merge, production release и money-действия не выполняются автоматически:
  они остаются за человеком согласно `CLAUDE.md`.

## Wayfinding

- Карта — один issue с label `wayfinder:map`, содержащий Notes,
  Decisions-so-far и Fog.
- Дочерняя задача — GitHub sub-issue карты. Если sub-issues недоступны,
  использовать task list в карте и строку `Part of #<map>` в дочернем issue.
- Тип задачи задаётся label `wayfinder:research`, `wayfinder:prototype`,
  `wayfinder:grilling` или `wayfinder:task`.
- Блокировки представлены native issue dependencies. Если API dependencies
  недоступен, использовать строку `Blocked by: #<n>, #<n>` в начале body.
- Claim выполняется через `gh issue edit <n> --add-assignee @me` и является
  первой записью агента во внешнее состояние.
- Resolve: добавить итоговый комментарий, закрыть дочерний issue и записать
  ссылку на контекст в Decisions-so-far карты.
