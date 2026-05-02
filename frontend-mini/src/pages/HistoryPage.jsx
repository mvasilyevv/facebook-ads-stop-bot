import React, { useEffect, useState, useCallback } from "react";
import { fetchJson } from "../api.js";

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
    const params = new URLSearchParams({
      date_from: dates.from,
      date_to: dates.to,
      ...(offerFilter ? { offer_code: offerFilter } : {}),
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
  }, [dates, offerFilter]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div>
      <h1>История</h1>

      {/* Фильтры дат */}
      <div className="card" style={{ padding: "10px 14px" }}>
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
      </div>

      {error && (
        <div className="card" style={{ borderLeft: "3px solid var(--color-danger)" }}>
          <span className="status-error">{error}</span>
        </div>
      )}

      {loading && <div className="loading">Загрузка истории...</div>}

      {/* KPI summary */}
      {summary && !loading && (
        <div className="hist-kpi">
          <div className="hist-kpi-item">
            <div className="hist-kpi-value">{fmt$(summary.total_spend)}</div>
            <div className="hist-kpi-label">Общий расход</div>
          </div>
          <div className="hist-kpi-item">
            <div className="hist-kpi-value">{fmtN(summary.total_leads)}</div>
            <div className="hist-kpi-label">Лиды</div>
          </div>
          <div className="hist-kpi-item">
            <div className="hist-kpi-value">{fmtN(summary.total_registrations)}</div>
            <div className="hist-kpi-label">Реги</div>
          </div>
          <div className="hist-kpi-item">
            <div className="hist-kpi-value">{fmtN(summary.total_deposits)}</div>
            <div className="hist-kpi-label">Депозиты</div>
          </div>
          <div className="hist-kpi-item">
            <div className="hist-kpi-value">{fmt$(summary.avg_cpc)}</div>
            <div className="hist-kpi-label">Ср. CPC</div>
          </div>
          <div className="hist-kpi-item">
            <div className="hist-kpi-value">{fmt$(summary.avg_cpr)}</div>
            <div className="hist-kpi-label">Ср. CPR</div>
          </div>
        </div>
      )}

      {/* По офферам */}
      {offersStats.length > 0 && !loading && (
        <div className="card">
          <div className="card-title">По офферам</div>
          {offersStats.map((o, i) => (
            <div key={o.offer_code ?? i} className="hist-row">
              <div className="hist-row-name">{o.offer_code ?? "—"}</div>
              <div className="hist-row-meta">
                <span>{fmt$(o.total_spend ?? o.spend)}</span>
                <span>Лидов: {fmtN(o.total_leads ?? o.leads)}</span>
                <span>Деп: {fmtN(o.total_deposits ?? o.deposits)}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* По кампаниям */}
      {campaigns.length > 0 && !loading && (
        <div className="card">
          <div className="card-title">Кампании ({campaigns.length})</div>
          {campaigns.slice(0, 20).map((c, i) => (
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
          {campaigns.length > 20 && (
            <p className="hint" style={{ marginTop: 8 }}>
              +{campaigns.length - 20} кампаний
            </p>
          )}
        </div>
      )}

      {!loading && !summary && campaigns.length === 0 && offersStats.length === 0 && (
        <div className="card" style={{ textAlign: "center", padding: "32px 16px" }}>
          <p className="hint">Нет данных за выбранный период</p>
        </div>
      )}
    </div>
  );
}
