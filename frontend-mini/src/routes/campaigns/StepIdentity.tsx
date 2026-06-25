/**
 * StepIdentity — шаг 2 визарда: идентичность (act_id, page_id, pixel_id) + оффер.
 *
 * Таймзона кабинета фиксируется при создании и неизменна. На blur поля
 * «ID рекламного кабинета» дёргаем GET /campaigns/ad-account-timezone и
 * записываем tz_offset (ЧИСЛО часов, м.б. отрицательным) + timezone_name.
 * Поле Timezone — read-only показ «UTC±HH:00 · {name}» + спиннер; при ошибке —
 * ручной фолбэк с полным диапазоном UTC (−12..+14).
 *
 * Данные могут быть предзаполнены из пресета — редактируемы.
 */
import { useEffect, useState } from "react";
import { Input, Button, Select } from "@/components/ui";
import { Eyebrow } from "@/components/data";
import { haptic } from "@/lib/tg";
import { useAdAccountTimezone, useOffers } from "@/lib/api";
import { useWizardStore } from "./-wizardStore";

/** Число часов сдвига → строка вида «±HH:00» (зеркало _tz_offset_to_str бэка). */
function tzOffsetToStr(hours: number): string {
  const sign = hours < 0 ? "-" : "+";
  const abs = Math.abs(Math.trunc(hours));
  return `${sign}${String(abs).padStart(2, "0")}:00`;
}

/** Полный диапазон UTC для ручного фолбэка: от −12 до +14. */
const TZ_FALLBACK_OPTIONS = Array.from({ length: 27 }, (_, i) => {
  const hours = i - 12;
  return { value: String(hours), label: `UTC${tzOffsetToStr(hours)}` };
});

