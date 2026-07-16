/**
 * Форма быстрого дублирования структуры из AdDrawer.
 * Preview — read-only; запуск требует отдельного явного клика в web-preview.
 */

import type { AdSnapshot } from "@fb/shared";
import {
  ArrowLeft,
  ArrowRight,
  CalendarClock,
  Check,
  CheckCircle2,
  CopyPlus,
  Layers3,
  Send,
  TriangleAlert,
} from "lucide-react";
import { useEffect, useId, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { Input } from "@/components/ui/Input";
import { Modal, ModalFooter } from "@/components/ui/Modal";
import {
  TERMINAL_ADSET_DUPLICATE_STATUSES,
  type AdsetDuplicatePreviewIn,
  type AdsetDuplicatePreviewOut,
  type DuplicateBudgetLevel,
  type DuplicateSourceAd,
  useAdsetDuplicateStatus,
  useStartAdsetDuplicate,
  usePreviewAdsetDuplicate,
} from "@/lib/api/adsetDuplicates";
import { cn } from "@/lib/utils/cn";

type FlowStep = "setup" | "preview" | "status";

interface DuplicateFormState {
  campaignCount: number;
  adsetsPerCampaign: number;
  budgetLevel: DuplicateBudgetLevel;
  dailyBudgetCents: number;
  campaignNameBase: string;
  adsetNameBase: string;
  startDate: string;
}

interface AdsetDuplicateActionProps {
  ad: AdSnapshot;
}

type AdSnapshotWithBudget = AdSnapshot & { adset_daily_budget?: string | null };

const MAX_DAILY_BUDGET_CENTS = 10_000_000;
const MAX_TIMEOUT_MS = 2_147_483_647;
const QUICK_BUDGET_AMOUNTS = [50, 100, 200, 500] as const;

export function AdsetDuplicateAction({ ad }: AdsetDuplicateActionProps) {
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState<FlowStep>("setup");
  const [form, setForm] = useState<DuplicateFormState>(() => initialForm(ad));
  const [selectedAdIds, setSelectedAdIds] = useState<string[]>([ad.fb_ad_id]);
  const [preview, setPreview] = useState<AdsetDuplicatePreviewOut | null>(null);
  const [taskId, setTaskId] = useState<number | null>(null);
  const [idempotencyToken, setIdempotencyToken] = useState(() => newIdempotencyToken());
  const [nowMs, setNowMs] = useState(() => Date.now());

  const previewMutation = usePreviewAdsetDuplicate();
  const launchMutation = useStartAdsetDuplicate();
  const statusQuery = useAdsetDuplicateStatus(open ? taskId : null);

  const sourceAds = preview?.source.ads ?? [];
  const selectedCount = selectedAdIds.length;
  const localFormat = `${form.campaignCount}-${form.adsetsPerCampaign}-${selectedCount}`;
  const validationError = validateForm(form, selectedCount);
  const status = statusQuery.data?.status ?? launchMutation.data?.status ?? "pending_confirmation";
  const isTerminal = TERMINAL_ADSET_DUPLICATE_STATUSES.has(status);
  const requiresFreshDraft = status === "failed" || status === "cancelled" || status === "expired";
  const previewExpiresAt = preview ? Date.parse(preview.expires_at) : Number.NaN;
  const previewExpired =
    preview !== null && (!Number.isFinite(previewExpiresAt) || previewExpiresAt <= nowMs);

  useEffect(() => {
    if (!preview || previewExpired || !Number.isFinite(previewExpiresAt)) return;
    const delay = Math.min(Math.max(0, previewExpiresAt - Date.now()) + 25, MAX_TIMEOUT_MS);
    const timeoutId = window.setTimeout(() => setNowMs(Date.now()), delay);
    return () => window.clearTimeout(timeoutId);
  }, [nowMs, preview, previewExpired, previewExpiresAt]);

  const selectedNames = sourceAds
    .filter((sourceAd) => selectedAdIds.includes(sourceAdId(sourceAd)))
    .map((sourceAd) => sourceAd.name);

  async function handleOpen() {
    const nextForm = initialForm(ad);
    const nextToken = newIdempotencyToken();
    setForm(nextForm);
    setSelectedAdIds([ad.fb_ad_id]);
    setPreview(null);
    setTaskId(null);
    setStep("setup");
    setIdempotencyToken(nextToken);
    setNowMs(Date.now());
    previewMutation.reset();
    launchMutation.reset();
    setOpen(true);

    try {
      // Первый preview намеренно не отправляет browser-local дату: сервер сам
      // выбирает «завтра» в timezone рекламного кабинета. Иначе около полуночи
      // browser TZ мог дать уже прошедшую для кабинета дату и не позволить форме
      // даже загрузить источник.
      const firstRequest = buildPreviewRequest(ad.fb_ad_id, nextForm, [ad.fb_ad_id], nextToken);
      const first = await previewMutation.mutateAsync({
        ...firstRequest,
        start_date: null,
      });
      setForm({
        ...nextForm,
        startDate: first.schedule.start_time_local.slice(0, 10),
      });
      setPreview(first);
    } catch {
      // Ошибка доступна из previewMutation.error и показывается внутри modal.
    }
  }

  function requestPreview(nextForm: DuplicateFormState, nextSelected: string[], token: string) {
    return previewMutation.mutateAsync(
      buildPreviewRequest(ad.fb_ad_id, nextForm, nextSelected, token),
    );
  }

  async function handleCalculate() {
    if (validationError) return;
    try {
      const calculated = await requestPreview(form, selectedAdIds, idempotencyToken);
      setPreview(calculated);
      setNowMs(Date.now());
      setStep("preview");
    } catch {
      // Ошибка мутации отрисуется в форме.
    }
  }

  async function handleLaunch() {
    if (!preview || previewExpired) return;
    try {
      const task = await launchMutation.mutateAsync({ preview_token: preview.preview_token });
      setTaskId(task.task_id);
      setStep("status");
    } catch {
      // Ошибка мутации отрисуется в preview.
    }
  }

  function toggleAd(adId: string, checked: boolean) {
    setSelectedAdIds((current) =>
      checked ? [...new Set([...current, adId])] : current.filter((id) => id !== adId),
    );
  }

  function apply321Preset() {
    setForm((current) => ({ ...current, campaignCount: 3, adsetsPerCampaign: 2 }));
    setSelectedAdIds([ad.fb_ad_id]);
  }

  return (
    <>
      <Button
        variant="secondary"
        className="flex-1"
        leftIcon={<CopyPlus size={15} aria-hidden="true" />}
        onClick={handleOpen}
        aria-label="Дублировать структуру объявления"
      >
        Дублировать структуру
      </Button>

      <Modal
        open={open}
        onOpenChange={setOpen}
        size="lg"
        contentClassName="p-0"
        title={null}
        ariaTitle="Дублировать структуру"
        ariaDescription="Настройка и безопасный запуск дублирования адсета"
      >
        <div className="px-6 pt-6 pb-4 border-b border-[var(--hairline)] bg-bg-1">
          <div className="flex items-start justify-between gap-5 pr-8">
            <div>
              <div className="font-display text-[10px] tracking-[0.16em] uppercase text-accent-muted">
                Structure duplicator / guarded launch
              </div>
              <h2 className="mt-1 font-display text-[21px] leading-tight text-bg-11">
                Дублировать структуру
              </h2>
              <p className="mt-1.5 text-[12.5px] text-bg-9 max-w-[540px]">
                Выберите нужные объявления и проверьте итог. Создание начнётся только после
                отдельной кнопки запуска на следующем шаге.
              </p>
            </div>
            <div className="shrink-0 rounded-[var(--radius-2)] border border-[var(--hairline)] bg-bg-2 px-3 py-2 text-right">
              <div className="font-display text-[9px] uppercase tracking-[0.12em] text-bg-8">
                Формат
              </div>
              <div className="font-mono text-[18px] leading-none text-bg-11 mt-1">
                {step === "preview" && preview ? preview.format_code : localFormat}
              </div>
            </div>
          </div>
          <StepRail step={step} />
        </div>

        <div className="px-6 py-5 min-h-[390px]">
          {step === "setup" ? (
            <SetupStep
              ad={ad}
              form={form}
              setForm={setForm}
              preview={preview}
              loading={previewMutation.isPending && !preview}
              error={previewMutation.error}
              selectedAdIds={selectedAdIds}
              selectedCount={selectedCount}
              onToggleAd={toggleAd}
              onApply321={apply321Preset}
            />
          ) : null}

          {step === "preview" && preview ? (
            <PreviewStep
              preview={preview}
              selectedNames={selectedNames}
              error={launchMutation.error}
            />
          ) : null}

          {step === "status" ? (
            <StatusStep
              status={status}
              taskId={taskId}
              preview={preview}
              statusData={statusQuery.data}
              loading={statusQuery.isFetching && !statusQuery.data}
              error={statusQuery.error}
            />
          ) : null}
        </div>

        <ModalFooter className="mt-0 px-6 py-4 bg-bg-1">
          {step === "setup" ? (
            <>
              <Button variant="ghost" onClick={() => setOpen(false)}>
                Отмена
              </Button>
              <Button
                variant="primary"
                onClick={handleCalculate}
                loading={previewMutation.isPending}
                disabled={Boolean(validationError) || !preview}
                rightIcon={<ArrowRight size={14} aria-hidden="true" />}
              >
                Рассчитать дубль
              </Button>
            </>
          ) : null}

          {step === "preview" ? (
            <>
              <Button
                variant="ghost"
                onClick={() => setStep("setup")}
                leftIcon={<ArrowLeft size={14} aria-hidden="true" />}
              >
                Изменить
              </Button>
              {previewExpired ? (
                <Button
                  variant="primary"
                  onClick={handleCalculate}
                  loading={previewMutation.isPending}
                >
                  Обновить preview
                </Button>
              ) : (
                <Button
                  variant="primary"
                  onClick={handleLaunch}
                  loading={launchMutation.isPending}
                  leftIcon={<Send size={14} aria-hidden="true" />}
                >
                  Запустить дублирование
                </Button>
              )}
            </>
          ) : null}

          {step === "status" ? (
            <Button variant={isTerminal ? "primary" : "secondary"} onClick={() => setOpen(false)}>
              {requiresFreshDraft
                ? "Закрыть окно"
                : isTerminal
                  ? "Готово"
                  : "Закрыть — задача продолжится"}
            </Button>
          ) : null}
        </ModalFooter>
      </Modal>
    </>
  );
}

function StepRail({ step }: { step: FlowStep }) {
  const current = step === "setup" ? 0 : step === "preview" ? 1 : 2;
  return (
    <ol className="mt-5 grid grid-cols-3 gap-2" aria-label="Этапы дублирования">
      {["Источник и формат", "Проверка", "Запуск и статус"].map((label, index) => (
        <li key={label} className="min-w-0">
          <div
            className={cn(
              "h-px mb-2 transition-colors",
              index <= current ? "bg-accent" : "bg-bg-5",
            )}
          />
          <div className="flex items-center gap-2">
            <span
              className={cn(
                "font-mono text-[10px]",
                index <= current ? "text-accent-muted" : "text-bg-8",
              )}
            >
              0{index + 1}
            </span>
            <span
              className={cn(
                "font-display text-[10px] uppercase tracking-[0.08em] truncate",
                index === current ? "text-bg-11" : "text-bg-8",
              )}
            >
              {label}
            </span>
          </div>
        </li>
      ))}
    </ol>
  );
}

interface SetupStepProps {
  ad: AdSnapshot;
  form: DuplicateFormState;
  setForm: React.Dispatch<React.SetStateAction<DuplicateFormState>>;
  preview: AdsetDuplicatePreviewOut | null;
  loading: boolean;
  error: Error | null;
  selectedAdIds: string[];
  selectedCount: number;
  onToggleAd: (adId: string, checked: boolean) => void;
  onApply321: () => void;
}

function SetupStep({
  ad,
  form,
  setForm,
  preview,
  loading,
  error,
  selectedAdIds,
  selectedCount,
  onToggleAd,
  onApply321,
}: SetupStepProps) {
  const errorMessage = validateForm(form, selectedCount);
  return (
    <div className="grid grid-cols-1 lg:grid-cols-[1.08fr_0.92fr] gap-5">
      <section className="min-w-0">
        <SectionLabel index="A" label="Источник" />
        {loading ? (
          <div className="h-[214px] rounded-[var(--radius-2)] border border-[var(--hairline)] bg-bg-2 animate-pulse" />
        ) : preview ? (
          <div className="rounded-[var(--radius-2)] border border-[var(--hairline)] overflow-hidden">
            <SourceRow label="Кампания" value={preview.source.campaign.name} />
            <SourceRow label="Адсет" value={preview.source.adset.name} border />
            <SourceRow
              label="Кабинет"
              value={formatAccountId(preview.source.account.id)}
              detail={preview.schedule.timezone_name}
              border
            />
            <div className="border-t border-[var(--hairline)] bg-bg-2 px-3 py-2.5">
              <div className="flex items-center justify-between gap-3 mb-2">
                <span className="font-display text-[10px] uppercase tracking-[0.1em] text-bg-9">
                  Объявления этого адсета
                </span>
                <span className="font-mono text-[10px] text-accent-muted">
                  {selectedCount}/{preview.source.ads.length}
                </span>
              </div>
              <div
                className="max-h-[176px] overflow-y-auto flex flex-col gap-1"
                role="group"
                aria-label="Объявления для дублирования"
              >
                {preview.source.ads.map((sourceAd) => {
                  const adId = sourceAdId(sourceAd);
                  const current = adId === ad.fb_ad_id;
                  return (
                    <div
                      key={adId}
                      className="flex items-center gap-2.5 rounded-[var(--radius-1)] px-2 py-2 hover:bg-bg-3"
                    >
                      <Checkbox
                        checked={selectedAdIds.includes(adId)}
                        onChange={(checked) => onToggleAd(adId, checked)}
                        aria-label={`Выбрать ${sourceAd.name}`}
                        disabled={selectedAdIds.length >= 10 && !selectedAdIds.includes(adId)}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[12.5px] text-bg-11">
                          {sourceAd.name}
                        </span>
                        <span className="block truncate font-mono text-[9.5px] text-bg-8">
                          {adId}
                        </span>
                      </span>
                      {current ? (
                        <Badge size="sm" variant="neutral">
                          текущая
                        </Badge>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        ) : (
          <InlineError error={error ?? new Error("Источник не загружен")} />
        )}
      </section>

      <section className="min-w-0">
        <div className="flex items-center justify-between gap-3">
          <SectionLabel index="B" label="Формат дубля" />
          <button
            type="button"
            onClick={onApply321}
            className="mb-3 rounded-[var(--radius-1)] border border-accent/40 bg-bg-2 px-2 py-1 font-mono text-[10px] text-accent-muted hover:bg-bg-3 focus-visible:outline-2 focus-visible:outline-accent"
          >
            preset 3-2-1
          </button>
        </div>

        <div className="rounded-[var(--radius-2)] border border-[var(--hairline)] bg-bg-1 p-3.5 space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <Input
              label="Кампаний"
              type="number"
              min={1}
              max={5}
              value={form.campaignCount}
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  campaignCount: readPositiveInt(event.target.value),
                }))
              }
            />
            <Input
              label="Адсетов / кампания"
              type="number"
              min={1}
              max={10}
              value={form.adsetsPerCampaign}
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  adsetsPerCampaign: readPositiveInt(event.target.value),
                }))
              }
            />
          </div>

          <div>
            <div className="mb-1.5 font-display text-[11px] uppercase tracking-wider text-bg-9">
              Бюджетный уровень
            </div>
            <div className="grid grid-cols-2 gap-2">
              {(["ABO", "CBO"] as const).map((level) => (
                <button
                  key={level}
                  type="button"
                  onClick={() => setForm((current) => ({ ...current, budgetLevel: level }))}
                  aria-pressed={form.budgetLevel === level}
                  className={cn(
                    "rounded-[var(--radius-2)] border px-3 py-2.5 text-left transition-colors focus-visible:outline-2 focus-visible:outline-accent",
                    form.budgetLevel === level
                      ? "border-accent bg-bg-3"
                      : "border-[var(--hairline)] bg-bg-2 hover:border-bg-7",
                  )}
                >
                  <span className="flex items-center justify-between font-mono text-[12px] text-bg-11">
                    {level}
                    {form.budgetLevel === level ? <Check size={13} aria-hidden="true" /> : null}
                  </span>
                  <span className="mt-1 block text-[10.5px] leading-snug text-bg-8">
                    {level === "ABO" ? "бюджет на каждый адсет" : "бюджет на кампанию"}
                  </span>
                </button>
              ))}
            </div>
          </div>

          <BudgetAmountField
            cents={form.dailyBudgetCents}
            currency={preview?.budget.currency || "USD"}
            budgetLevel={form.budgetLevel}
            unitCount={
              form.budgetLevel === "ABO"
                ? form.campaignCount * form.adsetsPerCampaign
                : form.campaignCount
            }
            onCents={(dailyBudgetCents) => setForm((current) => ({ ...current, dailyBudgetCents }))}
          />

          <Input
            label="Дата старта · 00:00 кабинета"
            type="date"
            min={tomorrowDateInTimeZone(preview?.schedule.timezone_name, preview?.schedule.offset)}
            value={form.startDate}
            onChange={(event) =>
              setForm((current) => ({ ...current, startDate: event.target.value }))
            }
            helpText={
              preview
                ? `${preview.schedule.timezone_name} · точный UTC будет в preview`
                : "По умолчанию — завтра в таймзоне рекламного кабинета"
            }
          />

          <div className="grid grid-cols-2 gap-3">
            <Input
              label="Основа кампании"
              maxLength={300}
              value={form.campaignNameBase}
              onChange={(event) =>
                setForm((current) => ({ ...current, campaignNameBase: event.target.value }))
              }
            />
            <Input
              label="Основа адсета"
              maxLength={300}
              value={form.adsetNameBase}
              onChange={(event) =>
                setForm((current) => ({ ...current, adsetNameBase: event.target.value }))
              }
            />
          </div>

          <div className="flex items-center gap-3 rounded-[var(--radius-2)] border border-[var(--hairline)] bg-bg-2 px-3 py-2.5">
            <CalendarClock size={15} className="text-accent-muted shrink-0" aria-hidden="true" />
            <div className="min-w-0 flex-1">
              <div className="text-[11px] text-bg-8">Старт · 00:00 кабинета по preview</div>
              <div className="truncate text-[12px] text-bg-11">
                {preview?.schedule.start_time_local ?? form.startDate}
              </div>
            </div>
          </div>
        </div>

        {errorMessage ? (
          <p role="alert" className="mt-2 text-[11px] text-danger">
            {errorMessage}
          </p>
        ) : null}
        {error ? <InlineError error={error} className="mt-2" /> : null}
      </section>
    </div>
  );
}

