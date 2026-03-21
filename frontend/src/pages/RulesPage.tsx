import { useEffect, useState, startTransition } from "react";
import { EmptyState } from "../components/EmptyState";
import { SectionCard } from "../components/SectionCard";
import { RuleEditor, type RuleOfferPreview } from "../components/RuleEditor";
import { fetchOffers, fetchRules, saveRule } from "../lib/api";
import type { OfferItem, RuleItem } from "../types";

export default function RulesPage() {
  const [rules, setRules] = useState<RuleItem[]>([]);
  const [offers, setOffers] = useState<OfferItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [message, setMessage] = useState<string | null>(null);

  async function reload(silent = false) {
    if (!silent) setLoading(true);
    try {
      const [rulesData, offersData] = await Promise.all([
        fetchRules(),
        fetchOffers(),
      ]);
      startTransition(() => {
        setRules(rulesData);
        setOffers(offersData);
        setLoading(false);
      });
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
  const offerPreviews: RuleOfferPreview[] = offers
    .filter((offer) => offer.is_active && offer.current_cpa_usd != null)
    .map((offer) => ({
      offerId: offer.id,
      offerName: offer.name,
      offerCode: offer.code,
      cpaUsd: Number(offer.current_cpa_usd),
    }))
    .filter((item): item is RuleOfferPreview => item !== null);

  if (loading) return <div className="page-loading">Загрузка правил...</div>;

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Правила</h1>
          <p className="page-subtitle">Порог считается автоматически от CPA оффера, здесь настраивается только процент</p>
        </div>
        <div className="page-header__actions">
          <input className="input input--compact" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Поиск по коду, названию..." />
          <button type="button" className="button button--primary" onClick={() => void reload(true)}>Обновить</button>
        </div>
      </div>

      {message && <div className="message-banner">{message}</div>}

      <div className="message-banner">
        Выбери процент от CPA, при котором бот должен остановить объявление. Ниже показаны реальные пороги по активным офферам, которые бот находит по имени объявления.
      </div>

      <SectionCard title={`Правила (${visible.length})`}>
        <div className="stack">
          {visible.length === 0 ? (
            <EmptyState title="Правила не загружены" description="Список правил появится после ответа backend." />
          ) : (
            visible.map((rule) => (
              <RuleEditor
                key={rule.id}
                rule={rule}
                offerPreviews={offerPreviews}
                onSave={(payload) => handleSave(rule.id, rule.code, payload)}
              />
            ))
          )}
        </div>
      </SectionCard>
    </>
  );
}