export function StepIdentity() {
  const { config, updateConfig, nextStep, prevStep } = useWizardStore();

  const [actId, setActId] = useState(config.act_id ?? "");
  const [pageId, setPageId] = useState(config.page_id ?? "");
  const [pixelId, setPixelId] = useState(config.pixel_id ?? "");
  const [offerCode, setOfferCode] = useState(config.offer_code ?? "");
  const [byerTag, setByerTag] = useState(config.byer_tag ?? "");
  const [error, setError] = useState<string | null>(null);

  // Таймзона: фетч на blur. fetchAct непустой → запрос включён.
  const [fetchAct, setFetchAct] = useState("");
  const [tzManual, setTzManual] = useState(false);
  const tzQuery = useAdAccountTimezone(fetchAct, fetchAct.length > 0);

  // Источник офферов для комбобокса (свободный ввод + подсказки).
  const offersQuery = useOffers();
  const offerCodes = (offersQuery.data ?? [])
    .map((o) => o.code)
    .filter(Boolean);

  // Успешный фетч → записать число часов + имя TZ в стор (деньги: число).
  useEffect(() => {
    if (tzQuery.data) {
      updateConfig({
        tz_offset: tzQuery.data.tz_offset_hours,
        timezone_name: tzQuery.data.timezone_name,
      });
      setTzManual(false);
    }
  }, [tzQuery.data, updateConfig]);

  // Ошибка фетча → даём ручной фолбэк (полный диапазон UTC).
  useEffect(() => {
    if (tzQuery.isError) setTzManual(true);
  }, [tzQuery.isError]);

  function handleActBlur() {
    const trimmed = actId.trim();
    if (trimmed) setFetchAct(trimmed);
  }

  function handleNext() {
    setError(null);
    if (!actId.trim()) {
      setError("Укажите ID рекламного кабинета");
      return;
    }
    if (!pageId.trim()) {
      setError("Укажите ID страницы");
      return;
    }
    if (!pixelId.trim()) {
      setError("Укажите ID пикселя");
      return;
    }
    if (!offerCode.trim()) {
      setError("Укажите код оффера");
      return;
    }
    // Деньги: не пускаем без подтверждённой TZ кабинета (авто/ручная/пресет),
    // иначе бэк подставит дефолт → старт кампании уедет.
    if (!config.timezone_name) {
      setError("Дождитесь подтягивания таймзоны кабинета или укажите вручную");
      return;
    }
    haptic.impact("light");
    updateConfig({
      act_id: actId.trim(),
      page_id: pageId.trim(),
      pixel_id: pixelId.trim(),
      offer_code: offerCode.trim().toUpperCase(),
      byer_tag: byerTag.trim() || null,
    });
    nextStep();
  }

  // Что показываем в read-only поле Timezone.
  const tzOffset = config.tz_offset;
  const tzName = config.timezone_name ?? null;
  const tzDisplay =
    typeof tzOffset === "number"
      ? `UTC${tzOffsetToStr(tzOffset)}${tzName ? ` · ${tzName}` : ""}`
      : "—";

  return (
    <div className="flex flex-col gap-5 p-4 pb-8">
      <Eyebrow num="02">ИДЕНТИЧНОСТЬ + ОФФЕР</Eyebrow>

      <div className="flex flex-col gap-4">
        <Input
          label="ID рекламного кабинета"
          placeholder="act_1234567890"
          value={actId}
          onChange={(e) => setActId(e.target.value)}
          onBlur={handleActBlur}
          autoCapitalize="none"
          autoCorrect="off"
        />

        {/* Timezone: read-only показ + спиннер; на ошибке — ручной Select */}
        {tzManual ? (
          <Select
            label="Таймзона кабинета (ручной выбор)"
            value={String(typeof tzOffset === "number" ? tzOffset : 0)}
            options={TZ_FALLBACK_OPTIONS}
            onChange={(e) =>
              updateConfig({ tz_offset: Number(e.target.value), timezone_name: "(вручную)" })
            }
          />
        ) : (
          <div className="flex flex-col gap-1">
            <label
              className="text-[11px] uppercase tracking-[0.07em] text-[var(--color-bg-9)] font-mono"
            >
              Таймзона кабинета
            </label>
            <div
              className="min-h-[44px] px-3 flex items-center gap-2 rounded-[var(--radius-2)] bg-[var(--color-bg-1)] border border-[var(--hairline)] text-[14px] text-[var(--color-bg-11)]"
              aria-label="Таймзона кабинета"
            >
              {tzQuery.isFetching ? (
                <>
                  <span
                    className="inline-block h-3.5 w-3.5 rounded-full border-2 border-[var(--color-bg-7)] border-t-transparent animate-spin"
                    role="status"
                    aria-label="Загрузка таймзоны"
                  />
                  <span className="text-bg-8">Определяем…</span>
                </>
              ) : (
                <span className={typeof tzOffset === "number" ? "" : "text-bg-7"}>
                  {tzDisplay}
                </span>
              )}
            </div>
            <p className="text-[11px] text-bg-7">
              Подтягивается из кабинета по ID. Зафиксирована при создании.
            </p>
          </div>
        )}

        <Input
          label="ID страницы Facebook"
          placeholder="123456789"
          value={pageId}
          onChange={(e) => setPageId(e.target.value)}
          inputMode="numeric"
        />
        <Input
          label="ID пикселя"
          placeholder="987654321"
          value={pixelId}
          onChange={(e) => setPixelId(e.target.value)}
          inputMode="numeric"
        />

        {/* Оффер: свободный ввод + подсказки из активных офферов (datalist) */}
        <div className="flex flex-col gap-1">
          <label
            htmlFor="offer-code-input"
            className="text-[11px] uppercase tracking-[0.07em] text-[var(--color-bg-9)] font-mono"
          >
            Код оффера
          </label>
          <input
            id="offer-code-input"
            list="offer-code-list"
            placeholder="GH_AVI"
            value={offerCode}
            onChange={(e) => setOfferCode(e.target.value.toUpperCase())}
            autoCapitalize="characters"
            autoCorrect="off"
            className="min-h-[44px] px-3 w-full rounded-[var(--radius-2)] bg-[var(--color-bg-2)] border border-[var(--hairline)] text-[14px] text-[var(--color-bg-11)] font-body placeholder:text-[var(--color-bg-7)] focus:outline-none focus:border-[var(--color-accent)] transition-colors duration-[var(--dur-base)]"
          />
          <datalist id="offer-code-list">
            {offerCodes.map((code) => (
              <option key={code} value={code} />
            ))}
          </datalist>
        </div>

        <Input
          label="Тег байера (опционально)"
          placeholder="MV"
          value={byerTag}
          onChange={(e) => setByerTag(e.target.value)}
          autoCapitalize="characters"
        />
      </div>

      {error !== null && (
        <p className="text-[12px] text-[var(--color-danger)]">{error}</p>
      )}

      <div className="flex flex-col gap-3 mt-2">
        <Button fullWidth onClick={handleNext}>
          Далее
        </Button>
        <Button variant="ghost" fullWidth onClick={() => { haptic.selection(); prevStep(); }}>
          Назад
        </Button>
      </div>
    </div>
  );
}