function PreviewStep({
  preview,
  selectedNames,
  error,
}: {
  preview: AdsetDuplicatePreviewOut;
  selectedNames: string[];
  error: Error | null;
}) {
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <PreviewMetric label="Кампании" value={preview.counts.campaigns} />
        <PreviewMetric label="Адсеты" value={preview.counts.adsets} />
        <PreviewMetric label="Объявления" value={preview.counts.ads} accent />
        <PreviewMetric label="Всего объектов" value={preview.counts.total_objects} />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <section className="rounded-[var(--radius-2)] border border-[var(--hairline)] overflow-hidden">
          <div className="px-3 py-2 bg-bg-2 border-b border-[var(--hairline)]">
            <SectionLabel index="A" label="Что будет скопировано" compact />
          </div>
          <SourceRow label="Кампания" value={preview.source.campaign.name} />
          <SourceRow label="Адсет" value={preview.source.adset.name} border />
          <div className="border-t border-[var(--hairline)] px-3 py-2.5">
            <div className="font-display text-[9px] uppercase tracking-[0.1em] text-bg-8 mb-1">
              Выбранные объявления
            </div>
            <div className="text-[12px] text-bg-11 leading-relaxed">
              {selectedNames.join(" · ") || "—"}
            </div>
          </div>
        </section>

        <section className="rounded-[var(--radius-2)] border border-[var(--hairline)] overflow-hidden">
          <div className="px-3 py-2 bg-bg-2 border-b border-[var(--hairline)]">
            <SectionLabel index="B" label="Бюджет и старт" compact />
          </div>
          <SourceRow
            label={`${preview.budget.level} · единица`}
            value={formatMoney(preview.budget.unit_daily_budget_cents, preview.budget.currency)}
          />
          <SourceRow
            label="Итого / день"
            value={formatMoney(preview.budget.total_daily_budget_cents, preview.budget.currency)}
            detail="после создания всей структуры"
            border
          />
          <SourceRow
            label="Старт"
            value={formatAccountLocal(preview.schedule.start_time_local)}
            detail={`${preview.schedule.timezone_name} · UTC${preview.schedule.offset}`}
            border
          />
        </section>
      </div>

      <section className="rounded-[var(--radius-2)] border border-[var(--hairline)] bg-bg-2 p-3.5">
        <div className="flex items-center gap-2 mb-3">
          <Layers3 size={14} className="text-accent-muted" aria-hidden="true" />
          <span className="font-display text-[10px] uppercase tracking-[0.1em] text-bg-9">
            Сгенерированные имена
          </span>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <NameList label="Кампании" values={preview.generated_names.campaigns} />
          <NameList label="Адсеты" values={preview.generated_names.adsets} />
        </div>
      </section>

      {preview.warnings.length ? (
        <div className="rounded-[var(--radius-2)] border border-[rgba(199,154,92,0.35)] bg-[rgba(199,154,92,0.08)] p-3">
          {preview.warnings.map((warning) => (
            <div key={warning} className="flex gap-2 text-[11.5px] text-bg-10">
              <TriangleAlert size={13} className="mt-0.5 shrink-0" aria-hidden="true" />
              <span>{warning}</span>
            </div>
          ))}
        </div>
      ) : null}

      <div className="flex items-center justify-between gap-4 text-[10.5px] text-bg-8">
        <span>Preview read-only · Meta не изменена</span>
        <span>Действителен до {formatSchedule(preview.expires_at)}</span>
      </div>
      {error ? <InlineError error={error} /> : null}
    </div>
  );
}

