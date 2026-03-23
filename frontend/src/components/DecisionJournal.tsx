import { Badge } from "./Badge";
import { EmptyState } from "./EmptyState";
import {
  formatDateTime,
  formatDecisionExecutionState,
  formatDecisionHuman,
  formatMoney,
  formatRelativeStatus,
  resolveDecisionExecutionState,
} from "../lib/format";
import { getBadgeTone } from "../lib/helpers";
import type { DecisionItem } from "../types";

type DecisionJournalProps = {
  decisions: DecisionItem[];
  emptyTitle: string;
  emptyDescription: string;
  compact?: boolean;
  maxItems?: number;
};

function getExecutionTone(state: string): "neutral" | "good" | "warn" | "bad" | "info" {
  switch (state) {
    case "SUCCEEDED":
      return "good";
    case "FAILED":
      return "bad";
    case "PENDING":
      return "info";
    case "SKIPPED_BY_MODE":
      return "warn";
    default:
      return "neutral";
  }
}

export function DecisionJournal({
  decisions,
  emptyTitle,
  emptyDescription,
  compact = false,
  maxItems,
}: DecisionJournalProps) {
  const visibleDecisions = typeof maxItems === "number" ? decisions.slice(0, maxItems) : decisions;

  if (visibleDecisions.length === 0) {
    return <EmptyState title={emptyTitle} description={emptyDescription} />;
  }

  return (
    <div className={`decision-journal${compact ? " decision-journal--compact" : ""}`}>
      <div className="decision-journal__head">
        <span>Объявление</span>
        <span>Решение</span>
        <span>Действие</span>
        <span>CPA</span>
        <span>Время</span>
      </div>
      {visibleDecisions.map((decision) => {
        const executionState = resolveDecisionExecutionState(decision);
        return (
          <article key={decision.id} className="decision-journal__row">
            <div className="decision-journal__primary">
              <strong className="mono">{decision.fb_ad_id}</strong>
              <div className="decision-journal__title">{formatDecisionHuman(decision.decision, decision.reason)}</div>
              <div className="muted">
                Скан {decision.scan_run_id}
                {decision.rule_id ? ` · Правило ${decision.rule_id}` : ""}
              </div>
            </div>
            <Badge tone={getBadgeTone(decision.decision)}>{formatRelativeStatus(decision.decision)}</Badge>
            <Badge tone={getExecutionTone(executionState)}>
              {formatDecisionExecutionState(executionState)}
            </Badge>
            <span>{formatMoney(decision.resolved_cpa_usd)}</span>
            <span>{formatDateTime(decision.created_at)}</span>
          </article>
        );
      })}
    </div>
  );
}
