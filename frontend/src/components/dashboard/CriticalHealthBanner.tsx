/**
 * CriticalHealthBanner — независимый от Telegram money-critical на Dashboard.
 * Композиция намеренно утилитарная: красная сигнальная рейка, точная дельта и один
 * прямой путь в Ads Manager. Не перекрывает ScanCluster — оператор может сразу
 * запустить ручной скан или остановить мониторинг.
 */

import { ArrowUpRight, Siren } from "lucide-react";
import type { HealthDetails } from "@fb/shared";

type CriticalAlert = NonNullable<HealthDetails["critical_alerts"]>[number];

interface CriticalHealthBannerProps {
  alerts: CriticalAlert[];
}

function adsManagerUrl(accountId?: string | null): string | null {
  const normalized = String(accountId ?? "")
    .replace(/^act_/, "")
    .trim();
  return normalized
    ? `https://adsmanager.facebook.com/adsmanager/manage/ads?act=${encodeURIComponent(normalized)}`
    : null;
}

export function CriticalHealthBanner({ alerts }: CriticalHealthBannerProps) {
  const alert = alerts[0];
  if (!alert) return null;

  const url = adsManagerUrl(alert.account_id);
  const extraCount = Math.max(0, alerts.length - 1);

  return (
    <div
      role="alert"
      aria-live="assertive"
      className="relative mb-6 overflow-hidden rounded-[var(--radius-3)] border border-[color-mix(in_srgb,var(--danger)_46%,transparent)] bg-[linear-gradient(105deg,color-mix(in_srgb,var(--danger)_16%,var(--bg-1))_0%,var(--bg-1)_58%,color-mix(in_srgb,var(--danger)_7%,var(--bg-1))_100%)]"
    >
      <div className="absolute inset-y-0 left-0 w-1 bg-danger shadow-[0_0_22px_color-mix(in_srgb,var(--danger)_72%,transparent)]" />
      <div className="flex flex-col gap-4 px-5 py-4 sm:flex-row sm:items-center">
        <div className="flex min-w-0 flex-1 items-start gap-3">
          <span
            className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-full border border-[color-mix(in_srgb,var(--danger)_44%,transparent)] bg-danger-bg text-danger"
            aria-hidden="true"
          >
            <Siren size={18} strokeWidth={1.9} />
          </span>
          <div className="min-w-0">
            <div className="mb-1 flex flex-wrap items-center gap-2">
              <span className="font-display text-[10px] font-bold uppercase tracking-[0.16em] text-danger">
                CRITICAL · MONEY
              </span>
              {extraCount > 0 ? (
                <span className="font-display text-[10px] text-bg-8">+{extraCount} сигнал</span>
              ) : null}
            </div>
            <div className="text-[15px] font-semibold leading-tight text-bg-11">{alert.title}</div>
            <div className="mt-1 text-[12px] leading-[1.5] text-bg-10">{alert.message}</div>
          </div>
        </div>

        {url ? (
          <a
            href={url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex shrink-0 items-center justify-center gap-2 rounded-[var(--radius-2)] border border-[color-mix(in_srgb,var(--danger)_48%,transparent)] bg-danger px-4 py-2.5 font-display text-[12px] font-bold text-white transition-[filter,transform] hover:brightness-110 active:translate-y-px focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-danger"
          >
            Открыть Ads Manager
            <ArrowUpRight size={14} aria-hidden="true" />
          </a>
        ) : null}
      </div>
    </div>
  );
}
