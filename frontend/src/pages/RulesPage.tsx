import { useEffect, useState, startTransition } from "react";
import { EmptyState } from "../components/EmptyState";
import { SectionCard } from "../components/SectionCard";
import { RuleEditor } from "../components/RuleEditor";
import { fetchRules, saveRule } from "../lib/api";
import type { RuleItem } from "../types";

export default function RulesPage() {
  const [rules, setRules] = useState<RuleItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [message, setMessage] = useState<string | null>(null);

  async function reload(silent = false) {
    if (!silent) setLoading(true);
    try {
      const data = await fetchRules();
      startTransition(() => { setRules(data); setLoading(false); });
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Ошибка загрузки");
      setLoading(false);
    }
  }

  useEffect(() => { void reload(); }, []);

  async function handleSave(ruleId: string, code: string, payload: Partial<RuleItem>) {
    setMessage(null);
    try {
      await saveRule(ruleId, payload);
      setMessage(`Правило ${code} сохранено`);
      await reload(true);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Не удалось сохранить");
    }
  }

  const visible = rules.filter((r) =>
    `${r.code} ${r.title} ${r.description ?? ""}`.toLowerCase().includes(search.toLowerCase())
  );

  if (loading) return <div className="page-loading">Загрузка правил...</div>;

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Правила</h1>
          <p className="page-subtitle">Редактируемые stop-метрики и CPA-множители</p>
        </div>
        <div className="page-header__actions">
          <input className="input input--compact" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Поиск по коду, названию..." />
          <button type="button" className="button button--primary" onClick={() => void reload(true)}>Обновить</button>
        </div>
      </div>

      {message && <div className="message-banner">{message}</div>}

      <SectionCard title={`Правила (${visible.length})`}>
        <div className="stack">
          {visible.length === 0 ? (
            <EmptyState title="Правила не загружены" description="Список правил появится после ответа backend." />
          ) : (
            visible.map((rule) => (
              <RuleEditor
                key={rule.id}
                rule={rule}
                draft={{
                  title: rule.title,
                  description: rule.description ?? "",
                  is_enabled: rule.is_enabled,
                  priority: rule.priority,
                  cpa_multiplier: rule.cpa_multiplier ? String(rule.cpa_multiplier) : "",
                }}
                onSave={(payload) => handleSave(rule.id, rule.code, payload)}
              />
            ))
          )}
        </div>
      </SectionCard>
    </>
  );
}
