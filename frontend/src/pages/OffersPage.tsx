import { FormEvent, useEffect, useState, startTransition } from "react";
import { Badge } from "../components/Badge";
import { EmptyState } from "../components/EmptyState";
import { SectionCard } from "../components/SectionCard";
import { fetchOffers, createOffer, createOfferRate, createOfferBinding } from "../lib/api";
import { formatMoney } from "../lib/format";
import type { OfferItem } from "../types";

const emptyOfferForm = { code: "", name: "", isActive: true };
const emptyRateForm = { offerId: "", cpaUsd: "", effectiveFrom: "", note: "" };
const emptyBindingForm = {
  targetType: "adset" as "adset" | "ad",
  entityId: "",
  offerId: "",
  priority: "0",
  isActive: true,
};

export default function OffersPage() {
  const [offers, setOffers] = useState<OfferItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [offerForm, setOfferForm] = useState(emptyOfferForm);
  const [rateForm, setRateForm] = useState(emptyRateForm);
  const [bindingForm, setBindingForm] = useState(emptyBindingForm);

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
    await runAction(
      () => createOffer({ code: offerForm.code, name: offerForm.name, is_active: offerForm.isActive }),
      "Оффер создан",
    );
    setOfferForm(emptyOfferForm);
  }

  async function onSubmitRate(event: FormEvent) {
    event.preventDefault();
    if (!rateForm.offerId) { setMessage("Выберите оффер для ставки"); return; }
    await runAction(
      () => createOfferRate(rateForm.offerId, {
        cpa_usd: rateForm.cpaUsd,
        effective_from: rateForm.effectiveFrom,
        note: rateForm.note || undefined,
      }),
      "Ставка оффера сохранена",
    );
    setRateForm(emptyRateForm);
  }

  async function onSubmitBinding(event: FormEvent) {
    event.preventDefault();
    if (!bindingForm.offerId || !bindingForm.entityId) { setMessage("Заполните оффер и ID"); return; }
    await runAction(
      () => createOfferBinding({
        path: bindingForm.targetType,
        entityId: bindingForm.entityId,
        offerId: bindingForm.offerId,
        priority: Number(bindingForm.priority) || 0,
        isActive: bindingForm.isActive,
      }),
      "Оффер привязан",
    );
    setBindingForm(emptyBindingForm);
  }

  if (loading) return <div className="page-loading">Загрузка офферов...</div>;

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Офферы</h1>
          <p className="page-subtitle">Создание офферов, ставок и привязок к adset/ad</p>
        </div>
        <div className="page-header__actions">
          <input className="input input--compact" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Поиск по коду или названию" />
          <button type="button" className="button button--primary" onClick={() => void reload(true)}>Обновить</button>
        </div>
      </div>

      {message && <div className="message-banner">{message}</div>}

      <SectionCard title="Формы управления">
        <div className="offers-grid">
          <form className="panel-form" onSubmit={(e) => void onSubmitOffer(e)}>
            <h3>Новый оффер</h3>
            <input className="input" value={offerForm.code} onChange={(e) => setOfferForm((c) => ({ ...c, code: e.target.value }))} placeholder="Код оффера" />
            <input className="input" value={offerForm.name} onChange={(e) => setOfferForm((c) => ({ ...c, name: e.target.value }))} placeholder="Название оффера" />
            <label className="checkbox">
              <input type="checkbox" checked={offerForm.isActive} onChange={(e) => setOfferForm((c) => ({ ...c, isActive: e.target.checked }))} />
              <span>Оффер активен</span>
            </label>
            <button type="submit" className="button button--primary">Создать оффер</button>
          </form>

          <form className="panel-form" onSubmit={(e) => void onSubmitRate(e)}>
            <h3>Новая ставка</h3>
            <select className="input" value={rateForm.offerId} onChange={(e) => setRateForm((c) => ({ ...c, offerId: e.target.value }))}>
              <option value="">Выберите оффер</option>
              {visible.map((o) => <option key={o.id} value={o.id}>{o.code} · {o.name}</option>)}
            </select>
            <input className="input" value={rateForm.cpaUsd} onChange={(e) => setRateForm((c) => ({ ...c, cpaUsd: e.target.value }))} placeholder="CPA, например 5.00" />
            <input className="input" value={rateForm.effectiveFrom} onChange={(e) => setRateForm((c) => ({ ...c, effectiveFrom: e.target.value }))} placeholder="effective_from" />
            <input className="input" value={rateForm.note} onChange={(e) => setRateForm((c) => ({ ...c, note: e.target.value }))} placeholder="Комментарий" />
            <button type="submit" className="button button--primary">Сохранить ставку</button>
          </form>

          <form className="panel-form panel-form--wide" onSubmit={(e) => void onSubmitBinding(e)}>
            <h3>Привязка оффера</h3>
            <div className="form-grid">
              <select className="input" value={bindingForm.offerId} onChange={(e) => setBindingForm((c) => ({ ...c, offerId: e.target.value }))}>
                <option value="">Выберите оффер</option>
                {visible.map((o) => <option key={o.id} value={o.id}>{o.code} · {o.name}</option>)}
              </select>
              <select className="input" value={bindingForm.targetType} onChange={(e) => setBindingForm((c) => ({ ...c, targetType: e.target.value as "adset" | "ad" }))}>
                <option value="adset">Адсет</option>
                <option value="ad">Объявление</option>
              </select>
            </div>
            <div className="form-grid">
              <input className="input" value={bindingForm.entityId} onChange={(e) => setBindingForm((c) => ({ ...c, entityId: e.target.value }))} placeholder="ID сущности" />
              <input className="input" value={bindingForm.priority} onChange={(e) => setBindingForm((c) => ({ ...c, priority: e.target.value }))} placeholder="Приоритет" />
            </div>
            <label className="checkbox">
              <input type="checkbox" checked={bindingForm.isActive} onChange={(e) => setBindingForm((c) => ({ ...c, isActive: e.target.checked }))} />
              <span>Активная привязка</span>
            </label>
            <button type="submit" className="button button--primary">Привязать</button>
          </form>
        </div>
      </SectionCard>

      <SectionCard title={`Список офферов (${visible.length})`}>
        <div className="offer-list">
          {visible.length === 0 ? (
            <EmptyState title="Офферы не загружены" description="После ответа backend появится список офферов." />
          ) : (
            visible.map((offer) => (
              <article key={offer.id} className="offer-card">
                <div className="offer-card__head">
                  <div>
                    <strong>{offer.name}</strong>
                    <div className="muted">{offer.code}</div>
                  </div>
                  <Badge tone={offer.is_active ? "good" : "warn"}>{offer.is_active ? "активен" : "неактивен"}</Badge>
                </div>
                <div className="offer-card__stats">
                  <span>Текущая CPA: {formatMoney(offer.current_cpa_usd)}</span>
                </div>
              </article>
            ))
          )}
        </div>
      </SectionCard>
    </>
  );
}
