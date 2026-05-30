import React, { useEffect, useState, useCallback } from "react";
import { fetchJson } from "../api.js";
import Loader from "../components/Loader.jsx";
import ErrorBox from "../components/ErrorBox.jsx";
import EmptyState from "../components/EmptyState.jsx";
import Card from "../components/Card.jsx";

function fmt$(v) {
  if (v == null || v === "") return "—";
  return "$" + Number(v).toFixed(2);
}

function fmtN(v) {
  if (v == null || v === "") return "—";
  return String(Number(v));
}

function dateDefault() {
  const end = new Date();
  const start = new Date();
  start.setDate(start.getDate() - 7);
  return {
    from: start.toISOString().slice(0, 10),
    to: end.toISOString().slice(0, 10),
  };
}

// Страница истории заливов — мобильный карточный layout
export default function HistoryPage() {
  const [dates, setDates] = useState(dateDefault());
  const [offers, setOffers] = useState([]);
  const [offerFilter, setOfferFilter] = useState("");
  const [summary, setSummary] = useState(null);
  const [campaigns, setCampaigns] = useState([]);
  const [offersStats, setOffersStats] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Загрузка списка офферов при маунте
  useEffect(() => {
    fetchJson("/offers")
      .then((data) => setOffers(Array.isArray(data) ? data : []))
      .catch(() => setOffers([]));
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    // Backend history.py ждёт from_iso/to_iso (ISO-8601). Расширяем «до» на конец
    // суток, чтобы захватить события дня целиком. offer фильтруем на клиенте —
    // history-агрегаты сервера принимают только offer_id (UUID), не offer_code.
    const params = new URLSearchParams({
      from_iso: `${dates.from}T00:00:00`,
      to_iso: `${dates.to}T23:59:59`,
    }).toString();

    try {
      const [sumRes, campRes, offRes] = await Promise.all([
        fetchJson(`/history/summary?${params}`).catch(() => null),
        fetchJson(`/history/campaigns?${params}`).catch(() => []),
        fetchJson(`/history/offers?${params}`).catch(() => []),
      ]);
      setSummary(sumRes);
      setCampaigns(Array.isArray(campRes) ? campRes : []);
      setOffersStats(Array.isArray(offRes) ? offRes : []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [dates]);

  useEffect(() => {
    load();
  }, [load]);

  // Фильтр по офферу — на клиенте (см. load): сужаем уже полученные агрегаты.
  const visibleOffers = offerFilter
    ? offersStats.filter((o) => o.offer_code === offerFilter)
    : offersStats;
  const visibleCampaigns = offerFilter
    ? campaigns.filter((c) => c.offer_code === offerFilter)
    : campaigns;

  return (
    <div>
      <h1>История</h1>

      {/* Фильтры дат */}
      <Card style={{ padding: "10px 14px" }}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 10 }}>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label className="form-label">От</label>
            <input
              className="form-input"
              type="date"
              value={dates.from}
              onChange={(e) => setDates((d) => ({ ...d, from: e.target.value }))}
            />
          </div>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label className="form-label">До</label>
            <input
              className="form-input"
              type="date"
              value={dates.to}
              onChange={(e) => setDates((d) => ({ ...d, to: e.target.value }))}
            />
          </div>
        </div>
        <div className="form-group" style={{ marginBottom: 0 }}>
          <label className="form-label">Оффер</label>
          <select
            className="form-input"
            value={offerFilter}
            onChange={(e) => setOfferFilter(e.target.value)}
          >
            <option value="">Все офферы</option>
            {offers.map((o) => (
              <option key={o.id} value={o.code}>
                {o.code}
              </option>
            ))}
          </select>
        </div>
      </Card>

      {error && <ErrorBox message={error} onRetry={load} />}

      {loading && <Loader text="Загрузка истории..." />}

      {/* KPI summary — backend отдаёт агрегаты в summary.totals.* */}
      {summary?.totals && !loading && (() => {
        const t = summary.totals;
        const spend = Number(t.spend ?? 0);
        const avgCpc = t.clicks ? spend / t.clicks : null;
        const avgCpr = t.registrations ? spend / t.registrations : null;
        return (
          <div className="hist-kpi">
            <div className="hist-kpi-item">
              <div className="hist-kpi-value">{fmt$(t.spend)}</div>
              <div className="hist-kpi-label">Расход</div>
            </div>
            <div className="hist-kpi-item">
              <div className="hist-kpi-value">{fmtN(t.leads)}</div>
              <div className="hist-kpi-label">Лиды</div>
            </div>
            <div className="hist-kpi-item">
              <div className="hist-kpi-value">{fmtN(t.registrations)}</div>
              <div className="hist-kpi-label">Реги</div>
            </div>
            <div className="hist-kpi-item">
              <div className="hist-kpi-value">{fmtN(t.deposits)}</div>
              <div className="hist-kpi-label">Депозиты</div>
            </div>
            <div className="hist-kpi-item">
              <div className="hist-kpi-value">{fmt$(avgCpc)}</div>
              <div className="hist-kpi-label">Ср. CPC</div>
            </div>
            <div className="hist-kpi-item">
              <div className="hist-kpi-value">{fmt$(avgCpr)}</div>
              <div className="hist-kpi-label">Ср. CPR</div>
            </div>
          </div>
        );
      })()}

      {/* По офферам */}
      {visibleOffers.length > 0 && !loading && (
        <Card title="По офферам">
          {visibleOffers.map((o, i) => (
            <div key={o.offer_code ?? i} className="hist-row">
              <div className="hist-row-name">{o.offer_code ?? "—"}</div>
              <div className="hist-row-meta">
                <span>{fmt$(o.total_spend ?? o.spend)}</span>
                <span>Лидов: {fmtN(o.total_leads ?? o.leads)}</span>
                <span>Деп: {fmtN(o.total_deposits ?? o.deposits)}</span>
              </div>
            </div>
          ))}
        </Card>
      )}

      {/* По кампаниям */}
      {visibleCampaigns.length > 0 && !loading && (
        <Card title={`Кампании (${visibleCampaigns.length})`}>
          {visibleCampaigns.slice(0, 20).map((c, i) => (
            <div key={c.campaign_name ?? i} className="hist-row">
              <div
                className="hist-row-name"
                style={{
                  overflow: "hidden",
                  whiteSpace: "nowrap",
                  textOverflow: "ellipsis",
                  maxWidth: "100%",
                }}
              >
                {c.campaign_name ?? "—"}
              </div>
              <div className="hist-row-meta">
                <span>{fmt$(c.total_spend ?? c.spend)}</span>
                <span>Лидов: {fmtN(c.total_leads ?? c.leads)}</span>
                <span>Деп: {fmtN(c.total_deposits ?? c.deposits)}</span>
              </div>
            </div>
          ))}
          {visibleCampaigns.length > 20 && (
            <p className="hint" style={{ marginTop: 8 }}>
              +{visibleCampaigns.length - 20} кампаний
            </p>
          )}
        </Card>
      )}

      {!loading && !summary && visibleCampaigns.length === 0 && visibleOffers.length === 0 && (
        <Card>
          <EmptyState icon="📅" title="Нет данных" subtitle="Нет данных за выбранный период" />
        </Card>
      )}
    </div>
  );
}
