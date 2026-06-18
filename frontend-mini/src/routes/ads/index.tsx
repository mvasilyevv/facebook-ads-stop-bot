/**
 * AdsPage — список объявлений под канон ads-mini.jsx.
 * MiniHeader → поиск + горизонтальные чип-фильтры → список строк AdRow.
 * Мультивыбор состояний, клиентский поиск по useMemo, лимит 120 строк.
 */
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState, useMemo } from "react";
import { Search, ChevronRight } from "lucide-react";
import {
  alertStateCssVar,
  deriveGeoFromNames,
  formatSpend,
  normalizeAlertState,
  ALERT_STATE_LABELS,
} from "@fb/shared";
import type { AdSnapshot } from "@fb/shared";
import { useDashboardAds } from "@/lib/api";
import { haptic } from "@/lib/tg";
import { MiniHeader } from "@/components/layout/MiniHeader";
import { Skeleton } from "@/components/ui";
import { cn } from "@/lib/cn";

export const Route = createFileRoute("/ads/")({
  component: AdsPage,
});

// ─── Константы фильтров ───────────────────────────────────────────────────────

/** Канонические id фильтров совпадают с alert_state в БД */
const STATE_FILTERS: { id: string; label: string }[] = [
  { id: "normal",       label: "Норма"    },
  { id: "warning_sent", label: "Предупр." },
  { id: "stop_sent",    label: "Стоп"     },
  { id: "claimed",      label: "В работе" },
  { id: "disabled",     label: "Откл."    },
];

// fsmColor удалён: подстановка state в имя токена давала несуществующий
// var(--fsm-warning_sent) → невидимая точка для warning/stop (баг).
// Канонический маппинг state→токен — alertStateCssVar из @fb/shared.

// ─── Строка объявления ────────────────────────────────────────────────────────

interface AdRowProps {
  ad: AdSnapshot;
  onClick: () => void;
}

function AdRow({ ad, onClick }: AdRowProps) {
  const state = normalizeAlertState(ad.alert_state);
  const stateLabel = ALERT_STATE_LABELS[state];
  const stateColor = alertStateCssVar(state);

  // Имя объявления (fallback → fb_ad_id)
  const name = ad.ad_name ?? ad.fb_ad_id;

  // Гео из имени кампании/adset — единый алгоритм с web (@fb/shared).
  const geoCode = deriveGeoFromNames(ad.campaign_name, ad.adset_name);

  // Spend и CPL из metrics
  const spend  = ad.metrics?.spend  != null ? parseFloat(String(ad.metrics.spend))         : null;
  const cpl    = ad.metrics?.cost_per_lead != null ? parseFloat(String(ad.metrics.cost_per_lead)) : null;
  const cplHigh = cpl != null && cpl > 30;

  return (
    <button
      type="button"
      onClick={() => {
        haptic.selection();
        onClick();
      }}
      className="w-full text-left bg-transparent border-none active:bg-bg-2"
      style={{
        display:             "grid",
        gridTemplateColumns: "40px 1fr auto auto",
        gap:                 10,
        alignItems:          "center",
        padding:             "10px 14px",
        minHeight:           44,
        cursor:              "pointer",
        font:                "inherit",
        color:               "inherit",
        borderBottom:        "1px solid var(--hairline)",
      }}
    >
      {/* Гео-плашка */}
      <div
        style={{
          width:           40,
          height:          26,
          background:      "var(--bg-2)",
          border:          "1px solid var(--hairline)",
          borderRadius:    "var(--radius-1)",
          display:         "flex",
          alignItems:      "center",
          justifyContent:  "center",
          flexShrink:      0,
        }}
      >
        <span
          className="font-display tabular-nums"
          style={{ fontSize: 8, color: "var(--bg-8)" }}
        >
          {geoCode}
        </span>
      </div>

      {/* Имя + состояние */}
      <div style={{ minWidth: 0 }}>
        <div
          className="font-display"
          style={{
            fontSize:     13,
            color:        "var(--bg-11)",
            overflow:     "hidden",
            textOverflow: "ellipsis",
            whiteSpace:   "nowrap",
          }}
        >
          {name}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 4 }}>
          <span
            aria-hidden
            style={{
              width:        6,
              height:       6,
              borderRadius: "50%",
              background:   stateColor,
              flexShrink:   0,
            }}
          />
          <span style={{ fontSize: 11, color: "var(--bg-9)" }}>{stateLabel}</span>
        </div>
      </div>

      {/* Spend + CPL */}
      <div style={{ textAlign: "right" }}>
        <div
          className="font-display tabular-nums"
          style={{ fontSize: 13, color: "var(--bg-11)" }}
        >
          {spend != null ? formatSpend(spend) : "—"}
        </div>
        <div
          className="font-display tabular-nums"
          style={{
            fontSize:  11,
            color:     cplHigh ? "var(--danger)" : "var(--bg-9)",
            marginTop: 3,
          }}
        >
          {cpl != null ? `CPL ${formatSpend(cpl)}` : "—"}
        </div>
      </div>

      {/* Шеврон */}
      <ChevronRight size={14} style={{ color: "var(--bg-8)", flexShrink: 0 }} />
    </button>
  );
}

