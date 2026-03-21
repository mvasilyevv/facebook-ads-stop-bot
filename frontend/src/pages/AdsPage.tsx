import { useEffect, useState, startTransition } from "react";
import { Badge } from "../components/Badge";
import { EmptyState } from "../components/EmptyState";
import { SectionCard } from "../components/SectionCard";
import { fetchAds, blockAd, unblockAd } from "../lib/api";
import { formatMoney, formatRelativeStatus } from "../lib/format";
import { getBadgeTone } from "../lib/helpers";
import type { AdSummary } from "../types";

export default function AdsPage() {
  const [ads, setAds] = useState<AdSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [blockReason, setBlockReason] = useState("Ручная блокировка через UI");
  const [message, setMessage] = useState<string | null>(null);

  async function reload(silent = false) {
    if (!silent) setLoading(true);
    try {
      const data = await fetchAds();
      startTransition(() => { setAds(data); setLoading(false); });
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

  const visible = ads.filter((ad) => {
    const text = `${ad.fb_ad_id} ${ad.ad_name} ${ad.adset_name} ${ad.campaign_name}`.toLowerCase();
    return text.includes(search.toLowerCase());
  });

  if (loading) return <div className="page-loading">Загрузка объявлений...</div>;

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Объявления</h1>
          <p className="page-subtitle">Статус доставки, tracking mode и ручные действия</p>
        </div>
        <div className="page-header__actions">
          <input className="input input--compact" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Поиск по ID, названию..." />
          <input className="input input--compact" value={blockReason} onChange={(e) => setBlockReason(e.target.value)} placeholder="Причина блокировки" />
          <button type="button" className="button button--primary" onClick={() => void reload(true)}>Обновить</button>
        </div>
      </div>

      {message && <div className="message-banner">{message}</div>}

      <SectionCard title={`Все объявления (${visible.length})`}>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Объявление</th>
                <th>Статус</th>
                <th>Режим</th>
                <th>CPA</th>
                <th>Последнее решение</th>
                <th>Действия</th>
              </tr>
            </thead>
            <tbody>
              {visible.length === 0 ? (
                <tr><td colSpan={6}><EmptyState title="Объявлений нет" description="После загрузки backend здесь появится список ads." /></td></tr>
              ) : (
                visible.map((ad) => (
                  <tr key={ad.fb_ad_id}>
                    <td>
                      <strong>{ad.ad_name}</strong>
                      <div className="muted">{ad.campaign_name} · {ad.adset_name}</div>
                      <div className="mono">{ad.fb_ad_id}</div>
                    </td>
                    <td><Badge tone={getBadgeTone(ad.delivery_status)}>{formatRelativeStatus(ad.delivery_status)}</Badge></td>
                    <td><Badge tone={getBadgeTone(ad.tracking_mode)}>{formatRelativeStatus(ad.tracking_mode)}</Badge></td>
                    <td>{formatMoney(ad.resolved_cpa_usd)}</td>
                    <td>{formatRelativeStatus(ad.last_decision)}</td>
                    <td>
                      <div className="row-actions">
                        <button type="button" className="button button--small" onClick={() => void runAction(() => blockAd(ad.fb_ad_id, blockReason), `${ad.fb_ad_id} заблокировано`)}>Заблокировать</button>
                        <button type="button" className="button button--small button--ghost" onClick={() => void runAction(() => unblockAd(ad.fb_ad_id), `${ad.fb_ad_id} разблокировано`)}>Разблокировать</button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </SectionCard>
    </>
  );
}
