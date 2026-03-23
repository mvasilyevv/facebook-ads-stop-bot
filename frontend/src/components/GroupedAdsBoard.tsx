import { AdIdentity } from "./AdIdentity";
import { Badge } from "./Badge";
import { EmptyState } from "./EmptyState";
import { formatDateTime, formatMoney, formatMetricText, resolveAdActivitySummary } from "../lib/format";
import { getBadgeTone, formatDeliveryStatusLabel, isAttentionAdSummary } from "../lib/helpers";
import type { AdSummary } from "../types";

type GroupedAdsBoardProps = {
  ads: AdSummary[];
  emptyTitle: string;
  emptyDescription: string;
  compact?: boolean;
  maxCampaigns?: number;
  maxAdsetsPerCampaign?: number;
  maxAdsPerAdset?: number;
  onBlock?: (ad: AdSummary) => void;
  onUnblock?: (ad: AdSummary) => void;
  blockReason?: string;
};

type AdTile = AdSummary & {
  spendScore: number;
  lastSeenScore: number;
};

type AdsetGroup = {
  adsetName: string;
  adsetKey: string;
  ads: AdTile[];
  spendTotal: number;
  pausedAds: number;
  activeAds: number;
  attentionAds: number;
  staleAds: number;
};

type CampaignGroup = {
  campaignName: string;
  campaignKey: string;
  adsets: AdsetGroup[];
  spendTotal: number;
  totalAds: number;
  pausedAds: number;
  activeAds: number;
  attentionAds: number;
  staleAds: number;
};

function parseMoneyScore(value: string | number | null | undefined): number {
  if (value == null) {
    return Number.NEGATIVE_INFINITY;
  }
  const normalized = typeof value === "number" ? value : Number(value);
  return Number.isFinite(normalized) ? normalized : Number.NEGATIVE_INFINITY;
}

function parseDateScore(value: string | null | undefined): number {
  if (!value) {
    return 0;
  }
  const stamp = new Date(value).getTime();
  return Number.isNaN(stamp) ? 0 : stamp;
}

function compareAdsetNames(left: string, right: string): number {
  const leftValue = left.trim();
  const rightValue = right.trim();
  const leftNumber = Number(leftValue);
  const rightNumber = Number(rightValue);
  const leftIsNumber = leftValue !== "" && Number.isFinite(leftNumber);
  const rightIsNumber = rightValue !== "" && Number.isFinite(rightNumber);

  if (leftIsNumber && rightIsNumber) {
    return leftNumber - rightNumber;
  }

  return leftValue.localeCompare(rightValue, "ru", {
    numeric: true,
    sensitivity: "base",
  });
}

function getTileTone(ad: AdSummary): "neutral" | "good" | "warn" | "bad" | "info" {
  const delivery = ad.delivery_status.toUpperCase();
  if (delivery.includes("ACTIVE")) {
    return "good";
  }
  if (delivery.includes("PAUSED") || delivery.includes("LEARNING")) {
    return "warn";
  }
  if (delivery.includes("NOT_DELIVERING")) {
    return "bad";
  }
  if (ad.tracking_mode.toUpperCase().includes("MANUAL") || ad.tracking_mode.toUpperCase().includes("READ_ONLY")) {
    return "info";
  }
  return getBadgeTone(ad.last_decision);
}

function isPausedDeliveryStatus(deliveryStatus: string): boolean {
  return deliveryStatus.toUpperCase().includes("PAUSED");
}

function isActiveDeliveryStatus(deliveryStatus: string): boolean {
  return deliveryStatus.toUpperCase().includes("ACTIVE");
}

function resolveGroupBadgeTone(attentionAds: number, activeAds: number, totalAds: number) {
  if (attentionAds > 0) {
    return "warn" as const;
  }
  if (activeAds > 0) {
    return "good" as const;
  }
  if (totalAds > 0) {
    return "neutral" as const;
  }
  return "info" as const;
}