function StatusStep({
  status,
  taskId,
  preview,
  statusData,
  loading,
  error,
}: {
  status: string;
  taskId: number | null;
  preview: AdsetDuplicatePreviewOut | null;
  statusData: ReturnType<typeof useAdsetDuplicateStatus>["data"];
  loading: boolean;
  error: Error | null;
}) {
  const progress = statusData?.progress;
  const completed = Number(progress?.completed ?? 0);
  const total = Number(progress?.total ?? 0);
  const progressPercent = total > 0 ? Math.min(100, Math.round((completed / total) * 100)) : 0;
  const createdMetaIds = statusData?.created_meta_ids ?? {};
  const createdCount = countCreatedMetaIds(createdMetaIds);
  const createdIdSample = listCreatedMetaIds(createdMetaIds).slice(0, 6);
  const success = status === "succeeded";
  const failed = status === "failed" || status === "expired" || status === "cancelled";
  const progressMessage = progress?.message || progress?.phase || "Задача поставлена в очередь";
  const statusAnnouncement = success
    ? "Задача завершена: структура создана и активирована с будущим временем старта."
    : failed
      ? `Задача завершилась со статусом ${duplicateStatusLabel(status)}. ${statusData?.error || ""}`
      : `${duplicateStatusLabel(status)}. ${progressMessage}${
          total > 0 ? `. Выполнено ${completed} из ${total}.` : "."
        }`;

  return (
    <div className="mx-auto max-w-[620px] space-y-4">
      <div role="status" aria-live="polite" aria-atomic="true" className="sr-only">
        {statusAnnouncement}
      </div>
      <div className="rounded-[var(--radius-3)] border border-[var(--hairline)] overflow-hidden">
        <div className="relative bg-bg-2 px-5 py-5">
          <div className="absolute inset-y-0 left-0 w-1 bg-accent" />
          <div className="flex items-start gap-3">
            <div className="mt-0.5 flex size-8 items-center justify-center rounded-full border border-accent/40 bg-bg-3 text-accent-muted">
              <Send size={15} aria-hidden="true" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="font-display text-[13px] text-bg-11">Дублирование запущено</div>
              <p className="mt-1 text-[11.5px] leading-relaxed text-bg-9">
                Задача поставлена в безопасную очередь. Статус создания обновляется прямо здесь.
              </p>
            </div>
            <Badge variant={success ? "success" : failed ? "failed" : "neutral"} size="sm">
              {duplicateStatusLabel(status)}
            </Badge>
          </div>
        </div>

        <div className="px-5 py-4 space-y-4">
          <div className="grid grid-cols-3 gap-3">
            <StatusDatum label="Task" value={taskId ? `#${taskId}` : "—"} mono />
            <StatusDatum label="Формат" value={preview?.format_code ?? "—"} mono />
            <StatusDatum label="Создано ID" value={String(createdCount)} mono />
          </div>

          {!failed && !success ? (
            <div>
              <div className="mb-1.5 flex items-center justify-between text-[10.5px] text-bg-8">
                <span>{progressMessage}</span>
                {total > 0 ? (
                  <span className="font-mono">
                    {completed}/{total}
                  </span>
                ) : null}
              </div>
              <div
                role="progressbar"
                aria-label="Прогресс создания структуры"
                aria-valuemin={0}
                aria-valuemax={total > 0 ? total : undefined}
                aria-valuenow={total > 0 ? Math.min(total, Math.max(0, completed)) : undefined}
                aria-valuetext={
                  total > 0 ? `${progressMessage}: ${completed} из ${total}` : progressMessage
                }
                className="h-1.5 overflow-hidden rounded-full bg-bg-4"
              >
                <div
                  aria-hidden="true"
                  className={cn(
                    "h-full bg-accent transition-[width] duration-500",
                    total === 0 && "w-1/3 animate-pulse",
                  )}
                  style={total > 0 ? { width: `${progressPercent}%` } : undefined}
                />
              </div>
            </div>
          ) : null}

          {success ? (
            <div className="flex items-center gap-2 text-[12px] text-success">
              <CheckCircle2 size={15} aria-hidden="true" />
              Структура создана и проверена. Объекты активированы с будущим временем старта.
            </div>
          ) : null}

          {failed ? (
            <div role="alert" className="flex items-start gap-2 text-[12px] text-danger">
              <TriangleAlert size={15} className="mt-0.5 shrink-0" aria-hidden="true" />
              <span>{statusData?.error || `Задача завершилась со статусом ${status}`}</span>
            </div>
          ) : null}

          {failed && createdCount > 0 ? (
            <div
              role="alert"
              className="rounded-[var(--radius-2)] border border-[rgba(199,154,92,0.35)] bg-[rgba(199,154,92,0.08)] p-3 text-[11.5px] text-bg-10"
            >
              <div className="font-display text-[10px] uppercase tracking-[0.1em] text-accent-muted">
                Частичная структура в Meta
              </div>
              <p className="mt-1 leading-relaxed">
                Создано объектов: {createdCount}. Защитный контур должен был оставить их PAUSED;
                перед новым дублем проверьте это вручную.
              </p>
              {createdIdSample.length ? (
                <div className="mt-2 break-all font-mono text-[9.5px] text-bg-9">
                  {createdIdSample.join(" · ")}
                  {createdCount > createdIdSample.length ? " · …" : ""}
                </div>
              ) : null}
            </div>
          ) : null}

          {failed ? (
            <p className="text-[11.5px] leading-relaxed text-bg-9">
              Чтобы повторить операцию, закройте окно и снова нажмите «Дублировать структуру». Новый
              запуск получит отдельный idempotency token.
            </p>
          ) : null}

          {loading ? (
            <div className="text-[11px] text-bg-8 animate-pulse">Читаем статус…</div>
          ) : null}
          {error ? <InlineError error={error} /> : null}
        </div>
      </div>
      <p className="text-center text-[10.5px] text-bg-8">
        Статус обновляется автоматически каждые 2 секунды. Окно можно закрыть.
      </p>
    </div>
  );
}

