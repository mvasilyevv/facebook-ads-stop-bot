/**
 * Страница «Кампании» (блок 01 OPERATE) — скоуп наблюдения.
 *
 * Вынесено из Settings → Observer: Owner Campaign Tag (какие кампании «мои»)
 * + отслеживаемые кампании (allowlist). Определяет, что именно сканирует бот.
 */

import { useState, useEffect, type FC } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { RefreshCw } from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { Card } from "@/components/ui/Card";
import { TagListInput } from "@/components/ui/TagListInput";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { toast } from "@/components/ui/Toast";
import {
  useObserverSettings,
  useUpdateObserverSettings,
  useObserverCampaigns,
  useRefreshObserverCampaigns,
  useSetCampaignAllowlist,
} from "@/lib/api/settings";

export const Route = createFileRoute("/campaigns/")({
  component: CampaignsPage,
});

function CampaignsPage() {
  return (
    <>
      <PageHeader eyebrowNum="01" eyebrow="OPERATE · СКОУП" title="Кампании" />
      <div className="space-y-6" style={{ maxWidth: 720 }}>
        <OwnerTagCard />
        <CampaignAllowlist />
      </div>
    </>
  );
}

// ─── Owner Campaign Tag ───────────────────────────────────────────────────────

const OwnerTagCard: FC = () => {
  const { data, isLoading, error, refetch } = useObserverSettings();
  const updateMut = useUpdateObserverSettings();
  // Тэги как список (на бэке хранятся одной строкой через запятую).
  const [tags, setTags] = useState<string[]>([]);

  useEffect(() => {
    if (data) {
      const raw = data.owner_campaign_tag ?? "";
      setTags(
        raw
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean),
      );
    }
  }, [data]);

  if (isLoading) return <Skeleton className="h-32 w-full" />;
  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />;

  // PUT требует ПОЛНЫЙ body (все обязательные поля), иначе 422.
  const handleSave = async () => {
    try {
      await updateMut.mutateAsync({
        is_scanning_enabled: data?.is_scanning_enabled ?? false,
        auto_enable_recommendations: data?.auto_enable_recommendations ?? false,
        default_interval_seconds: data?.default_interval_seconds ?? 30,
        owner_campaign_tag: tags.length ? tags.join(",") : null,
      });
      toast.success("Owner Tag сохранён");
    } catch (e) {
      toast.error("Ошибка сохранения", e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <Card eyebrow="OWNER CAMPAIGN TAG" padded>
      <div className="text-[12px] text-bg-9 mb-3">
        Тег(и) в названии кампании, помечающие «мои» кампании в общем кабинете. Добавляй по одному
        (Enter), × — удалить. Пусто — сканируются все кампании.
      </div>
      <TagListInput
        id="owner-tag"
        aria-label="Owner Campaign Tag"
        placeholder="MV + Enter"
        values={tags}
        onChange={setTags}
      />
      <div className="flex justify-end mt-3">
        <Button variant="primary" onClick={() => void handleSave()} loading={updateMut.isPending}>
          Сохранить
        </Button>
      </div>
    </Card>
  );
};

// ─── Отслеживаемые кампании (allowlist) ───────────────────────────────────────

const CampaignAllowlist: FC = () => {
  const { data: campaigns, isLoading } = useObserverCampaigns();
  const refreshMut = useRefreshObserverCampaigns();
  const saveMut = useSetCampaignAllowlist();
  const [selected, setSelected] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (campaigns) {
      setSelected(new Set(campaigns.filter((c) => c.selected).map((c) => c.id)));
    }
  }, [campaigns]);

  const toggle = (id: string) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  const handleRefresh = async () => {
    try {
      await refreshMut.mutateAsync();
      toast.success("Список кампаний обновлён");
    } catch (e) {
      toast.error("Не удалось обновить список", e instanceof Error ? e.message : String(e));
    }
  };

  const handleSaveAllowlist = async () => {
    try {
      await saveMut.mutateAsync([...selected]);
      toast.success("Выбор кампаний сохранён");
    } catch (e) {
      toast.error("Ошибка сохранения выбора", e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <Card padded>
      <div className="flex items-center justify-between mb-2">
        <div className="font-display text-[10px] tracking-[0.12em] uppercase text-bg-8">
          ОТСЛЕЖИВАЕМЫЕ КАМПАНИИ
        </div>
        <Button
          variant="secondary"
          size="sm"
          leftIcon={<RefreshCw size={13} />}
          onClick={() => void handleRefresh()}
          loading={refreshMut.isPending}
        >
          Обновить список
        </Button>
      </div>
      <div className="text-[11px] text-bg-8 mb-3">
        Пусто (ничего не выбрано) — сканируются все кампании по Owner Tag. Выбор сужает скан до
        отмеченных. «Обновить список» тянет кампании из кабинета живьём через browser-agent.
      </div>

      {isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : !campaigns || campaigns.length === 0 ? (
        <div
          className="text-[12px] text-bg-8 border border-[var(--hairline)] rounded-[var(--radius-2)]"
          style={{ padding: "var(--s-4)" }}
        >
          Кампаний нет. Нажми «Обновить список» — резолвим из кабинета по Owner Tag.
        </div>
      ) : (
        <div
          className="border border-[var(--hairline)] rounded-[var(--radius-2)] overflow-hidden"
          style={{ maxHeight: 320, overflowY: "auto" }}
        >
          {campaigns.map((c) => (
            <label
              key={c.id}
              className="text-[13px] text-bg-10"
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: "8px 12px",
                borderBottom: "1px solid var(--hairline)",
                cursor: "pointer",
              }}
            >
              <input
                type="checkbox"
                checked={selected.has(c.id)}
                onChange={() => toggle(c.id)}
                aria-label={`Отслеживать ${c.name}`}
              />
              <span style={{ flex: 1 }}>{c.name || c.id}</span>
              <span className="font-display tabular-nums text-[11px] text-bg-7">
                …{c.id.slice(-4)}
              </span>
            </label>
          ))}
        </div>
      )}

      <div style={{ marginTop: "var(--s-4)" }}>
        <Button
          variant="primary"
          onClick={() => void handleSaveAllowlist()}
          loading={saveMut.isPending}
          disabled={!campaigns || campaigns.length === 0}
        >
          Сохранить выбор ({selected.size})
        </Button>
      </div>
    </Card>
  );
};
