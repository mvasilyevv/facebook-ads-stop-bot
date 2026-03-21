import { useEffect, useState, startTransition } from "react";
import { Badge } from "../components/Badge";
import { EmptyState } from "../components/EmptyState";
import { SectionCard } from "../components/SectionCard";
import { fetchDecisions } from "../lib/api";
import { formatDateTime, formatMoney, formatRelativeStatus, formatDecisionHuman } from "../lib/format";
import { getBadgeTone } from "../lib/helpers";
import type { DecisionItem } from "../types";

export default function DecisionsPage() {
  const [decisions, setDecisions] = useState<DecisionItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function reload(silent = false) {
    if (!silent) setLoading(true);
    setError(null);
    try {
      const data = await fetchDecisions();
      startTransition(() => { setDecisions(data); setLoading(false); });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка загрузки");
      setLoading(false);
    }
  }

  useEffect(() => { void reload(); }, []);

  const visible = decisions.filter((d) =>
    `${d.fb_ad_id} ${d.rule_id ?? ""} ${d.reason} ${d.decision}`.toLowerCase().includes(search.toLowerCase())
  );

  if (loading) return <div className="page-loading">Загрузка решений...</div>;

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Решения</h1>
          <p className="page-subtitle">История would_pause / would_resume и итоговых действий</p>
        </div>
        <div className="page-header__actions">
          <input className="input input--compact" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Поиск по ad ID, правилу или причине" />
          <button type="button" className="button button--primary" onClick={() => void reload(true)}>Обновить</button>
        </div>
      </div>

      {error && <div className="inline-error">{error}</div>}

      <SectionCard title={`Решения (${visible.length})`}>
        <div className="timeline">
          {visible.length === 0 ? (
            <EmptyState title="Решений пока нет" description="Когда backend начнёт писать decisions, они появятся здесь." />
          ) : (
            visible.map((decision) => (
              <article key={decision.id} className="timeline-item">
                <div className="timeline-item__head">
                  <div>
                    <strong>Объявление #{decision.fb_ad_id}</strong>
                    <div className="muted">
                      Скан {decision.scan_run_id}
                      {decision.rule_id ? ` · Правило ${decision.rule_id}` : ""}
                    </div>
                  </div>
                  <Badge tone={getBadgeTone(decision.decision)}>{formatRelativeStatus(decision.decision)}</Badge>
                </div>
                <p>{formatDecisionHuman(decision.decision, decision.reason)}</p>
                <div className="timeline-item__meta">
                  <span>{formatDateTime(decision.created_at)}</span>
                  <span>Действие: {decision.action_executed ? "выполнено" : "не выполнялось"}</span>
                  <span>Статус: {decision.action_status ?? "—"}</span>
                  <span>CPA: {formatMoney(decision.resolved_cpa_usd)}</span>
                </div>
              </article>
            ))
          )}
        </div>
      </SectionCard>
    </>
  );
}