function resolveGroupBadgeLabel(attentionAds: number, activeAds: number, totalAds: number): string {
  if (attentionAds > 0) {
    return "нужна проверка";
  }
  if (activeAds > 0) {
    return "в работе";
  }
  if (totalAds > 0) {
    return "на паузе";
  }
  return "нет данных";
}

function groupAds(ads: AdSummary[]): CampaignGroup[] {
  const campaignMap = new Map<string, CampaignGroup>();

  for (const ad of ads) {
    const campaignName = ad.campaign_name?.trim() || "Кампания не определена";
    const adsetName = ad.adset_name?.trim() || "Адсет не определён";
    const campaignKey = campaignName.toLowerCase();
    const adsetKey = `${campaignKey}::${adsetName.toLowerCase()}`;
    const adItem: AdTile = {
      ...ad,
      spendScore: parseMoneyScore(ad.spend),
      lastSeenScore: parseDateScore(ad.last_seen_at),
    };

    const campaignEntry =
      campaignMap.get(campaignKey) ??
      ({
        campaignName,
        campaignKey,
        adsets: [],
        spendTotal: 0,
        totalAds: 0,
        pausedAds: 0,
        activeAds: 0,
        attentionAds: 0,
        staleAds: 0,
      } satisfies CampaignGroup);

    const adsetEntry =
      campaignEntry.adsets.find((item) => item.adsetKey === adsetKey) ??
      ({
        adsetName,
        adsetKey,
        ads: [],
        spendTotal: 0,
        pausedAds: 0,
        activeAds: 0,
        attentionAds: 0,
        staleAds: 0,
      } satisfies AdsetGroup);

    adsetEntry.ads.push(adItem);
    adsetEntry.spendTotal += adItem.spendScore > 0 ? adItem.spendScore : 0;
    adsetEntry.pausedAds += isPausedDeliveryStatus(ad.delivery_status) ? 1 : 0;
    adsetEntry.activeAds += isActiveDeliveryStatus(ad.delivery_status) ? 1 : 0;
    adsetEntry.attentionAds += isAttentionAdSummary(ad) ? 1 : 0;
    adsetEntry.staleAds += ad.scope_presence === "NOT_SEEN_THIS_SCAN" ? 1 : 0;
    campaignEntry.totalAds += 1;
    campaignEntry.spendTotal += adItem.spendScore > 0 ? adItem.spendScore : 0;
    campaignEntry.pausedAds += isPausedDeliveryStatus(ad.delivery_status) ? 1 : 0;
    campaignEntry.activeAds += isActiveDeliveryStatus(ad.delivery_status) ? 1 : 0;
    campaignEntry.attentionAds += isAttentionAdSummary(ad) ? 1 : 0;
    campaignEntry.staleAds += ad.scope_presence === "NOT_SEEN_THIS_SCAN" ? 1 : 0;

    if (!campaignEntry.adsets.some((item) => item.adsetKey === adsetKey)) {
      campaignEntry.adsets.push(adsetEntry);
    }

        campaignMap.set(campaignKey, campaignEntry);
  }

  return [...campaignMap.values()]
    .sort((left, right) => right.spendTotal - left.spendTotal)
    .map((campaign) => ({
      ...campaign,
      adsets: campaign.adsets
        .sort((left, right) => {
          const adsetDelta = compareAdsetNames(left.adsetName, right.adsetName);
          if (adsetDelta !== 0) {
            return adsetDelta;
          }
          return right.spendTotal - left.spendTotal;
        })
        .map((adset) => ({
          ...adset,
          ads: [...adset.ads].sort((left, right) => {
            const spendDelta = right.spendScore - left.spendScore;
            if (spendDelta !== 0) {
              return spendDelta;
            }
            return right.lastSeenScore - left.lastSeenScore;
          }),
        })),
    }));
}