function SectionLabel({
  index,
  label,
  compact = false,
}: {
  index: string;
  label: string;
  compact?: boolean;
}) {
  return (
    <div className={cn("flex items-center gap-2", !compact && "mb-3")}>
      <span className="font-mono text-[9px] text-accent-muted">{index}</span>
      <span className="font-display text-[10px] uppercase tracking-[0.12em] text-bg-9">
        {label}
      </span>
    </div>
  );
}

function SourceRow({
  label,
  value,
  detail,
  border = false,
}: {
  label: string;
  value: string;
  detail?: string;
  border?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex items-center justify-between gap-4 px-3 py-2.5",
        border && "border-t border-[var(--hairline)]",
      )}
    >
      <span className="font-display text-[9px] uppercase tracking-[0.1em] text-bg-8 shrink-0">
        {label}
      </span>
      <span className="min-w-0 text-right">
        <span className="block truncate text-[12px] text-bg-11" title={value}>
          {value || "—"}
        </span>
        {detail ? <span className="block truncate text-[9.5px] text-bg-8">{detail}</span> : null}
      </span>
    </div>
  );
}

function PreviewMetric({
  label,
  value,
  accent = false,
}: {
  label: string;
  value: number;
  accent?: boolean;
}) {
  return (
    <div className="rounded-[var(--radius-2)] border border-[var(--hairline)] bg-bg-2 px-3 py-3">
      <div className="font-display text-[9px] uppercase tracking-[0.1em] text-bg-8">{label}</div>
      <div
        className={cn(
          "mt-1 font-mono text-[22px] leading-none",
          accent ? "text-accent-muted" : "text-bg-11",
        )}
      >
        {value}
      </div>
    </div>
  );
}

