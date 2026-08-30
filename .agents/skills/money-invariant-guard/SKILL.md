---
name: money-invariant-guard
description: Guard rail for edits on the FB_Agent money path - anything that can pause, activate, duplicate or delete an ad, spend budget, set a spend cap, or navigate the live cabinet tab. Use before editing core/enable_reco, core/observer, core/rules, apps/meta_api_worker, apps/autopause_worker, core/meta_api, services/browser-agent session handling, or any file whose diff touches a comment marked money / ВАЖНО (money) / fencing.
---

# Охрана money-инвариантов

Большинство модулей FB_Agent можно починить потом. Несколько решают, продолжают ли
тратиться настоящие деньги, и держат этот факт в виде комментария в коде, а не в типах.
Такие комментарии несущие: это единственная запись об инварианте, за который уже заплачено.

## Когда включается

Правка затрагивает что-то из списка:

- `core/enable_reco/**`, `core/observer/**`, `core/rules/**`
- `apps/meta_api_worker/**`, `apps/autopause_worker/**`, `apps/enable_recommendation_worker/**`
- `core/meta_api/**`, `core/tasks/queue.py`, `core/models/tasks/**`
- `services/browser-agent/src/session-manager.ts`, `.../index.ts` (выбор primary-вкладки)
- любой файл, где diff убирает или меняет строку рядом с
  `ВАЖНО (money)`, `# money:`, `fencing`, `lease_owner`, `lease_token`, `spend_cap`, `outcome`

## Порядок

1. **Назвать инвариант до патча.** Одно предложение в отчёте: «сегодня держится X,
   после правки держится Y». Не формулируется — значит, правка ещё не понята.

2. **Прочитать комментарий, который собираешься обесценить.** Реальный случай:

   ```python
   # ВАЖНО (money): НЕ передаём ad_account_id — риск порвать живую вкладку кабинета
   ```

   Передать `ad_account_id`, «чтобы self-heal нашёл вкладку», — это разворот именно того
   решения. Продуктовый вопрос, а не рефакторинг: нужен владелец или роль `buyer`.

3. **Не ослаблять предохранитель ради прохода.** Две конкретные формы запрещены:
   - возвращать `{}` / `None` из fencing-хелпера, когда поля нет, вместо отказа
     (`_task_fence` не имеет права молча терять `lease_owner`);
   - удалять или инвертировать регресс, который этот инвариант и фиксирует
     (см. `python-testing-patterns`, «Красный тест — зафиксированное поведение»).

4. **Кап абсолютный, не инкрементный.** Любой предел расхода абсолютен на сутки кабинета
   (`cap = CPA`), а не `уже_потрачено + CPA`. Если расход уже выше капа, правильное действие —
   вообще не ставить grace.

5. **Перепроверка на внешней границе.** Money-задача может пролежать в очереди столько,
   что spend, CPA и статус объявления успеют измениться. Перечитывать их непосредственно
   перед вызовом Meta; после мутации проверять поздно.

6. **Старое состояние падает закрыто.** Меняешь формат маркера или payload — версионируй
   и отвергай неверсионные остатки, а не трактуй их оптимистично.

7. **Сначала регресс.** Тест, который поймал бы эту ошибку, пишется до фикса, и его имя
   называет инвариант.

## Эскалация

Изменение стоп-правил, порогов и всего, что решает судьбу объявления, не проходит мимо роли
`buyer`, а money-путь — мимо `eng-safety` (`docs/agents/engineering-team.md`). Техническое
согласие не отменяет их отказ.
