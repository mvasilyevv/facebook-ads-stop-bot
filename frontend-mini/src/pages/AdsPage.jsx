import React, { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { fetchJson } from "../api.js";
import Loader from "../components/Loader.jsx";
import ErrorBox from "../components/ErrorBox.jsx";
import EmptyState from "../components/EmptyState.jsx";
import Card from "../components/Card.jsx";
import { haptic } from "../theme.js";
import { useNetworkStatus } from "../App.jsx";

const STATE_LABELS = {
  NORMAL: "Норма",
  WARNING_SENT: "Предупреждение",
  STOP_SENT: "Стоп",
  CLAIMED: "Ожидает OFF",
  DISABLED: "Отключено",
  ARCHIVED: "Архив",
};

function fmt$(v) {
  if (v == null || v === "") return "—";
  return "$" + Number(v).toFixed(2);
}

function fmtN(v) {
  if (v == null || v === "") return "—";
  return String(Number(v));
}

function timeAgo(isoStr) {
  if (!isoStr) return "—";
  const diff = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000);
  if (diff < 60) return `${diff}с`;
  const m = Math.floor(diff / 60);
  if (m < 60) return `${m}м`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}ч`;
  return `${Math.floor(h / 24)}д`;
}

function adCardClass(state) {
  if (state === "STOP_SENT" || state === "CLAIMED") return "ad-card ad-card-stop";
  if (state === "WARNING_SENT") return "ad-card ad-card-warning";
  if (state === "DISABLED" || state === "ARCHIVED") return "ad-card ad-card-disabled";
  return "ad-card ad-card-normal";
}

function StateBadge({ state }) {
  const cls =
    state === "STOP_SENT" || state === "CLAIMED"
      ? "badge badge-stop"
      : state === "WARNING_SENT"
      ? "badge badge-warning"
      : state === "DISABLED" || state === "ARCHIVED"
      ? "badge badge-disabled"
      : "badge badge-normal";
  return <span className={cls}>{STATE_LABELS[state] ?? state}</span>;
}

const FILTERS = [
  { id: "", label: "Все" },
  { id: "STOP_SENT", label: "Стоп" },
  { id: "WARNING_SENT", label: "Предупреждение" },
  { id: "NORMAL", label: "Норма" },
  { id: "DISABLED", label: "Отключено" },
];

// Страница объявлений — мобильные карточки
export default function AdsPage() {
  const navigate = useNavigate();
  const isOnline = useNetworkStatus();
  const [ads, setAds] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [stateFilter, setStateFilter] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [lastScanAt, setLastScanAt] = useState(null);

  const load = useCallback(async () => {
    try {
      const [adsData, statsData] = await Promise.all([
        fetchJson("/dashboard/ads?limit=200"),
        fetchJson("/dashboard/stats").catch(() => null),
      ]);
      setAds(Array.isArray(adsData) ? adsData : []);
      setLastScanAt(statsData?.last_scan_at ?? null);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    // Автообновление каждые 15 секунд
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, [load]);

  const filtered = ads.filter((ad) => {
    // 1. Фильтр по статусу
    if (stateFilter && (ad.alert_state || "NORMAL") !== stateFilter) {
      return false;
    }
    // 2. Фильтр по поисковой строке
    if (searchQuery.trim()) {
      const query = searchQuery.toLowerCase();
      const nameMatch = (ad.ad_name || "").toLowerCase().includes(query);
      const idMatch = (ad.fb_ad_id || "").toLowerCase().includes(query);
      const campaignMatch = (ad.campaign_name || "").toLowerCase().includes(query);
      const adsetMatch = (ad.adset_name || "").toLowerCase().includes(query);
      const offerMatch = (ad.offer_code || "").toLowerCase().includes(query);
      return nameMatch || idMatch || campaignMatch || adsetMatch || offerMatch;
    }
    return true;
  });

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
        <h1 style={{ marginBottom: 0 }}>Объявления</h1>
        {lastScanAt && <span className="hint">скан {timeAgo(lastScanAt)} назад</span>}
      </div>

      {error && <ErrorBox message={error} onRetry={load} />}

      {/* Фильтры */}
      <div className="filter-row" style={{ marginBottom: 10 }}>
        {FILTERS.map((f) => (
          <button
            key={f.id}
            className={`filter-chip${stateFilter === f.id ? " active" : ""}`}
            onClick={() => { haptic.selection(); setStateFilter(f.id); }}
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* Поиск по объявлениям */}
      <div className="search-input-wrap">
        <input
          type="text"
          className="search-input"
          placeholder="Поиск по имени, кампании, офферу..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
        />
        {searchQuery && (
          <button className="search-input-clear" onClick={() => setSearchQuery("")}>
            ✕
          </button>
        )}
      </div>

      <p className="hint" style={{ marginBottom: 10 }}>
        Показано: {filtered.length} из {ads.length}
      </p>

      {loading && ads.length === 0 ? (
        <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
          <div className="skeleton-card" />
          <div className="skeleton-card" />
          <div className="skeleton-card" />
          <div className="skeleton-card" />
        </div>
      ) : filtered.length === 0 ? (
        <Card>
          <EmptyState icon="📋" title="Объявлений не найдено" />
        </Card>
      ) : (
        filtered.map((ad) => {
          const state = ad.alert_state || "NORMAL";
          const rules = [
            ...(ad.stop_rule_codes || []).map((c) => ({ code: c, type: "stop" })),
            ...(ad.warning_rule_codes || []).map((c) => ({ code: c, type: "warn" })),
          ];
          return (
            <div key={ad.fb_ad_id} className={adCardClass(state)} onClick={() => { haptic.selection(); navigate(`/ads/${ad.fb_ad_id}`); }} style={{ cursor: "pointer" }}>
              <div className="ad-card-header">
                <div style={{ flex: 1, minWidth: 0 }}>
                  {ad.campaign_name && (
                    <div
                      className="hint"
                      style={{
                        fontSize: 11,
                        lineHeight: 1.25,
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        marginBottom: 1,
                      }}
                      title={ad.campaign_name}
                    >
                      📁 {ad.campaign_name}
                    </div>
                  )}
                  {ad.adset_name && (
                    <div
                      className="hint"
                      style={{
                        fontSize: 11,
                        lineHeight: 1.25,
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        marginBottom: 2,
                      }}
                      title={ad.adset_name}
                    >
                      🎯 {ad.adset_name}
                    </div>
                  )}
                  <div className="ad-name">
                    {ad.ad_name || ad.fb_ad_id}
                  </div>
                </div>
                <StateBadge state={state} />
              </div>

              {ad.offer_code && (
                <span
                  style={{
                    fontFamily: "monospace",
                    fontSize: 11,
                    background: "rgba(10,132,255,0.14)",
                    color: "var(--tg-link-color)",
                    borderRadius: 4,
                    padding: "1px 6px",
                    marginBottom: 6,
                    display: "inline-block",
                  }}
                >
                  {ad.offer_code}
                </span>
              )}

              <div className="ad-metrics">
                <div className="metric-chip">
                  <div className="label">Расход</div>
                  <div className="value">{fmt$(ad.spend)}</div>
                </div>
                <div className="metric-chip">
                  <div className="label">CPC</div>
                  <div className="value">{fmt$(ad.cpc)}</div>
                </div>
                <div className="metric-chip">
                  <div className="label">Лиды</div>
                  <div className="value">{fmtN(ad.leads)}</div>
                </div>
                <div className="metric-chip">
                  <div className="label">Деп</div>
                  <div
                    className="value"
                    style={{
                      color:
                        (ad.effective_deposits ?? ad.deposits) === 0 && Number(ad.spend) > 0
                          ? "var(--color-danger)"
                          : "inherit",
                    }}
                  >
                    {fmtN(ad.effective_deposits ?? ad.deposits)}
                  </div>
                </div>
              </div>

              {rules.length > 0 && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 8 }}>
                  {rules.slice(0, 5).map((r) => (
                    <span
                      key={r.code}
                      style={{
                        fontSize: 10,
                        fontWeight: 600,
                        padding: "1px 5px",
                        borderRadius: 4,
                        background:
                          r.type === "stop"
                            ? "rgba(255,69,58,0.14)"
                            : "rgba(255,214,10,0.14)",
                        color:
                          r.type === "stop"
                            ? "var(--color-danger)"
                            : "var(--color-warning)",
                      }}
                    >
                      {r.code}
                    </span>
                  ))}
                </div>
              )}
            </div>
          );
        })
      )}

      <button className="btn btn-secondary" onClick={load} style={{ marginTop: 8 }} disabled={!isOnline}>
        {!isOnline ? "Офлайн-режим" : "Обновить"}
      </button>
    </div>
  );
}