export function BudgetAmountField({
  cents,
  currency,
  budgetLevel,
  unitCount,
  onCents,
}: {
  cents: number;
  currency: string;
  budgetLevel: DuplicateBudgetLevel;
  unitCount: number;
  onCents: (cents: number) => void;
}) {
  const inputId = useId();
  const summaryId = `${inputId}-summary`;
  const [text, setText] = useState(() => budgetCentsToText(cents));

  useEffect(() => {
    if (budgetTextToCents(text) !== cents) setText(budgetCentsToText(cents));
  }, [cents, text]);

  const safeUnitCount = Math.max(0, unitCount);
  const totalCents = cents * safeUnitCount;
  const unitLabel = budgetLevel === "ABO" ? "на каждый адсет" : "на каждую кампанию";

  function handleChange(rawValue: string) {
    const nextText = normalizeBudgetText(rawValue);
    setText(nextText);
    onCents(budgetTextToCents(nextText));
  }

  return (
    <div className="overflow-hidden rounded-[var(--radius-2)] border border-[var(--hairline-strong)] bg-bg-2">
      <div className="flex items-center justify-between gap-3 border-b border-[var(--hairline)] px-3.5 py-2.5">
        <label
          htmlFor={inputId}
          className="font-display text-[11px] uppercase tracking-wider text-bg-9"
        >
          Дневной бюджет
        </label>
        <span className="rounded-full border border-[var(--hairline)] bg-bg-1 px-2 py-1 font-mono text-[9.5px] text-bg-9">
          {budgetLevel} · {currency}
        </span>
      </div>

      <div className="space-y-3 p-3.5">
        <div className="flex min-w-0 items-center rounded-[var(--radius-2)] border border-bg-6 bg-bg-1 transition-colors focus-within:border-accent focus-within:bg-bg-3 focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-accent">
          <span
            aria-hidden="true"
            className="shrink-0 border-r border-[var(--hairline)] px-3.5 font-mono text-[22px] text-accent-muted"
          >
            {currencySymbol(currency)}
          </span>
          <input
            id={inputId}
            type="text"
            inputMode="decimal"
            autoComplete="off"
            value={text}
            aria-label={`Дневной бюджет · ${budgetLevel} · ${currency}`}
            aria-describedby={summaryId}
            placeholder="100"
            onChange={(event) => handleChange(event.target.value)}
            onBlur={() => setText(budgetCentsToText(cents))}
            className="h-14 min-w-0 flex-1 bg-transparent px-3.5 font-mono text-[25px] tabular-nums text-bg-11 outline-none placeholder:text-bg-7"
          />
          <span className="shrink-0 pr-3.5 text-right">
            <span className="block font-mono text-[10px] text-bg-10">{currency}</span>
            <span className="block text-[9.5px] text-bg-8">в день</span>
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-1.5" aria-label="Быстрый выбор бюджета">
          <span className="mr-1 text-[10px] text-bg-8">Быстро:</span>
          {QUICK_BUDGET_AMOUNTS.map((amount) => {
            const selected = cents === amount * 100;
            return (
              <button
                key={amount}
                type="button"
                aria-label={`Установить дневной бюджет ${amount} ${currency}`}
                aria-pressed={selected}
                onClick={() => onCents(amount * 100)}
                className={cn(
                  "rounded-full border px-2.5 py-1 font-mono text-[10.5px] transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
                  selected
                    ? "border-accent bg-bg-3 text-accent-muted"
                    : "border-[var(--hairline)] bg-bg-1 text-bg-9 hover:border-bg-7 hover:text-bg-11",
                )}
              >
                {currencySymbol(currency)}
                {amount}
              </button>
            );
          })}
        </div>

        <div
          id={summaryId}
          className="flex flex-col gap-1 border-t border-[var(--hairline)] pt-3 text-[11px] sm:flex-row sm:items-center sm:justify-between sm:gap-4"
        >
          <span className="text-bg-8">
            {unitLabel} · {formatMoney(cents, currency)} × {safeUnitCount}
          </span>
          <span className="font-medium text-bg-11">
            Итого: {formatMoney(totalCents, currency)} / день
          </span>
        </div>
      </div>
    </div>
  );
}

