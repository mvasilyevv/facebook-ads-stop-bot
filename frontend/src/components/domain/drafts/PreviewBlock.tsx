/**
 * PreviewBlock — mono key=value блок для create_campaign и bulk-pause.
 *
 * create_campaign: иерархические секции Campaign → Adset → Creative + Ad.
 * bulk-pause: список affected ads + selector + агрегат.
 *
 * Данные берутся из DraftOut.payload напрямую.
 * Компонент декларативный — рендерит произвольные секции через prop.
 */

import { cn } from "@/lib/utils/cn";

// ─── Типы ─────────────────────────────────────────────────────────────────────

/** Одна строка key=value. */
export interface PreviewEntry {
  key: string;
  value: string;
  /** Акцентный цвет значения (для objective/cta/статусов). */
  accent?: boolean;
  /** Комментарий (серым рядом с value). */
  comment?: string;
}

/** Секция (Campaign / Adset / Affected ads …). */
export interface PreviewSection {
  title: string;
  entries: PreviewEntry[];
}

/** Вид — список объявлений (bullet). */
export interface BulletList {
  label: string;
  items: string[];
}

interface PreviewBlockProps {
  sections?: PreviewSection[];
  bullets?: BulletList[];
  className?: string;
}

// ─── Компонент ─────────────────────────────────────────────────────────────────

export function PreviewBlock({ sections = [], bullets = [], className }: PreviewBlockProps) {
  return (
    <div
      className={cn(
        "bg-bg-0 border border-bg-5",
        "p-4 font-display text-[12px] text-bg-10 leading-[1.7]",
        className,
      )}
    >
      {/* Bullet-списки (affected ads) */}
      {bullets.map((bl, bi) => (
        <div key={bi}>
          {/* Лейбл секции */}
          <span className="block text-[10px] tracking-[0.14em] uppercase text-bg-8 mb-1 mt-0 first:mt-0">
            {bl.label}
          </span>
          {bl.items.map((item, ii) => (
            <div key={ii}>
              <span className="text-bg-7">·</span>{" "}
              <span className="text-bg-11">{item}</span>
            </div>
          ))}
        </div>
      ))}

      {/* Key=value секции */}
      {sections.map((sec, si) => (
        <div key={si}>
          <span
            className={cn(
              "block text-[10px] tracking-[0.14em] uppercase text-bg-8 mb-1",
              // Отступ между секциями
              si > 0 || bullets.length > 0 ? "mt-3" : "mt-0",
            )}
          >
            {sec.title}
          </span>
          {sec.entries.map((entry, ei) => (
            <div key={ei}>
              <span className="text-bg-9">{entry.key}</span>
              {" = "}
              <span className={entry.accent ? "text-accent" : "text-bg-11"}>
                {entry.value}
              </span>
              {entry.comment ? (
                <span className="text-bg-8 ml-2">// {entry.comment}</span>
              ) : null}
            </div>
          ))}
        </div>
      ))}

      {/* Пусто */}
      {sections.length === 0 && bullets.length === 0 && (
        <span className="text-bg-8">—</span>
      )}
    </div>
  );
}

// ─── Хелперы-билдеры из payload ───────────────────────────────────────────────

/**
 * Строит PreviewBlock-props для create_campaign payload.
 * Читает известные поля — неизвестные игнорирует (safe).
 */
export function buildCreateCampaignPreview(
  payload: Record<string, unknown>,
): PreviewBlockProps {
  const p = payload;

  const campaignEntries: PreviewEntry[] = [
    p["campaign_name"] != null && { key: "name", value: String(p["campaign_name"]) },
    p["objective"] != null && { key: "objective", value: String(p["objective"]), accent: true },
    p["status"] != null && {
      key: "status",
      value: String(p["status"]),
      comment: "safe by default",
    },
  ].filter(Boolean) as PreviewEntry[];

  const adsetEntries: PreviewEntry[] = [
    p["adset_name"] != null && { key: "name", value: String(p["adset_name"]) },
    p["daily_budget"] != null && {
      key: "daily_budget",
      value: `$${Number(p["daily_budget"]).toFixed(2)}`,
    },
    p["budget_cents"] != null && {
      key: "daily_budget",
      value: `$${(Number(p["budget_cents"]) / 100).toFixed(2)}`,
    },
    p["optimization_goal"] != null && {
      key: "optimization_goal",
      value: String(p["optimization_goal"]),
      accent: true,
    },
    p["geo"] != null && { key: "geo", value: String(p["geo"]) },
    p["age_range"] != null && { key: "age_range", value: String(p["age_range"]) },
    p["platforms"] != null && {
      key: "platforms",
      value: Array.isArray(p["platforms"]) ? p["platforms"].join(", ") : String(p["platforms"]),
    },
  ].filter(Boolean) as PreviewEntry[];

  const creativeEntries: PreviewEntry[] = [
    p["primary_text"] != null && {
      key: "primary_text",
      value: `"${p["primary_text"]}"`,
    },
    p["headline"] != null && { key: "headline", value: `"${p["headline"]}"` },
    p["cta"] != null && { key: "cta", value: String(p["cta"]), accent: true },
    p["landing_url"] != null && { key: "landing", value: String(p["landing_url"]) },
  ].filter(Boolean) as PreviewEntry[];

  const sections: PreviewSection[] = [
    campaignEntries.length > 0 && {
      title: "01 · Campaign",
      entries: campaignEntries,
    },
    adsetEntries.length > 0 && {
      title: "02 · Adset",
      entries: adsetEntries,
    },
    creativeEntries.length > 0 && {
      title: "03 · Creative + Ad",
      entries: creativeEntries,
    },
  ].filter(Boolean) as PreviewSection[];

  return { sections };
}

/**
 * Строит PreviewBlock-props для bulk_status_change payload.
 * Показывает affected ads, selector, агрегат.
 */
export function buildBulkPausePreview(
  payload: Record<string, unknown>,
): PreviewBlockProps {
  const ids = (payload["object_ids"] as string[] | undefined) ?? [];
  const offerCode = payload["offer_code"] as string | undefined;
  const action = payload["action"] as string | undefined;
  const objectType = payload["object_type"] as string | undefined;
  const totalSpend = payload["total_spend_today"] as number | undefined;
  const totalLeads = payload["total_leads_today"] as number | undefined;

  const bullets: BulletList[] = ids.length > 0
    ? [
        {
          label: `Affected ads · ${ids.length}`,
          // Ids как bullet-лист. Если есть human-readable names в payload — предпочесть.
          items: ids,
        },
      ]
    : [];

  const selectorEntries: PreviewEntry[] = [
    offerCode != null && { key: "offer_code", value: `"${offerCode}"` },
    { key: "match", value: "anchored_word_boundary" },
    { key: "scope", value: "active only" },
    objectType != null && { key: "object_type", value: objectType },
    action != null && { key: "action", value: action.toUpperCase(), accent: true },
  ].filter(Boolean) as PreviewEntry[];

  const aggrEntries: PreviewEntry[] = [
    totalSpend != null && {
      key: "total_spend_today",
      value: `$${totalSpend.toLocaleString("en-US", { minimumFractionDigits: 2 })}`,
      accent: true,
    },
    totalLeads != null && {
      key: "total_leads_today",
      value: String(totalLeads),
      accent: true,
    },
  ].filter(Boolean) as PreviewEntry[];

  const sections: PreviewSection[] = [
    { title: "Selector", entries: selectorEntries },
    aggrEntries.length > 0 && { title: "Aggregated impact", entries: aggrEntries },
  ].filter(Boolean) as PreviewSection[];

  return { sections, bullets };
}