export function GroupedAdsBoard({
  ads,
  emptyTitle,
  emptyDescription,
  compact = false,
  maxCampaigns,
  maxAdsetsPerCampaign,
  maxAdsPerAdset,
  onBlock,
  onUnblock,
  blockReason,
}: GroupedAdsBoardProps) {
  const grouped = groupAds(ads);
  const visibleCampaigns = typeof maxCampaigns === "number" ? grouped.slice(0, maxCampaigns) : grouped;

  if (visibleCampaigns.length === 0) {
    return <EmptyState title={emptyTitle} description={emptyDescription} />;
  }

  return (
    <div className={`ads-board${compact ? " ads-board--compact" : ""}`}>
      {visibleCampaigns.map((campaign) => {
        const visibleAdsets =
          typeof maxAdsetsPerCampaign === "number"
            ? campaign.adsets.slice(0, maxAdsetsPerCampaign)
            : campaign.adsets;

        return (
          <section key={campaign.campaignKey} className="ads-campaign-card">
            <div className="ads-campaign-card__head">
              <div className="ads-campaign-card__title">
                <h3>{campaign.campaignName}</h3>
                <div className="ads-summary-pills">
                  <Badge
                    tone={resolveGroupBadgeTone(
                      campaign.attentionAds,
                      campaign.activeAds,
                      campaign.totalAds,
                    )}
                  >
                    {resolveGroupBadgeLabel(
                      campaign.attentionAds,
                      campaign.activeAds,
                      campaign.totalAds,
                    )}
                  </Badge>
                  <span className="ads-summary-pill">{campaign.totalAds} объявлений</span>
                  <span className="ads-summary-pill">активно {campaign.activeAds}</span>
                  <span className="ads-summary-pill">на паузе {campaign.pausedAds}</span>
                  {campaign.attentionAds > 0 ? (
                    <span className="ads-summary-pill ads-summary-pill--warn">
                      проверить {campaign.attentionAds}
                    </span>
                  ) : null}
                  <span className="ads-summary-pill">расход {formatMoney(campaign.spendTotal)}</span>
                </div>
              </div>
              <Badge tone="info">{`${visibleAdsets.length} adset`}</Badge>
            </div>

            <div className="ads-campaign-card__body">
              {visibleAdsets.map((adset) => {
                const visibleAds =
                  typeof maxAdsPerAdset === "number" ? adset.ads.slice(0, maxAdsPerAdset) : adset.ads;

                return (
                  <div key={adset.adsetKey} className="ads-adset-block">
                    <div className="ads-adset-block__head">
                      <div className="ads-adset-block__title">
                        <strong>{adset.adsetName}</strong>
                        <div className="ads-summary-pills">
                          <span className="ads-summary-pill">{adset.ads.length} объявлений</span>
                          <span className="ads-summary-pill">активно {adset.activeAds}</span>
                          <span className="ads-summary-pill">на паузе {adset.pausedAds}</span>
                          {adset.attentionAds > 0 ? (
                            <span className="ads-summary-pill ads-summary-pill--warn">
                              проверить {adset.attentionAds}
                            </span>
                          ) : null}
                          <span className="ads-summary-pill">расход {formatMoney(adset.spendTotal)}</span>
                        </div>
                      </div>
                      {adset.staleAds > 0 ? (
                        <Badge tone="neutral">{`нет в скане ${adset.staleAds}`}</Badge>
                      ) : null}
                    </div>

                    <div className="ads-tile-grid">
                      {visibleAds.map((ad) => {
                        const tone = getTileTone(ad);
                        const activitySummary = resolveAdActivitySummary(ad);
                        const trackingIndicatorTone =
                          ad.tracking_mode.toUpperCase() === "TRACKED" ? "good" : "bad";
                        const trackingIndicatorTitle =
                          ad.tracking_mode.toUpperCase() === "TRACKED"
                            ? "Объявление отслеживается"
                            : "Объявление не в отслеживаемом режиме";
                        return (
                          <article key={ad.fb_ad_id} className={`ads-tile ads-tile--${tone}`}>
                            <div className="ads-tile__head">
                              <div className="ads-tile__badges">
                                <Badge tone={getBadgeTone(ad.delivery_status)}>
                                  {formatDeliveryStatusLabel(ad.delivery_status)}
                                </Badge>
                                {isAttentionAdSummary(ad) ? <Badge tone="warn">проверить</Badge> : null}
                                {ad.scope_presence === "NOT_SEEN_THIS_SCAN" ? (
                                  <Badge tone="neutral">нет в последнем скане</Badge>
                                ) : null}
                                <span
                                  className={`tracking-indicator tracking-indicator--${trackingIndicatorTone}`}
                                  title={trackingIndicatorTitle}
                                  aria-label={trackingIndicatorTitle}
                                />
                              </div>
                              <strong className="ads-tile__spend">{formatMoney(ad.spend)}</strong>
                            </div>

                            <AdIdentity
                              adName={ad.ad_name}
                              campaignName={ad.campaign_name}
                              adsetName={ad.adset_name}
                              fbAdId={ad.fb_ad_id}
                              showScope={false}
                            />

                            <div className={`ads-tile__metrics${compact ? " ads-tile__metrics--compact" : ""}`}>
                              <div className="ads-tile__metric">
                                <span>CPA</span>
                                <strong>{formatMoney(ad.resolved_cpa_usd)}</strong>
                              </div>
                              <div className="ads-tile__metric">
                                <span>Расход</span>
                                <strong>{formatMoney(ad.spend)}</strong>
                              </div>
                              <div className="ads-tile__metric">
                                <span>Клики</span>
                                <strong>{formatMetricText(ad.clicks)}</strong>
                              </div>
                              <div className="ads-tile__metric">
                                <span>CPC</span>
                                <strong>{formatMoney(ad.cpc)}</strong>
                              </div>
                              {!compact ? (
                                <>
                                  <div className="ads-tile__metric">
                                    <span>Лиды</span>
                                    <strong>{formatMetricText(ad.leads)}</strong>
                                  </div>
                                  <div className="ads-tile__metric">
                                    <span>Рег.</span>
                                    <strong>{formatMetricText(ad.registrations)}</strong>
                                  </div>
                                  <div className="ads-tile__metric">
                                    <span>Деп.</span>
                                    <strong>{formatMetricText(ad.deposits)}</strong>
                                  </div>
                                </>
                              ) : null}
                            </div>

                            <div className="ads-tile__activity">
                              <div className="ads-tile__activity-head">
                                <Badge tone={activitySummary.tone}>{activitySummary.label}</Badge>
                                <span className="section-note">
                                  {activitySummary.occurredAt
                                    ? `${activitySummary.occurredLabel}: ${formatDateTime(activitySummary.occurredAt)}`
                                    : "Событие ещё не зафиксировано"}
                                </span>
                              </div>
                              <div className="ads-tile__activity-text" title={activitySummary.detail}>
                                {activitySummary.detail}
                              </div>
                            </div>

                            <div className="ads-tile__footer">
                              <span className="muted">
                                {ad.last_seen_at ? `Последний скан: ${formatDateTime(ad.last_seen_at)}` : "Скан ещё не зафиксирован"}
                              </span>
                            </div>

                            {(onBlock || onUnblock) && !compact ? (
                              <div className="row-actions ads-tile__actions">
                                {onBlock ? (
                                  <button
                                    type="button"
                                    className="button button--small"
                                    onClick={() => onBlock(ad)}
                                  >
                                    Заблокировать
                                  </button>
                                ) : null}
                                {onUnblock ? (
                                  <button
                                    type="button"
                                    className="button button--small button--ghost"
                                    onClick={() => onUnblock(ad)}
                                  >
                                    Разблокировать
                                  </button>
                                ) : null}
                                {blockReason ? <span className="section-note">Причина: {blockReason}</span> : null}
                              </div>
                            ) : null}
                          </article>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        );
      })}
    </div>
  );
}