function NameList({ label, values }: { label: string; values: string[] }) {
  return (
    <div className="min-w-0">
      <div className="mb-1 font-display text-[9px] uppercase tracking-[0.1em] text-bg-8">
        {label}
      </div>
      <div className="space-y-1">
        {values.slice(0, 4).map((value) => (
          <div key={value} className="truncate font-mono text-[10.5px] text-bg-10" title={value}>
            {value}
          </div>
        ))}
        {values.length > 4 ? (
          <div className="text-[10px] text-bg-8">+ ещё {values.length - 4}</div>
        ) : null}
      </div>
    </div>
  );
}

function StatusDatum({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div>
      <div className="font-display text-[9px] uppercase tracking-[0.1em] text-bg-8">{label}</div>
      <div className={cn("mt-1 truncate text-[12px] text-bg-11", mono && "font-mono")}>{value}</div>
    </div>
  );
}

function InlineError({ error, className }: { error: Error; className?: string }) {
  return (
    <div
      role="alert"
      className={cn(
        "flex items-start gap-2 rounded-[var(--radius-2)] border border-[rgba(199,98,92,0.3)] bg-danger-bg p-3 text-[11.5px] text-danger",
        className,
      )}
    >
      <TriangleAlert size={13} className="mt-0.5 shrink-0" aria-hidden="true" />
      <span>{error.message}</span>
    </div>
  );
}

