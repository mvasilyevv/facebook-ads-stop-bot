/**
 * /ads/$fbAdId — deep-link drawer деталей объявления.
 *
 * Грузит snapshot из списка (useAds кэш) и рендерит AdDrawer.
 * Закрытие → navigate к /ads.
 */

import { createFileRoute, useRouter, useParams } from "@tanstack/react-router";
import { useMemo } from "react";

import { AdDrawer } from "@/components/domain/ads/AdDrawer";
import { useAds, useAdTimeline } from "@/lib/api/ads";
import type { AdSnapshot } from "@fb/shared";

export const Route = createFileRoute("/ads/$fbAdId")({
  component: AdDetailRoute,
});

function AdDetailRoute() {
  const router = useRouter();
  const { fbAdId } = useParams({ from: "/ads/$fbAdId" });

  function close() {
    void router.navigate({ to: "/ads" });
  }

  // Ищем snapshot в общем списке (обычно уже в кэше).
  const { data, isLoading: adsLoading } = useAds({ limit: 1000, offset: 0 });
  const fromList = useMemo(
    () => data?.data.find((a) => a.fb_ad_id === fbAdId) ?? null,
    [data, fbAdId],
  );

  // Фолбэк: timeline для холодного deep-link без кэша.
  const { data: timeline, isLoading: timelineLoading } = useAdTimeline(fbAdId, {
    include_metrics: true,
    include_alerts: true,
    include_tasks: false,
  });

  const ad: AdSnapshot | null = useMemo(() => {
    if (fromList) return fromList;
    if (!timeline) return null;
    return {
      fb_ad_id: timeline.fb_ad_id,
      internal_id: timeline.internal_id,
      ad_name: timeline.ad_name,
      campaign_name: timeline.campaign_name ?? null,
      adset_name: timeline.adset_name ?? null,
      offer_code: timeline.offer_code ?? null,
      offer_id: null,
      alert_state: "normal",
      is_active: true,
      last_seen_at: null,
      stop_rule_codes: [],
      warning_rule_codes: [],
      metrics: null,
    } satisfies AdSnapshot;
  }, [fromList, timeline]);

  const isLoading = adsLoading || (timelineLoading && !fromList);

  return <AdDrawer ad={ad} onClose={close} isLoading={isLoading} fbAdId={fbAdId} />;
}
