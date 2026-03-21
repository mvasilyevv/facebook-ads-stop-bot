import { FormEvent, useEffect, useState, startTransition } from "react";
import { Badge } from "../components/Badge";
import { EmptyState } from "../components/EmptyState";
import { SectionCard } from "../components/SectionCard";
import { fetchOffers, createOffer, deleteOffer } from "../lib/api";
import { formatMoney } from "../lib/format";
import type { OfferItem } from "../types";

const emptyOfferForm = { name: "", cpaUsd: "", isActive: true };

export default function OffersPage() {
  const [offers, setOffers] = useState<OfferItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [offerForm, setOfferForm] = useState(emptyOfferForm);

  async function reload(silent = false) {
    if (!silent) setLoading(true);
    try {
      const data = await fetchOffers();
      startTransition(() => { setOffers(data); setLoading(false); });
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Ошибка загрузки");
      setLoading(false);
    }
  }

  useEffect(() => { void reload(); }, []);

  async function runAction(action: () => Promise<unknown>, successMsg: string) {
    setMessage(null);
    try {
      await action();
      setMessage(successMsg);
      await reload(true);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Операция не выполнена");
    }
  }

  const visible = offers.filter((o) =>
    `${o.code} ${o.name}`.toLowerCase().includes(search.toLowerCase())
  );

  async function onSubmitOffer(event: FormEvent) {
    event.preventDefault();
    if (!offerForm.name || !offerForm.cpaUsd) {
      setMessage("Заполните название оффера и CPA");
      return;
    }
    await runAction(
      () => createOffer({ name: offerForm.name, cpa_usd: offerForm.cpaUsd, is_active: offerForm.isActive }),
      "Оффер создан",
    );
    setOfferForm(emptyOfferForm);
  }

  async function onDeleteOffer(offer: OfferItem) {
    if (!window.confirm(`Удалить оффер «${offer.name}»?`)) {
      return;
    }
    await runAction(() => deleteOffer(offer.id), `Оффер ${offer.name} удален`);
  }

  if (loading) return <div className="page-loading">Загрузка офферов...</div>;

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Офферы</h1>
          <p className="page-subtitle">Создай оффер по коду из нейминга объявления и укажи его CPA</p>
        </div>
        <div className="page-header__actions">
          <input className="input input--compact" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Поиск по коду или названию" />
          <button type="button" className="button button--primary" onClick={() => void reload(true)}>Обновить</button>
        </div>
      </div>

      {message && <div className="message-banner">{message}</div>}

      <div className="message-banner">
        Если объявление называется `DRC_CR2_CR001`, создай оффер с названием `DRC_CR2`. Бот сам возьмет первые две части имени и сопоставит объявление с оффером.
      </div>

      <SectionCard title="Создать оффер">
        <div className="offers-grid">
          <form className="panel-form" onSubmit={(e) => void onSubmitOffer(e)}>
            <h3>Новый оффер</h3>
            <input className="input" value={offerForm.name} onChange={(e) => setOfferForm((c) => ({ ...c, name: e.target.value }))} placeholder="Название оффера, например DRC_CR2" />
            <input className="input" value={offerForm.cpaUsd} onChange={(e) => setOfferForm((c) => ({ ...c, cpaUsd: e.target.value }))} placeholder="CPA в долларах, например 5.00" />
            <label className="checkbox">
              <input type="checkbox" checked={offerForm.isActive} onChange={(e) => setOfferForm((c) => ({ ...c, isActive: e.target.checked }))} />
              <span>Оффер активен</span>
            </label>
            <button type="submit" className="button button--primary">Создать оффер и ставку</button>
          </form>
        </div>
      </SectionCard>

      <SectionCard title={`Список офферов (${visible.length})`}>
        <div className="offer-list">
          {visible.length === 0 ? (
            <EmptyState title="Офферы не загружены" description="Создай первый оффер по коду из названия объявления." />
          ) : (
            visible.map((offer) => (
              <article key={offer.id} className="offer-card">
                <div className="offer-card__head">
                  <div>
                    <strong>{offer.name}</strong>
                    <div className="muted">Автоопределение по имени объявления</div>
                  </div>
                  <Badge tone={offer.is_active ? "good" : "warn"}>{offer.is_active ? "активен" : "неактивен"}</Badge>
                </div>
                <div className="offer-card__stats">
                  <span>Текущая CPA: {formatMoney(offer.current_cpa_usd)}</span>
                  <span>Служебный код: {offer.code}</span>
                </div>
                <div className="row-actions">
                  <button type="button" className="button button--small button--ghost" onClick={() => void onDeleteOffer(offer)}>
                    Удалить оффер
                  </button>
                </div>
              </article>
            ))
          )}
        </div>
      </SectionCard>
    </>
  );
}