function initialForm(ad: AdSnapshot): DuplicateFormState {
  const rawBudget = Number((ad as AdSnapshotWithBudget).adset_daily_budget ?? 0);
  return {
    campaignCount: 3,
    adsetsPerCampaign: 2,
    budgetLevel: Number.isFinite(rawBudget) && rawBudget > 0 ? "ABO" : "CBO",
    // Money-настройку источника не наследуем молча: новый draft начинается
    // с фиксированного baseline, который оператор явно видит и подтверждает.
    dailyBudgetCents: 10_000,
    campaignNameBase: ad.campaign_name ?? "",
    adsetNameBase: ad.adset_name ?? "",
    startDate: tomorrowDateInTimeZone(),
  };
}

function buildPreviewRequest(
  sourceAdId: string,
  form: DuplicateFormState,
  selectedAdIds: string[],
  idempotencyToken: string,
): AdsetDuplicatePreviewIn {
  return {
    source_ad_id: sourceAdId,
    selected_ad_ids: selectedAdIds,
    campaign_count: form.campaignCount,
    adsets_per_campaign: form.adsetsPerCampaign,
    budget_level: form.budgetLevel,
    daily_budget_cents: form.dailyBudgetCents,
    start_date: form.startDate,
    campaign_name_base: form.campaignNameBase.trim() || null,
    adset_name_base: form.adsetNameBase.trim() || null,
    idempotency_token: idempotencyToken,
  };
}