// extractGeo удалён — заменён на deriveGeoFromNames из @fb/shared (единый
// алгоритм с web: KNOWN_GEOS + токенизация; старый regex не находил «CR2_GH»).

// ─── Skeleton-строка ──────────────────────────────────────────────────────────

function AdRowSkeleton() {
  return (
    <div
      style={{
        display:             "grid",
        gridTemplateColumns: "40px 1fr auto auto",
        gap:                 10,
        alignItems:          "center",
        padding:             "10px 14px",
        minHeight:           44,
        borderBottom:        "1px solid var(--hairline)",
      }}
    >
      <Skeleton className="h-[26px] w-[40px]" />
      <div className="space-y-1.5">
        <Skeleton className="h-3 w-3/4" />
        <Skeleton className="h-2.5 w-1/3" />
      </div>
      <div className="space-y-1.5 text-right">
        <Skeleton className="h-3 w-12" />
        <Skeleton className="h-2.5 w-10" />
      </div>
      <Skeleton className="h-3 w-3" />
    </div>
  );
}

// ─── Основная страница ────────────────────────────────────────────────────────

function AdsPage() {
  const navigate = useNavigate();
  const [search, setSearch]   = useState("");
  const [activeStates, setActiveStates] = useState<string[]>([]);

  // Загружаем без серверного фильтра — фильтруем на клиенте (мультивыбор)
  const { data: allAds = [], isLoading } = useDashboardAds("", "");

  // Клиентская фильтрация: состояния + поиск, сортировка по spend desc
  const rows = useMemo(() => {
    let result = allAds as AdSnapshot[];

    // Фильтр по состоянию (мультивыбор)
    if (activeStates.length > 0) {
      result = result.filter((ad) =>
        activeStates.includes(normalizeAlertState(ad.alert_state)),
      );
    }

    // Поиск: по имени, кампании, adset, офферу, fb_ad_id
    if (search.trim()) {
      const q = search.toLowerCase();
      result = result.filter((ad) => {
        const name     = (ad.ad_name ?? "").toLowerCase();
        const campaign = (ad.campaign_name ?? "").toLowerCase();
        const adset    = (ad.adset_name ?? "").toLowerCase();
        const offer    = (ad.offer_code ?? "").toLowerCase();
        const id       = ad.fb_ad_id.toLowerCase();
        return (
          name.includes(q) ||
          campaign.includes(q) ||
          adset.includes(q) ||
          offer.includes(q) ||
          id.includes(q)
        );
      });
    }

    // Сортировка по spend desc
    return [...result].sort((a, b) => {
      const sa = a.metrics?.spend != null ? parseFloat(String(a.metrics.spend)) : 0;
      const sb = b.metrics?.spend != null ? parseFloat(String(b.metrics.spend)) : 0;
      return sb - sa;
    });
  }, [allAds, activeStates, search]);

  const shown = rows.slice(0, 120);
  const overflow = rows.length - shown.length;

  /** Переключить чип состояния (повторный клик снимает) */
  const toggleState = (id: string) => {
    haptic.selection();
    setActiveStates((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );
  };

  return (
    <div className="flex flex-col">
      {/* ── Шапка ── */}
      <MiniHeader
        eyebrowNum="04"
        eyebrow="УПРАВЛЕНИЕ"
        title="Объявления"
        right={
          <span
            className="font-display tabular-nums"
            style={{ fontSize: 12, color: "var(--bg-9)" }}
          >
            {rows.length.toLocaleString("en-US")}
          </span>
        }
      />

      {/* ── Поиск и фильтры (обычный блок под шапкой) ── */}
      <div className="px-4 pt-3 pb-0 border-b border-[var(--hairline)]">
        {/* Поле поиска */}
        <div style={{ position: "relative", marginBottom: 10 }}>
          <Search
            size={15}
            style={{
              position:  "absolute",
              left:      10,
              top:       "50%",
              transform: "translateY(-50%)",
              color:     "var(--bg-9)",
              flexShrink: 0,
            }}
          />
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Поиск"
            aria-label="Поиск по объявлениям"
            className={cn(
              "w-full outline-none",
              "bg-bg-2 border border-[var(--hairline-strong)] rounded-[var(--radius-2)]",
              "text-bg-11 placeholder:text-bg-8",
              "font-display text-[14px]",
            )}
            style={{
              height:     40,
              padding:    "0 10px 0 34px",
              boxSizing:  "border-box",
            }}
          />
        </div>

        {/* Горизонтальный ряд чипов состояний */}
        <div
          role="group"
          aria-label="Фильтр по состоянию"
          style={{
            display:    "flex",
            gap:        6,
            overflowX:  "auto",
            paddingBottom: 10,
          }}
        >
          {STATE_FILTERS.map((f) => {
            const on = activeStates.includes(f.id);
            return (
              <button
                key={f.id}
                type="button"
                aria-pressed={on}
                onClick={() => toggleState(f.id)}
                className="font-display"
                style={{
                  flexShrink:  0,
                  height:      30,
                  padding:     "0 12px",
                  borderRadius: "9999px",
                  border:      `1px solid ${on ? "var(--accent)" : "var(--hairline-strong)"}`,
                  background:  on ? "var(--accent-bg)" : "transparent",
                  color:       on ? "var(--accent)" : "var(--bg-10)",
                  font:        "inherit",
                  fontSize:    12,
                  fontWeight:  500,
                  cursor:      "pointer",
                  display:     "inline-flex",
                  alignItems:  "center",
                  gap:         6,
                }}
              >
                <span
                  aria-hidden
                  style={{
                    width:        6,
                    height:       6,
                    borderRadius: "50%",
                    background:   `var(--fsm-${f.id})`,
                    flexShrink:   0,
                  }}
                />
                {f.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* ── Список строк ── */}
      <div>
        {isLoading ? (
          // Skeleton при загрузке
          [...Array(6)].map((_, i) => <AdRowSkeleton key={i} />)
        ) : shown.length === 0 ? (
          // Пустое состояние
          <div className="py-10 text-center">
            <p style={{ fontSize: 13, color: "var(--bg-9)" }}>Ничего не найдено</p>
          </div>
        ) : (
          <>
            {shown.map((ad) => (
              <AdRow
                key={ad.fb_ad_id}
                ad={ad}
                onClick={() =>
                  void navigate({
                    to:     "/ads/$fbAdId",
                    params: { fbAdId: ad.fb_ad_id },
                  })
                }
              />
            ))}
            {overflow > 0 && (
              <div
                style={{
                  padding:   16,
                  textAlign: "center",
                  fontSize:  12,
                  color:     "var(--bg-8)",
                }}
              >
                +{overflow.toLocaleString("en-US")} ещё · уточни фильтр
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
