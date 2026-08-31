/**
 * Список действий на экране `/actions`. Отдельный модуль: журнал действий
 * реестра и этот список нужны разным экранам, и общий файл затягивал бы один
 * в стартовый чанк вслед за другим (issue #349).
 */
import { Link } from "@tanstack/react-router";

import { ACTION_STATE_LABEL } from "@fb/shared/operator/viewModel";
import { collapseConsecutiveOperatorActions } from "@fb/shared/operator/ledgerSemantics";
import type { OperatorActionItem } from "@fb/shared/operator/contracts";

export function MiniActions({ items }: { items: OperatorActionItem[] }) {
  if (!items.length) return <MiniEmpty text="Активных действий нет." />;
  return (
    <ol className="mt-3 divide-y divide-[var(--color-hairline)]">
      {collapseConsecutiveOperatorActions(items).map(({ item, count }) => (
        <li key={item.id} className="py-3">
          <div className="flex items-baseline justify-between gap-3">
            <Link
              to="/actions/$actionId"
              params={{ actionId: item.id }}
              className="inline-flex min-h-11 min-w-0 items-center truncate rounded-[var(--radius-2)] px-1 text-[14px] font-semibold text-bg-11 underline-offset-4 focus-visible:outline-2 focus-visible:outline-accent"
            >
              {item.title}
            </Link>
            <span className="flex shrink-0 items-baseline gap-2 font-display text-[12px] text-bg-9">
              {count > 1 ? <span className="text-bg-11">×{count}</span> : null}
              <span>{item.public_id}</span>
            </span>
          </div>
          <p className="mt-1 text-[14px] text-bg-9">
            {ACTION_STATE_LABEL[item.state]} · {item.target_label ?? "система"}
          </p>
          {item.state === "unknown" ? (
            <p className="mt-1 text-[12px] text-warning">
              Результат проверяется, успех не подтверждён.
            </p>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

function MiniEmpty({ text }: { text: string }) {
  return (
    <div className="mt-3 rounded-[var(--radius-2)] border border-dashed border-[var(--color-hairline-strong)] p-4 text-center text-[14px] text-bg-9">
      {text}
    </div>
  );
}
