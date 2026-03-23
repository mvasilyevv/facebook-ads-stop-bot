import { useEffect, useMemo, useState, startTransition } from "react";
import { EmptyState } from "../components/EmptyState";
import { GroupedAdsBoard } from "../components/GroupedAdsBoard";
import { SectionCard } from "../components/SectionCard";
import { useAutoRefresh } from "../hooks/useAutoRefresh";
import { isAttentionAdSummary } from "../lib/helpers";
import { blockAd, fetchAds, unblockAd } from "../lib/api";
import { formatMoney } from "../lib/format";
import type { AdSummary } from "../types";
import { useOperatorScope } from "../context/OperatorScopeContext";

type AdsFilter = "all" | "attention" | "active" | "paused";

function formatRiskBandLabel(value: AdSummary["risk_band"]): string {
  switch (value) {
    case "SAFE":
      return "без риска";
    case "WATCH":
      return "наблюдение";
    case "STOP":
      return "стоп";
    default:
      return String(value).toLowerCase();
  }
}

function formatFastStopStateLabel(value: AdSummary["fast_stop_state"]): string {
  switch (value) {
    case "IDLE":
      return "нет";
    case "WATCH":
      return "наблюдение";
    case "STOP":
      return "стоп";
    case "QUEUED":
      return "в очереди";
    case "RUNNING":
      return "в работе";
    case "PAUSED":
      return "на паузе";
    case "FAILED":
      return "ошибка";
    default:
      return String(value).toLowerCase();
  }
}

function formatActionStatusLabel(value: NonNullable<AdSummary["queued_action_status"]>): string {
  switch (value) {
    case "QUEUED":
      return "в очереди";
    case "RUNNING":
      return "в работе";
    case "RETRYING":
      return "повтор";
    case "SUCCEEDED":
      return "выполнено";
    case "FAILED":
      return "ошибка";
    case "CANCELLED":
      return "отменено";
    default:
      return String(value).toLowerCase();
  }
}

function toNumber(value: string | number | null | undefined): number {
  if (value == null || value === "") {
    return 0;
  }
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

export default function AdsPage() {
  const scope = useOperatorScope();
  const [ads, setAds] = useState<AdSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [blockReason, setBlockReason] = useState("Ручная блокировка через UI");
  const [filter, setFilter] = useState<AdsFilter>("all");
  const [message, setMessage] = useState<string | null>(null);
  const profileId = scope?.selectedProfileId ?? null;
  const launchId = scope?.selectedLaunchId ?? null;
  const selectedLaunch = scope?.selectedLaunch ?? null;
  const isArchiveLaunch = selectedLaunch != null && !selectedLaunch.is_active;

  async function reload(silent = false) {
    if (!silent) {
      setLoading(true);
    }
    setMessage(null);
    try {
      const data = await fetchAds(
        profileId && launchId
          ? {
              profileId,
              profileLaunchId: launchId,
            }
          : undefined,
      );
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
  }, [profileId, launchId]);

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
  const watchCount = useMemo(() => ads.filter((ad) => ad.risk_band === "WATCH").length, [ads]);
  const stopCount = useMemo(() => ads.filter((ad) => ad.risk_band === "STOP").length, [ads]);
  const queuedCount = useMemo(
    () =>
      ads.filter((ad) =>
        ad.queued_action_status === "QUEUED" ||
        ad.queued_action_status === "RUNNING" ||
        ad.queued_action_status === "RETRYING",
      ).length,
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
  const fastStopAds = useMemo(
    () =>
      [...ads]
        .filter((ad) => ad.risk_band !== "SAFE" || ad.queued_action_status != null || ad.fast_stop_state !== "IDLE")
        .sort((left, right) => {
          const bandScore = { SAFE: 0, WATCH: 1, STOP: 2 } as const;
          const leftScore = bandScore[left.risk_band];
          const rightScore = bandScore[right.risk_band];
          if (leftScore !== rightScore) {
            return rightScore - leftScore;
          }
          return right.priority_score - left.priority_score;
        })
        .slice(0, 8),
    [ads],
  );

  if (loading) {
    return <div className="page-loading">Загрузка объявлений...</div>;
  }

  if (scope && !profileId) {
    return (
      <EmptyState
        title="Профиль не выбран"
        description="Выберите профиль и запуск в верхней панели, чтобы открыть объявления нужного периода."
      />
    );
  }

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Объявления</h1>
          <p className="page-subtitle">
            {selectedLaunch
              ? `${selectedLaunch.name}${isArchiveLaunch ? " · архивный просмотр" : ""}`
              : "Плиточный обзор кампаний, групп объявлений и самих ads"}
          </p>
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
      {isArchiveLaunch ? (
        <div className="message-banner">
          Открыт архивный запуск. Карточки доступны только для просмотра, ручные действия отключены.
        </div>
      ) : null}

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
        <article className="metric-tile">
          <span>Наблюдение</span>
          <strong>{watchCount}</strong>
          <div className="mini-row">
            <span>Стоп</span>
            <span>{stopCount}</span>
          </div>
        </article>
        <article className="metric-tile">
          <span>В очереди</span>
          <strong>{queuedCount}</strong>
          <div className="mini-row">
            <span>Последняя реакция</span>
            <span>{isArchiveLaunch ? "архив" : "актуально"}</span>
          </div>
        </article>
      </div>

      <SectionCard title="Быстрый стоп" subtitle="Рискованные объявления, причины и очередь действий">
        {fastStopAds.length === 0 ? (
          <EmptyState
            title="Рискованных объявлений пока нет"
            description="Когда быстрый стоп найдёт объявления в наблюдении или стопе, здесь появятся ключевые поля и статусы."
          />
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Объявление</th>
                  <th>Риск</th>
                  <th>Быстрый стоп</th>
                  <th>Очередь</th>
                  <th>Приоритет</th>
                  <th>Причина</th>
                </tr>
              </thead>
              <tbody>
                {fastStopAds.map((ad) => (
                  <tr key={ad.fb_ad_id}>
                    <td>
                      <div className="mono" title={ad.fb_ad_id}>
                        {ad.fb_ad_id}
                      </div>
                      <div className="section-note">{ad.ad_name}</div>
                    </td>
                    <td>{formatRiskBandLabel(ad.risk_band)}</td>
                    <td>{formatFastStopStateLabel(ad.fast_stop_state)}</td>
                    <td>{ad.queued_action_status ? formatActionStatusLabel(ad.queued_action_status) : "—"}</td>
                    <td>{ad.priority_score}</td>
                    <td>{ad.watch_reason || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>

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
        {!isArchiveLaunch ? (
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
        ) : null}
        {visibleAds.length === 0 ? (
          <EmptyState
            title="Объявлений нет"
            description="После первого скана выбранного запуска здесь появится список ads."
          />
        ) : (
          <GroupedAdsBoard
            ads={visibleAds}
            emptyTitle="Объявлений нет"
            emptyDescription="После первого скана выбранного запуска здесь появится список ads."
            onBlock={
              isArchiveLaunch
                ? undefined
                : (ad) =>
                    void runAction(
                      () => blockAd(ad.fb_ad_id, blockReason),
                      `${ad.fb_ad_id} заблокировано`,
                    )
            }
            onUnblock={
              isArchiveLaunch
                ? undefined
                : (ad) =>
                    void runAction(
                      () => unblockAd(ad.fb_ad_id),
                      `${ad.fb_ad_id} разблокировано`,
                    )
            }
            blockReason={blockReason}
          />
        )}
      </SectionCard>
    </>
  );
}
