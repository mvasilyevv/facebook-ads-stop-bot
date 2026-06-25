/**
 * Шаг 2 — Идентичность + Оффер.
 *
 * Поля: act_id, page_id, pixel_id, tz_offset, offer_code, byer_tag.
 * Если шаг 1 был "preset" — поля предзаполнены из пресета.
 *
 * Таймзона кабинета подтягивается автоматически по act_id (blur): TZ зафиксирована
 * при создании кабинета, её нельзя выбрать руками без ошибки. На ошибке авто-подхвата —
 * фолбэк на ручной ввод с ПОЛНЫМ диапазоном UTC (−12..+14, включая отрицательные).
 *
 * Тем же blur'ом (общий дедуп) тянем список FB-страниц кабинета: если подтянулись —
 * page_id выбирается дропдауном, иначе остаётся ручной ввод ID.
 */

import { useRef, useState, type FC } from "react";
import { Loader2 } from "lucide-react";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { useAdAccountPages, useAdAccountTimezone } from "@/lib/api/campaigns";
import { useOffers } from "@/lib/api/offers";
import type { WizardIdentity } from "@/stores/campaignWizard";

interface WizardStep2IdentityProps {
  values: WizardIdentity;
  onChange: (v: Partial<WizardIdentity>) => void;
  /** Ошибки валидации по именам полей. */
  errors?: Partial<Record<keyof WizardIdentity, string>>;
}

/** Полный диапазон смещений UTC для ручного фолбэка: от −12 до +14 (включая отрицательные). */
const TZ_FALLBACK_OPTIONS = Array.from({ length: 14 - -12 + 1 }, (_, i) => {
  const h = i - 12;
  const sign = h < 0 ? "-" : "+";
  return { value: String(h), label: `UTC${sign}${String(Math.abs(h)).padStart(2, "0")}:00` };
});

/** Форматирует смещение часов в "UTC±HH:00" (зеркало backend _tz_offset_to_str). */
function formatTzOffset(hours: number): string {
  const sign = hours < 0 ? "-" : "+";
  return `UTC${sign}${String(Math.abs(hours)).padStart(2, "0")}:00`;
}

