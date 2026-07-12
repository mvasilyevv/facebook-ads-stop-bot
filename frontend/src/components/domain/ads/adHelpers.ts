/**
 * Хелперы Ads-страницы: парсинг метрик из AdSnapshot, флаги-пороги,
 * извлечение гео из имени, money-форматтер «1 знак» (как в эталоне).
 *
 * Контракт данных (AdSnapshotOut.metrics → MetricsBlock):
 *   spend / ctr / cpm / frequency / cost_per_lead — строки (Decimal → str),
 *   leads / deposits / impressions / clicks / reach — числа.
 *   ROAS в схеме НЕТ → всегда null (рендерим «—», не фейк).
 */

import { deriveGeoFromNames, formatSpend1, type AdSnapshot } from "@fb/shared";

// ─── Числовой парс ────────────────────────────────────────────────────────────

/** Безопасный парс строки/числа в number|null. Пустое/NaN → null. */
export function num(v: string | number | null | undefined): number | null {
  if (v == null || v === "") return null;
  const n = typeof v === "string" ? Number.parseFloat(v) : v;
  return Number.isNaN(n) ? null : n;
}

// ─── Мульти-кабинет ──────────────────────────────────────────────────────────

/**
 * Кабинет объявления (числовой ID без act_). Поле есть в generated-типах
 * (AdSnapshotOut.ad_account_id) — читаем напрямую (M-26: устаревший каст убран).
 */
export function adAccountId(ad: AdSnapshot): string | null {
  return ad.ad_account_id ?? null;
}

/**
 * Короткий вид ID кабинета для узких колонок: «…1234» (последние 4 цифры).
 * Арбитражники различают кабинеты по хвосту; полный ID — в title/tooltip.
 */
export function shortAccountId(id: string): string {
  return id.length > 6 ? `…${id.slice(-4)}` : id;
}

// ─── Родитель объявления (кампания → адсет) ─────────────────────────────────

/** Имя в кабинете — пайп-делимитед: «MV | GH_CR | static | adset.pro | 18.06». */
function splitName(name: string | null | undefined): string[] {
  return (name ?? "")
    .split("|")
    .map((s) => s.trim())
    .filter(Boolean);
}

/** Читаемый «родитель»: контекст кампании + различающий хвост адсета. */
export interface ParentTrail {
  /** Сегменты кампании (очищены от offer_code), через « · ». Контекст, приглушён. */
  context: string;
  /** Уникальный хвост адсета после общего с кампанией префикса — различитель дублей. */
  adset: string;
  /** Полное «кампания · адсет» для tooltip. */
  full: string;
}

/**
 * Сворачивает шумные пайп-имена в читаемую пару. Глушит offer_code (он есть в
 * колонке OFFER) и общий префикс campaign∩adset (повтор owner/типа): контекст
 * кампании показываем целиком, у адсета — только хвост, отличающий его от кампании
 * (и от соседних дублей с тем же ad_name).
 */
export function parentTrail(ad: AdSnapshot): ParentTrail {
  const offer = (ad.offer_code ?? "").trim().toLowerCase();
  const keep = (s: string) => s.toLowerCase() !== offer;
  const camp = splitName(ad.campaign_name).filter(keep);
  const adset = splitName(ad.adset_name).filter(keep);

  let prefix = 0;
  while (prefix < camp.length && prefix < adset.length) {
    if (camp[prefix]!.toLowerCase() !== adset[prefix]!.toLowerCase()) break;
    prefix++;
  }

  // Хвост адсета: после общего префикса + без сегментов, уже показанных в кампании
  // (напр. дата 18.06 повторяется в обоих именах) — остаётся только различитель.
  const campSet = new Set(camp.map((s) => s.toLowerCase()));
  const adsetTail = adset.slice(prefix).filter((s) => !campSet.has(s.toLowerCase()));

  return {
    context: camp.join(" · "),
    adset: adsetTail.join(" · "),
    full: `${ad.campaign_name ?? "—"} · ${ad.adset_name ?? "—"}`,
  };
}

// ─── Метрики строки таблицы ─────────────────────────────────────────────────

/** Распарсенные метрики одного объявления (number|null, ROAS всегда null). */
export interface AdMetricsView {
  spend: number | null;
  cpl: number | null;
  freq: number | null;
  cpm: number | null;
  ctr: number | null;
  /** ROAS отсутствует в API-схеме AdSnapshot → всегда null. */
  roas: number | null;
  leads: number | null;
  deposits: number | null;
}

/** Достаёт и парсит метрики из snapshot (или нули-null, если metrics нет). */
export function readAdMetrics(ad: AdSnapshot): AdMetricsView {
  const m = ad.metrics;
  return {
    spend: num(m?.spend),
    cpl: num(m?.cost_per_lead),
    freq: num(m?.frequency),
    cpm: num(m?.cpm),
    ctr: num(m?.ctr),
    roas: null, // нет в схеме
    leads: m?.leads ?? null,
    deposits: m?.deposits ?? null,
  };
}

// ─── Пороги-флаги (канон ads-web.jsx) ──────────────────────────────────────

/** CPL > 30 → danger. */
export const isCplBad = (v: number | null): boolean => v != null && v > 30;
/** FREQ > 4 → danger. */
export const isFreqBad = (v: number | null): boolean => v != null && v > 4;
/** ROAS < 1 → danger (только если значение есть). */
export const isRoasBad = (v: number | null): boolean => v != null && v < 1;

// ─── Money-форматтер «1 знак» (как в прототипе) ────────────────────────────

/**
 * $1,234.5 — формат денег с одним знаком (эталонный money() из ads-web.jsx).
 * Реэкспорт: реализация сведена в @fb/shared (formatSpend1) — единый источник
 * с mini, локальная копия убрана (аудит 02.07, LOW F1 «дубль money1()»).
 */
export const money1 = formatSpend1;

// ─── Гео из имени объявления ───────────────────────────────────────────────

/**
 * Гео-плейсхолдер для thumb. Реализация сведена в @fb/shared (deriveGeoFromNames) —
 * единый алгоритм с mini (дедуп, аудит 2026-06-09). Обёртка сохраняет прежнюю
 * сигнатуру по AdSnapshot для всех call-sites web.
 */
export function deriveGeo(ad: Pick<AdSnapshot, "ad_name" | "campaign_name">): string {
  return deriveGeoFromNames(ad.campaign_name, ad.ad_name);
}
