import { useEffect, useMemo, useState, startTransition } from "react";
import { EmptyState } from "../components/EmptyState";
import { GroupedAdsBoard } from "../components/GroupedAdsBoard";
import { SectionCard } from "../components/SectionCard";
import { useAutoRefresh } from "../hooks/useAutoRefresh";
import { isAttentionAdSummary } from "../lib/helpers";
import { blockAd, fetchAds, unblockAd } from "../lib/api";
import { formatMoney } from "../lib/format";
import type { AdSummary } from "../types";

type AdsFilter = "all" | "attention" | "active" | "paused";

function toNumber(value: string | number | null | undefined): number {
  if (value == null || value === "") {
    return 0;
  }
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

export default function AdsPage() {
  const [ads, setAds] = useState<AdSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [blockReason, setBlockReason] = useState("Ручная блокировка через UI");
  const [filter, setFilter] = useState<AdsFilter>("all");
  const [message, setMessage] = useState<string | null>(null);

  async function reload(silent = false) {
    if (!silent) {
      setLoading(true);
    }
    setMessage(null);
    try {
      const data = await fetchAds();
      startTransition(() => {
        setAds(data);
        setLoading(false);
      });
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Ошибка загрузки");
      setLoading(false);
    }
  }

  useEffect(() => {
    void reload();
  }, []);

  useAutoRefresh(reload, { enabled: !loading });

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

  const visibleAds = useMemo(() => {
    const normalizedSearch = search.toLowerCase();
    return ads.filter((ad) => {
      const text = `${ad.fb_ad_id} ${ad.ad_name} ${ad.adset_name} ${ad.campaign_name}`.toLowerCase();
      if (!text.includes(normalizedSearch)) {
        return false;
      }
      if (filter === "attention") {
        return isAttentionAdSummary(ad);
      }
      if (filter === "active") {
        return ad.delivery_status.toUpperCase().includes("ACTIVE");
      }
      if (filter === "paused") {
        return ad.delivery_status.toUpperCase().includes("PAUSED");
      }
      return true;
    });
  }, [ads, filter, search]);

  const totalSpend = useMemo(
    () => visibleAds.reduce((sum, ad) => sum + toNumber(ad.spend), 0),
    [visibleAds],
  );
  const campaignCount = useMemo(
    () => new Set(visibleAds.map((ad) => ad.campaign_name)).size,
    [visibleAds],
  );
  const adsetCount = useMemo(
    () => new Set(visibleAds.map((ad) => `${ad.campaign_name}::${ad.adset_name}`)).size,
    [visibleAds],
  );
  const attentionCount = useMemo(
    () => ads.filter((ad) => isAttentionAdSummary(ad)).length,
    [ads],
  );
  const activeCount = useMemo(
    () => ads.filter((ad) => ad.delivery_status.toUpperCase().includes("ACTIVE")).length,
    [ads],
  );
  const pausedCount = useMemo(
    () => ads.filter((ad) => ad.delivery_status.toUpperCase().includes("PAUSED")).length,
    [ads],
  );

  if (loading) {
    return <div className="page-loading">Загрузка объявлений...</div>;
  }

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Объявления</h1>
          <p className="page-subtitle">Плиточный обзор кампаний, групп объявлений и самих ads</p>
        </div>
        <div className="page-header__actions">
          <input
            className="input input--compact"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Поиск по ID, названию..."
          />
          <button type="button" className="button button--primary" onClick={() => void reload(true)}>
            Обновить
          </button>
        </div>
      </div>

      {message ? <div className="message-banner">{message}</div> : null}

      <div className="metric-grid ads-summary-grid">
        <article className="metric-tile metric-tile--accent">
          <span>Показываем</span>
          <strong>{visibleAds.length}</strong>
          <div className="mini-row">
            <span>Кампаний</span>
            <span>{campaignCount}</span>
          </div>
        </article>
        <article className="metric-tile">
          <span>Групп объявлений</span>
          <strong>{adsetCount}</strong>
          <div className="mini-row">
            <span>Суммарный расход</span>
            <span>{formatMoney(totalSpend)}</span>
          </div>
        </article>
        <article className="metric-tile">
          <span>Требуют внимания</span>
          <strong>{attentionCount}</strong>
          <div className="mini-row">
            <span>Активно / пауза</span>
            <span>
              {activeCount} / {pausedCount}
            </span>
          </div>
        </article>
      </div>

      <SectionCard
        title={`Все объявления (${visibleAds.length})`}
        subtitle="Сгруппировано по кампании и adset"
        actions={
          <div className="ads-toolbar__actions">
            <div className="ads-filter-group">
              {([
                ["all", "все"],
                ["attention", `внимание ${attentionCount}`],
                ["active", `активно ${activeCount}`],
                ["paused", `на паузе ${pausedCount}`],
              ] as const).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  className={`chip${filter === value ? " chip--active" : ""}`}
                  onClick={() => setFilter(value)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        }
      >
        <div className="ads-toolbar">
          <label className="panel-form ads-toolbar__reason">
            <span>Причина ручной блокировки</span>
            <input
              className="input input--compact"
              value={blockReason}
              onChange={(event) => setBlockReason(event.target.value)}
              placeholder="Например: ручная проверка"
            />
          </label>
        </div>
        {visibleAds.length === 0 ? (
          <EmptyState title="Объявлений нет" description="После загрузки backend здесь появится список ads." />
        ) : (
          <GroupedAdsBoard
            ads={visibleAds}
            emptyTitle="Объявлений нет"
            emptyDescription="После загрузки backend здесь появится список ads."
            onBlock={(ad) => void runAction(() => blockAd(ad.fb_ad_id, blockReason), `${ad.fb_ad_id} заблокировано`)}
            onUnblock={(ad) => void runAction(() => unblockAd(ad.fb_ad_id), `${ad.fb_ad_id} разблокировано`)}
            blockReason={blockReason}
          />
        )}
      </SectionCard>
    </>
  );
}