export const WizardStep2Identity: FC<WizardStep2IdentityProps> = ({
  values,
  onChange,
  errors = {},
}) => {
  const tzMutation = useAdAccountTimezone();
  const pagesMutation = useAdAccountPages();
  const offersQuery = useOffers();
  // Авто-подхват TZ упал → показываем ручной фолбэк-контрол.
  const [tzFallback, setTzFallback] = useState(false);
  // Подтянутые страницы кабинета → дропдаун выбора page_id. Пусто/ошибка → ручной ввод.
  const [pages, setPages] = useState<{ id: string; name: string }[]>([]);
  // Дедуп: не фетчить повторно тот же act_id на каждом blur (бьёт по живой
  // Vision-сессии + строка в meta_api_audit_log на каждый клик).
  const lastFetchedAct = useRef<string | null>(null);

  // Подтягиваем TZ кабинета И список страниц по act_id при потере фокуса
  // (если поле непустое). Один blur → один фетч на act_id (общий дедуп).
  const fetchAccountMeta = () => {
    const actId = values.act_id.trim();
    if (!actId || actId === lastFetchedAct.current) return;
    lastFetchedAct.current = actId;
    tzMutation.mutate(actId, {
      onSuccess: (data) => {
        setTzFallback(false);
        onChange({ tz_offset: data.tz_offset_hours, timezone_name: data.timezone_name });
      },
      onError: () => {
        // Авто-подхват не удался — даём ручной ввод с полным диапазоном + разрешаем
        // повторить фетч тем же act_id (сбрасываем дедуп).
        lastFetchedAct.current = null;
        setTzFallback(true);
      },
    });
    pagesMutation.mutate(actId, {
      onSuccess: (data) => {
        // Непустой массив → дропдаун; пустой → остаётся ручной ввод page_id.
        setPages(data.pages);
      },
      onError: () => {
        // Не удалось подтянуть страницы — фолбэк на ручной ввод page_id.
        setPages([]);
      },
    });
  };

  return (
    <div className="space-y-6">
      {/* Заголовок */}
      <div>
        <div className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-7 mb-1">
          ШАГ 2 · ИДЕНТИЧНОСТЬ
        </div>
        <h2 className="font-display text-[20px] font-medium text-bg-11 leading-tight m-0">
          Кабинет и оффер
        </h2>
        <p className="text-[13px] text-bg-9 mt-1">
          Укажите ID рекламного кабинета, страницы, пикселя и код оффера.
        </p>
      </div>

      {/* Кабинет */}
      <div>
        <div className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-7 mb-3">
          РЕКЛАМНЫЙ КАБИНЕТ
        </div>
        <div className="grid grid-cols-2 gap-4">
          <Input
            label="Ad Account ID"
            placeholder="act_123456789"
            value={values.act_id}
            onChange={(e) => onChange({ act_id: e.target.value })}
            onBlur={fetchAccountMeta}
            errorMessage={errors.act_id}
            helpText="Числовой ID с префиксом act_ или без"
          />
          {tzFallback ? (
            <div className="flex flex-col gap-1.5">
              <Select
                label="Timezone"
                options={TZ_FALLBACK_OPTIONS}
                value={String(values.tz_offset)}
                onChange={(e) =>
                  onChange({ tz_offset: Number(e.target.value), timezone_name: "(вручную)" })
                }
              />
              <p className="text-[11px] text-bg-8">
                Авто-подхват не удался — укажите таймзону кабинета вручную.
              </p>
            </div>
          ) : (
            <div className="flex flex-col gap-1.5">
              <label className="text-[11px] font-display tracking-wider uppercase text-bg-9">
                Timezone
              </label>
              <div className="flex h-8 items-center gap-2 rounded-[var(--radius-2)] border border-[var(--hairline-strong)] bg-bg-2 px-3 text-[13.5px] text-bg-11">
                {tzMutation.isPending ? (
                  <>
                    <Loader2 aria-hidden="true" size={14} className="animate-spin text-bg-9" />
                    <span className="text-bg-9">Подтягиваю таймзону кабинета…</span>
                  </>
                ) : values.timezone_name ? (
                  <span>
                    {formatTzOffset(values.tz_offset)} · {values.timezone_name}
                  </span>
                ) : (
                  <span className="text-bg-9">
                    Заполните Ad Account ID — таймзона подтянется автоматически
                  </span>
                )}
              </div>
              {errors.tz_offset && (
                <span className="text-[11px] text-danger font-display">{errors.tz_offset}</span>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Страница и пиксель */}
      <div>
        <div className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-7 mb-3">
          СТРАНИЦА И ПИКСЕЛЬ
        </div>
        <div className="grid grid-cols-2 gap-4">
          {pagesMutation.isPending ? (
            // Спиннер во время фетча страниц — финальный контрол ещё неизвестен.
            <div className="flex flex-col gap-1.5">
              <label className="text-[11px] font-display tracking-wider uppercase text-bg-9">
                Facebook Page ID
              </label>
              <div className="flex h-8 items-center gap-2 rounded-[var(--radius-2)] border border-[var(--hairline-strong)] bg-bg-2 px-3 text-[13.5px] text-bg-9">
                <Loader2 aria-hidden="true" size={14} className="animate-spin text-bg-9" />
                <span>Подтягиваю страницы кабинета…</span>
              </div>
              {errors.page_id && (
                <span className="text-[11px] text-danger font-display">{errors.page_id}</span>
              )}
            </div>
          ) : pages.length > 0 ? (
            // Страницы подтянулись — выбор из дропдауна, value=id.
            <Select
              label="Facebook Page"
              placeholder="Выберите страницу"
              options={pages.map((p) => ({ value: p.id, label: `${p.name} — ${p.id}` }))}
              value={values.page_id}
              onChange={(e) => onChange({ page_id: e.target.value })}
              errorMessage={errors.page_id}
            />
          ) : (
            // Фетч упал / страниц нет — ручной ввод ID с подсказкой.
            <Input
              label="Facebook Page ID"
              placeholder="123456789"
              value={values.page_id}
              onChange={(e) => onChange({ page_id: e.target.value })}
              errorMessage={errors.page_id}
              helpText="Не удалось подтянуть — введите ID вручную"
            />
          )}
          <Input
            label="FB Pixel ID"
            placeholder="123456789"
            value={values.pixel_id}
            onChange={(e) => onChange({ pixel_id: e.target.value })}
            errorMessage={errors.pixel_id}
          />
        </div>
      </div>

      {/* Оффер */}
      <div>
        <div className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-7 mb-3">
          ОФФЕР И БАЙЕР
        </div>
        {/* Комбобокс-подсказки из активных офферов (вне grid — datalist не занимает место). */}
        <datalist id="offers-dl">
          {(offersQuery.data ?? []).map((o) => (
            <option key={o.id} value={o.code}>
              {o.name}
            </option>
          ))}
        </datalist>
        <div className="grid grid-cols-2 gap-4">
          {/* Свободный ввод разрешён, .toUpperCase() сохраняется. */}
          <Input
            label="Код оффера"
            placeholder="GH_CR2"
            value={values.offer_code}
            onChange={(e) => onChange({ offer_code: e.target.value.toUpperCase() })}
            errorMessage={errors.offer_code}
            helpText="Войдёт в название кампании"
            list="offers-dl"
          />
          <Input
            label="Тег байера"
            placeholder="MV"
            value={values.byer_tag}
            onChange={(e) => onChange({ byer_tag: e.target.value.toUpperCase() })}
            errorMessage={errors.byer_tag}
            helpText="Опционально — для фильтра owner_campaign_tag"
          />
        </div>
      </div>
    </div>
  );
};

// ─── Валидация ────────────────────────────────────────────────────────────────

export function validateIdentity(
  values: WizardIdentity,
): Partial<Record<keyof WizardIdentity, string>> {
  const errors: Partial<Record<keyof WizardIdentity, string>> = {};

  if (!values.act_id.trim()) errors.act_id = "Обязательное поле";
  if (!values.page_id.trim()) errors.page_id = "Обязательное поле";
  if (!values.pixel_id.trim()) errors.pixel_id = "Обязательное поле";
  if (!values.offer_code.trim()) errors.offer_code = "Обязательное поле";
  // Деньги: TZ кабинета должна быть подтверждена (авто-подхват / ручной выбор /
  // пресет) — иначе бэк тихо подставит дефолт и старт кампании уедет на часы.
  if (!values.timezone_name.trim())
    errors.tz_offset = "Подтвердите таймзону: введите Ad Account ID";

  return errors;
}