function validateForm(form: DuplicateFormState, selectedCount: number): string | null {
  if (selectedCount < 1) return "Выберите хотя бы одно объявление из исходного адсета";
  if (selectedCount > 10) return "Можно выбрать не больше 10 объявлений";
  if (form.campaignCount < 1 || form.campaignCount > 5) return "Кампаний: от 1 до 5";
  if (form.adsetsPerCampaign < 1 || form.adsetsPerCampaign > 10) return "Адсетов: от 1 до 10";
  const totalAds = form.campaignCount * form.adsetsPerCampaign * selectedCount;
  if (totalAds > 50) return `Получится ${totalAds} объявлений — максимум 50`;
  if (form.dailyBudgetCents < 100) return "Минимальный дневной бюджет — 1.00";
  if (form.dailyBudgetCents > MAX_DAILY_BUDGET_CENTS) {
    return "Максимальный дневной бюджет — 100 000.00";
  }
  if (!form.startDate) return "Выберите дату старта";
  return null;
}

function sourceAdId(ad: DuplicateSourceAd): string {
  return ad.fb_ad_id || ad.id;
}

function readPositiveInt(value: string): number {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : 0;
}

function tomorrowDateInTimeZone(timeZone?: string, offset?: string): string {
  let year: number;
  let month: number;
  let day: number;
  try {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(new Date());
    year = Number(parts.find((part) => part.type === "year")?.value);
    month = Number(parts.find((part) => part.type === "month")?.value);
    day = Number(parts.find((part) => part.type === "day")?.value);
  } catch (error) {
    if (!(error instanceof RangeError)) throw error;
    const offsetMatch = /^([+-])(\d{2}):(\d{2})$/.exec(offset ?? "");
    const direction = offsetMatch?.[1] === "-" ? -1 : 1;
    const offsetMinutes = offsetMatch
      ? direction * (Number(offsetMatch[2]) * 60 + Number(offsetMatch[3]))
      : 0;
    const accountNow = new Date(Date.now() + offsetMinutes * 60_000);
    year = accountNow.getUTCFullYear();
    month = accountNow.getUTCMonth() + 1;
    day = accountNow.getUTCDate();
  }
  const tomorrow = new Date(Date.UTC(year, month - 1, day + 1));
  return tomorrow.toISOString().slice(0, 10);
}

function newIdempotencyToken(): string {
  return globalThis.crypto.randomUUID();
}

function formatMoney(cents: number, currency: string): string {
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: currency || "USD",
    maximumFractionDigits: 2,
  }).format(cents / 100);
}

function currencySymbol(currency: string): string {
  try {
    const parts = new Intl.NumberFormat("en", {
      style: "currency",
      currency: currency || "USD",
      currencyDisplay: "narrowSymbol",
    }).formatToParts(0);
    return parts.find((part) => part.type === "currency")?.value || currency;
  } catch (error) {
    if (!(error instanceof RangeError)) throw error;
    return currency || "$";
  }
}

function normalizeBudgetText(rawValue: string): string {
  const cleaned = rawValue.replace(",", ".").replace(/[^\d.]/g, "");
  const decimalIndex = cleaned.indexOf(".");
  if (decimalIndex < 0) return cleaned;

  const whole = cleaned.slice(0, decimalIndex).replace(/\D/g, "") || "0";
  const fraction = cleaned
    .slice(decimalIndex + 1)
    .replace(/\D/g, "")
    .slice(0, 2);
  return `${whole}.${fraction}`;
}

function budgetTextToCents(value: string): number {
  if (!value) return 0;
  const amount = Number(value);
  return Number.isFinite(amount) && amount >= 0 ? Math.round(amount * 100) : 0;
}

function budgetCentsToText(cents: number): string {
  return cents > 0 ? String(cents / 100) : "";
}

function formatAccountId(value: string): string {
  return value.startsWith("act_") ? value : `act_${value}`;
}

function formatSchedule(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

/** Keep account-local wall clock intact instead of converting it to browser TZ. */
function formatAccountLocal(value: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value);
  if (!match) return value;
  return `${match[3]}.${match[2]}.${match[1]}, ${match[4]}:${match[5]}`;
}

function duplicateStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: "В очереди",
    draft: "Черновик",
    pending_confirmation: "Ждёт подтверждения",
    awaiting_confirmation: "Ждёт подтверждения",
    confirmed: "Подтверждено",
    queued: "В очереди",
    running: "Создаётся",
    succeeded: "Готово",
    failed: "Ошибка",
    cancelled: "Отменено",
    expired: "Истёк",
  };
  return labels[status] ?? status;
}

function countCreatedMetaIds(value: Record<string, string | string[]>): number {
  return Object.values(value).reduce(
    (total, item) => total + (Array.isArray(item) ? item.length : item ? 1 : 0),
    0,
  );
}

function listCreatedMetaIds(value: Record<string, string | string[]>): string[] {
  return Object.values(value).flatMap((item) => (Array.isArray(item) ? item : item ? [item] : []));
}
