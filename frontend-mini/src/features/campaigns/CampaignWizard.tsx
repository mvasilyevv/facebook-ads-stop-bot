import { useEffect, useRef, useState } from "react";
import {
  aggregateCampaignLaunchState,
  CAMPAIGN_GENDER_OPTIONS,
  CAMPAIGN_PLACEMENT_OPTIONS,
  buildCampaignConfig,
  campaignPresetsDataState,
  nextCampaignKey,
  validateCampaignStep,
  type CampaignWizardCampaign,
  type CampaignWizardConcept,
  type CampaignWizardStep,
  type CampaignLaunchObservedState,
} from "@fb/features/campaigns";
import { safeApiProblemMessage } from "@fb/operator-api";
import { ChoiceTagListInput } from "@fb/operator-ui";
import { Link } from "@tanstack/react-router";
import {
  AlertTriangle,
  Check,
  ChevronLeft,
  ChevronRight,
  FileUp,
  Layers3,
  Plus,
  RefreshCw,
  ShieldCheck,
  Trash2,
} from "lucide-react";

import { Button, Card, Input, Select, Skeleton, TagListInput } from "@/components/ui";
import { useOffers } from "@/lib/api";
import {
  useCampaignAccountContext,
  useCampaignAccountPages,
  useCampaignAccountPixels,
  useCampaignRunDetails,
  useCampaignPresets,
  useLaunchCampaign,
  useUploadCampaignConcepts,
  useValidateCampaignConfig,
} from "@/lib/campaigns";
import type { LaunchOut } from "@/lib/campaigns";
import { cn } from "@/lib/cn";
import { haptic } from "@/lib/tg";
import { CampaignTagPicker } from "./CampaignTagPicker";
import { useCampaignWizardDraft } from "./useCampaignWizardDraft";

const STEP_LABELS = [
  "Старт",
  "Кабинет",
  "Параметры",
  "Структура",
  "Креативы",
  "Превью",
  "Запуск",
] as const;

const CTA_OPTIONS = [
  { value: "PLAY_GAME", label: "Играть" },
  { value: "SIGN_UP", label: "Регистрация" },
  { value: "LEARN_MORE", label: "Подробнее" },
];

const ATTRIBUTION_OPTIONS = [
  { value: "1", label: "1 день" },
  { value: "7", label: "7 дней" },
  { value: "28", label: "28 дней" },
];

export function CampaignWizard() {
  const wizard = useCampaignWizardDraft();
  const presets = useCampaignPresets();
  const offers = useOffers();
  const context = useCampaignAccountContext();
  const pagesRequest = useCampaignAccountPages();
  const pixelsRequest = useCampaignAccountPixels();
  const upload = useUploadCampaignConcepts();
  const validate = useValidateCampaignConfig();
  const launch = useLaunchCampaign();
  const [pages, setPages] = useState<Array<{ id: string; name: string }>>([]);
  const [pixels, setPixels] = useState<Array<{ id: string; name: string }>>([]);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [problem, setProblem] = useState<string | null>(null);
  const [launchReceipt, setLaunchReceipt] = useState<LaunchOut | null>(null);

  if (wizard.isHydrating) {
    return (
      <div className="space-y-3 px-4 py-5" aria-busy="true" aria-label="Восстановление черновика">
        <Skeleton className="h-11 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (wizard.isHydrationError) {
    return (
      <div className="px-4 py-6">
        <Card className="border-danger/35">
          <p role="alert" className="text-[14px] leading-5 text-danger">
            Серверный черновик недоступен. Создание заблокировано, чтобы не потерять конфигурацию.
          </p>
          <Button
            variant="secondary"
            fullWidth
            className="mt-4"
            onClick={() => void wizard.reload()}
          >
            Повторить
          </Button>
        </Card>
      </div>
    );
  }

  const state = wizard.state;
  const selectedPreset =
    presets.data?.find((preset) => preset.id === state.start.preset_id) ?? null;
  const selectedOffer = offers.data?.find(
    (offer) => offer.code === state.identity.offer_code,
  );
  const offerAccounts = selectedOffer?.ad_account_ids?.filter(Boolean) ?? [];
  const presetsState = campaignPresetsDataState({
    isPending: presets.isPending,
    isError: presets.isError,
    count: presets.data?.length ?? 0,
  });
  let config: ReturnType<typeof buildCampaignConfig> | null = null;
  if (state.currentStep >= 6) {
    try {
      config = buildCampaignConfig(state);
    } catch {
      config = null;
    }
  }

  function patchIdentity(value: Parameters<typeof wizard.dispatch>[0] & { type: "patchIdentity" }) {
    wizard.dispatch(value);
  }

  function chooseOffer(code: string) {
    const offer = offers.data?.find((candidate) => candidate.code === code);
    const accounts = offer?.ad_account_ids?.filter(Boolean) ?? [];
    patchIdentity({
      type: "patchIdentity",
      value: {
        offer_code: code,
        pixel_id: offer?.pixel_id ?? "",
        ad_account_ids: accounts.length === 1 ? [accounts[0]!] : [],
        act_id: accounts.length === 1 ? accounts[0]! : "",
        account_context_state: "unavailable",
        timezone_name: "",
        currency: "",
        currency_exponent: null,
        account_context_observed_at: null,
        account_context_issue: null,
      },
    });
    if (offer?.countries?.length) {
      wizard.dispatch({
        type: "patchGoal",
        value: {
          countries: offer.countries.map((country) => country.toUpperCase()),
        },
      });
    }
  }

  function chooseAccounts(accountIds: string[]) {
    const primary = accountIds[0] ?? "";
    if (primary !== state.identity.act_id) setPages([]);
    patchIdentity({
      type: "patchIdentity",
      value: {
        ad_account_ids: accountIds,
        act_id: primary,
        page_id:
          primary === state.identity.act_id ? state.identity.page_id : "",
        account_context_state:
          primary === state.identity.act_id
            ? state.identity.account_context_state
            : "unavailable",
        timezone_name:
          primary === state.identity.act_id ? state.identity.timezone_name : "",
        currency:
          primary === state.identity.act_id ? state.identity.currency : "",
        currency_exponent:
          primary === state.identity.act_id
            ? state.identity.currency_exponent
            : null,
        account_context_observed_at:
          primary === state.identity.act_id
            ? state.identity.account_context_observed_at
            : null,
        account_context_issue: null,
      },
    });
  }

  async function loadAccountContext() {
    const actId = state.identity.act_id.trim();
    if (!/^(?:act_)?[0-9]+$/.test(actId)) {
      setErrors({ act_id: "Укажите числовой ID кабинета" });
      return;
    }
    setProblem(null);
    setErrors({});
    const [contextResult, pageResult, pixelResult] = await Promise.allSettled([
      context.mutateAsync(actId),
      pagesRequest.mutateAsync(actId),
      pixelsRequest.mutateAsync(actId),
    ]);
    if (pageResult.status === "fulfilled") setPages(pageResult.value.pages);
    // Пусто или отказ — остаётся ручной ввод pixel_id, как и у страниц.
    setPixels(pixelResult.status === "fulfilled" ? pixelResult.value.pixels : []);
    if (contextResult.status === "rejected") {
      patchIdentity({
        type: "patchIdentity",
        value: {
          account_context_state: "unavailable",
          timezone_name: "",
          currency: "",
          currency_exponent: null,
          account_context_observed_at: null,
          account_context_issue: "account_context_request_failed",
        },
      });
      setProblem(
        safeApiProblemMessage(
          contextResult.reason,
          "Не удалось подтвердить кабинет. Проверьте открытые вкладки Meta и повторите.",
        ),
      );
      haptic.notify("error");
      return;
    }
    const evidence = contextResult.value;
    const readyUsd =
      evidence.state === "ready" && evidence.currency === "USD" && evidence.currency_exponent === 2;
    patchIdentity({
      type: "patchIdentity",
      value: {
        account_context_state: readyUsd ? "ready" : evidence.state,
        timezone_name: evidence.timezone_name ?? "",
        currency: readyUsd ? "USD" : "",
        currency_exponent: readyUsd ? 2 : null,
        account_context_observed_at: evidence.observed_at,
        account_context_issue: readyUsd
          ? evidence.issue
          : (evidence.issue ?? "usd_context_required"),
      },
    });
    if (evidence.next_start_date) {
      wizard.dispatch({
        type: "patchGoal",
        value: { start_date: evidence.next_start_date },
      });
    }
    const offer = offers.data?.find((candidate) => candidate.code === state.identity.offer_code);
    if (readyUsd && offer?.currency === "USD" && offer.cpa_threshold) {
      wizard.dispatch({
        type: "patchGoal",
        value: { bid_amount: offer.cpa_threshold },
      });
    }
    if (!readyUsd) {
      setProblem("Кабинет не подтвердил USD-контекст. Запуск заблокирован.");
      haptic.notify("warning");
    } else {
      haptic.notify("success");
    }
  }

  function validateAndNext() {
    const nextErrors = validateCampaignStep(state, state.currentStep);
    if (state.currentStep === 6 && !wizard.plan) {
      nextErrors.preview = "Сначала получите подтверждённый план";
    }
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length === 0) {
      haptic.selection();
      wizard.dispatch({
        type: "goTo",
        step: Math.min(7, state.currentStep + 1) as CampaignWizardStep,
      });
    }
  }

  async function uploadFiles(files: File[]) {
    if (files.length === 0) return;
    setProblem(null);
    try {
      const response = await upload.mutateAsync({
        files,
        uploadId: state.creatives.upload_id,
      });
      const previousAssignments = new Map(
        state.creatives.concepts.map((concept) => [concept.ref, concept.campaign_keys]),
      );
      const allCampaigns = state.structure.campaigns.map((campaign) => campaign.key);
      wizard.dispatch({
        type: "patchCreatives",
        value: {
          upload_id: response.upload_id,
          concepts: response.concepts.map((concept) => ({
            ...concept,
            campaign_keys: previousAssignments.get(concept.ref) ?? allCampaigns,
          })),
        },
      });
      haptic.notify("success");
    } catch (error) {
      setProblem(safeApiProblemMessage(error, "Файлы не загружены. Проверьте формат и повторите."));
      haptic.notify("error");
    }
  }

  async function validatePlan() {
    if (!config) {
      setProblem("Конфигурация неполна. Вернитесь к предыдущим шагам.");
      return;
    }
    setProblem(null);
    try {
      wizard.setPlan(await validate.mutateAsync(config));
      haptic.notify("success");
    } catch (error) {
      setProblem(
        safeApiProblemMessage(error, "План не подтверждён. Проверьте кабинет и повторите."),
      );
      haptic.notify("error");
    }
  }

  async function queueLaunch() {
    if (!config || wizard.revision < 1 || wizard.syncState !== "saved") return;
    setProblem(null);
    try {
      const receipt = await launch.mutateAsync({
        config,
        ad_account_ids: state.identity.ad_account_ids ?? [],
        preset_id: state.start.preset_id ?? null,
        draft_revision: wizard.revision,
      });
      if (receipt.draft_cleared) wizard.markCleared();
      setLaunchReceipt(receipt);
      haptic.notify("success");
    } catch (error) {
      setProblem(
        safeApiProblemMessage(error, "Запуск не поставлен в очередь. Обновите план и повторите."),
      );
      haptic.notify("error");
    }
  }

  return (
    <div className="min-w-0 px-4 pb-[max(120px,var(--tg-content-safe-bottom),env(safe-area-inset-bottom))] pt-4">
      <DraftStatus state={wizard.syncState} updatedAt={wizard.updatedAt} onReload={wizard.reload} />

      <ol className="-mx-4 mb-5 flex gap-1 overflow-x-auto px-4 pb-2" aria-label="Шаги создания">
        {STEP_LABELS.map((label, index) => {
          const step = (index + 1) as CampaignWizardStep;
          const active = state.currentStep === step;
          const done = state.currentStep > step;
          return (
            <li key={step} className="shrink-0">
              <button
                type="button"
                aria-current={active ? "step" : undefined}
                disabled={step > state.currentStep}
                onClick={() => step <= state.currentStep && wizard.dispatch({ type: "goTo", step })}
                className={cn(
                  "flex min-h-11 items-center gap-2 rounded-[var(--radius-2)] border px-3 text-[12px] transition-colors disabled:opacity-40",
                  active
                    ? "border-accent bg-accent/10 text-accent"
                    : done
                      ? "border-success/30 text-success"
                      : "border-[var(--color-hairline)] text-bg-8",
                )}
              >
                <span aria-hidden="true">{done ? <Check size={13} /> : step}</span>
                {label}
              </button>
            </li>
          );
        })}
      </ol>

      {problem ? (
        <p
          role="alert"
          className="mb-4 rounded-[var(--radius-2)] border border-danger/35 bg-danger/10 p-3 text-[13px] leading-5 text-danger"
        >
          {problem}
        </p>
      ) : null}

      <section aria-label={`Шаг ${state.currentStep}: ${STEP_LABELS[state.currentStep - 1]}`}>
        {state.currentStep === 1 ? (
          <Card eyebrow="ШАГ 1 · СТАРТ" title="Как создать кампанию">
            <div className="grid gap-3">
              {(["new", "preset"] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  aria-pressed={state.start.mode === mode}
                  onClick={() =>
                    wizard.dispatch({
                      type: "patchStart",
                      value: { mode, preset_id: null },
                    })
                  }
                  className={cn(
                    "min-h-14 rounded-[var(--radius-2)] border p-3 text-left text-[14px]",
                    state.start.mode === mode
                      ? "border-accent bg-accent/10 text-bg-11"
                      : "border-[var(--color-hairline)] bg-bg-2 text-bg-9",
                  )}
                >
                  <strong className="block text-bg-11">
                    {mode === "new" ? "Новая конфигурация" : "Из пресета"}
                  </strong>
                  <span className="mt-1 block text-[12px]">
                    {mode === "new" ? "Заполнить параметры с нуля" : "Взять проверенные defaults"}
                  </span>
                </button>
              ))}
            </div>
            {state.start.mode === "preset" ? (
              <div className="mt-4 space-y-3" data-state={presetsState}>
                {presetsState === "stale" ? (
                  <Skeleton className="h-12 w-full" />
                ) : presetsState === "unavailable" ? (
                  <div
                    role="alert"
                    className="rounded-[var(--radius-2)] border border-warning/35 bg-warning/10 p-3 text-[13px] leading-5 text-bg-10"
                  >
                    <p>Пресеты сейчас недоступны. Кампанию можно заполнить вручную.</p>
                    <Button
                      variant="secondary"
                      fullWidth
                      className="mt-3"
                      onClick={() => void presets.refetch()}
                    >
                      <RefreshCw size={15} aria-hidden="true" />
                      Повторить загрузку
                    </Button>
                  </div>
                ) : presetsState === "empty" ? (
                  <div className="rounded-[var(--radius-2)] border border-[var(--color-hairline)] bg-bg-2 p-3 text-[13px] leading-5 text-bg-9">
                    <p className="flex items-center gap-2 font-semibold text-bg-11">
                      <Layers3 size={16} aria-hidden="true" />
                      Пресетов пока нет
                    </p>
                    <p className="mt-1">Создайте первый или продолжите без шаблона.</p>
                    <Link
                      to="/campaigns/presets"
                      className="mt-3 inline-flex min-h-11 items-center text-accent"
                    >
                      Управлять пресетами
                    </Link>
                  </div>
                ) : (
                  <Select
                    label="Пресет"
                    value={state.start.preset_id ?? ""}
                    errorMessage={errors.preset_id}
                    options={[
                      { value: "", label: "Выберите пресет" },
                      ...(presets.data ?? []).map((preset) => ({
                        value: preset.id,
                        label: preset.name,
                      })),
                    ]}
                    onChange={(event) => {
                      const preset = presets.data?.find((item) => item.id === event.target.value);
                      if (preset) wizard.applyPreset(preset);
                    }}
                  />
                )}
                {selectedPreset ? (
                  <div className="rounded-[var(--radius-2)] border border-accent/30 bg-accent/5 p-3 text-[12px] leading-5 text-bg-9">
                    <strong className="block text-[13px] text-bg-11">
                      Подставлено из «{selectedPreset.name}»
                    </strong>
                    {selectedPreset.countries.join(" · ")} · {selectedPreset.age_min}–
                    {selectedPreset.age_max} · ${selectedPreset.daily_budget ?? "—"} · Purchase
                    <span className="mt-1 block text-accent">
                      Все подставленные поля можно изменить на шаге «Параметры».
                    </span>
                  </div>
                ) : null}
                {presetsState !== "empty" ? (
                  <Link
                    to="/campaigns/presets"
                    className="inline-flex min-h-11 items-center text-[13px] text-accent"
                  >
                    Управлять пресетами
                  </Link>
                ) : null}
              </div>
            ) : null}
          </Card>
        ) : null}

        {state.currentStep === 2 ? (
          <Card eyebrow="ШАГ 2 · КАБИНЕТ" title="Оффер и Meta-контекст">
            <div className="space-y-4">
              <Select
                label="Оффер"
                value={state.identity.offer_code}
                errorMessage={errors.offer_code}
                options={[
                  { value: "", label: "Выберите оффер" },
                  ...(offers.data ?? []).map((offer) => ({
                    value: offer.code,
                    label: offer.name,
                  })),
                ]}
                onChange={(event) => chooseOffer(event.target.value)}
              />
              <ChoiceTagListInput
                label="Кабинеты оффера"
                values={state.identity.ad_account_ids ?? []}
                options={offerAccounts.map((accountId) => ({
                  value: accountId,
                  label: `act_${accountId}`,
                }))}
                onChange={chooseAccounts}
                placeholder="Добавить кабинет оффера"
                selectAllLabel={
                  offerAccounts.length > 1 ? "Выбрать все" : undefined
                }
                errorMessage={errors.ad_account_ids ?? errors.act_id}
                disabled={offerAccounts.length === 0}
                helpText="По одному независимому run на кабинет. Первый используется для preview и списка страниц."
              />
              <Button
                variant="secondary"
                fullWidth
                loading={context.isPending || pagesRequest.isPending || pixelsRequest.isPending}
                onClick={() => void loadAccountContext()}
              >
                Подтвердить основной кабинет
              </Button>
              <ContextEvidence identity={state.identity} />
              {pages.length > 0 ? (
                <Select
                  label="Страница Facebook"
                  value={state.identity.page_id}
                  errorMessage={errors.page_id}
                  options={[
                    { value: "", label: "Выберите страницу" },
                    ...pages.map((page) => ({
                      value: page.id,
                      label: page.name,
                    })),
                  ]}
                  onChange={(event) =>
                    patchIdentity({
                      type: "patchIdentity",
                      value: { page_id: event.target.value },
                    })
                  }
                />
              ) : (
                <Input
                  label="Page ID"
                  value={state.identity.page_id}
                  errorMessage={errors.page_id}
                  onChange={(event) =>
                    patchIdentity({
                      type: "patchIdentity",
                      value: { page_id: event.target.value },
                    })
                  }
                />
              )}
              {pixels.length > 0 ? (
                <Select
                  label="Pixel"
                  value={state.identity.pixel_id}
                  errorMessage={errors.pixel_id}
                  options={[
                    { value: "", label: "Выберите пиксель" },
                    ...pixels.map((pixel) => ({
                      value: pixel.id,
                      label: pixel.name,
                    })),
                  ]}
                  onChange={(event) =>
                    patchIdentity({
                      type: "patchIdentity",
                      value: { pixel_id: event.target.value },
                    })
                  }
                />
              ) : (
                <Input
                  label="Pixel ID"
                  value={state.identity.pixel_id}
                  errorMessage={errors.pixel_id}
                  onChange={(event) =>
                    patchIdentity({
                      type: "patchIdentity",
                      value: { pixel_id: event.target.value },
                    })
                  }
                />
              )}
              <Input
                label="Тег байера"
                value={state.identity.byer_tag}
                onChange={(event) =>
                  patchIdentity({
                    type: "patchIdentity",
                    value: { byer_tag: event.target.value.toUpperCase() },
                  })
                }
              />
            </div>
          </Card>
        ) : null}

        {state.currentStep === 3 ? (
          <GoalStep
            state={state}
            errors={errors}
            dispatch={wizard.dispatch}
            appliedPresetName={selectedPreset?.name ?? null}
          />
        ) : null}

        {state.currentStep === 4 ? (
          <StructureStep
            campaigns={state.structure.campaigns}
            error={errors.structure}
            onChange={(campaigns) => wizard.dispatch({ type: "setCampaigns", campaigns })}
          />
        ) : null}

        {state.currentStep === 5 ? (
          <CreativesStep
            concepts={state.creatives.concepts}
            campaigns={state.structure.campaigns}
            error={errors.creatives}
            uploading={upload.isPending}
            onUpload={uploadFiles}
            onChange={(value) => wizard.dispatch({ type: "patchCreatives", value })}
          />
        ) : null}

        {state.currentStep === 6 ? (
          <PreviewStep
            plan={wizard.plan}
            configReady={config !== null}
            loading={validate.isPending}
            onValidate={() => void validatePlan()}
          />
        ) : null}

        {state.currentStep === 7 && config ? (
          <LaunchStep
            config={config}
            accountIds={state.identity.ad_account_ids ?? []}
            draftReady={wizard.revision > 0 && wizard.syncState === "saved"}
            receipt={launchReceipt}
            loading={launch.isPending}
            onLaunch={() => void queueLaunch()}
          />
        ) : null}
      </section>

      {errors.preview ? (
        <p role="alert" className="mt-3 text-[13px] text-warning">
          {errors.preview}
        </p>
      ) : null}

      {!launchReceipt ? (
        <nav className="fixed inset-x-0 bottom-[calc(64px+var(--tg-content-safe-bottom))] z-30 border-t border-[var(--color-hairline)] bg-bg-0/95 px-[max(16px,var(--tg-content-safe-left))] py-3 backdrop-blur">
          <div className="mx-auto flex max-w-xl items-center justify-between gap-3">
            <Button
              variant="secondary"
              disabled={state.currentStep === 1}
              onClick={() =>
                wizard.dispatch({
                  type: "goTo",
                  step: Math.max(1, state.currentStep - 1) as CampaignWizardStep,
                })
              }
            >
              <ChevronLeft size={16} aria-hidden="true" />
              Назад
            </Button>
            {state.currentStep < 7 ? (
              <Button onClick={validateAndNext}>
                {state.currentStep === 6 ? "Подтвердить план" : "Далее"}
                <ChevronRight size={16} aria-hidden="true" />
              </Button>
            ) : null}
          </div>
        </nav>
      ) : null}
    </div>
  );
}

function DraftStatus({
  state,
  updatedAt,
  onReload,
}: {
  state: ReturnType<typeof useCampaignWizardDraft>["syncState"];
  updatedAt: string | null;
  onReload: () => Promise<void>;
}) {
  if (state === "conflict") {
    return (
      <div
        role="alert"
        className="mb-4 rounded-[var(--radius-2)] border border-warning/40 bg-warning/10 p-3"
      >
        <p className="flex items-start gap-2 text-[13px] leading-5 text-bg-10">
          <AlertTriangle size={16} className="mt-0.5 shrink-0 text-warning" aria-hidden="true" />
          Черновик изменён в другой вкладке.
        </p>
        <Button variant="secondary" fullWidth className="mt-3" onClick={() => void onReload()}>
          Загрузить серверную версию
        </Button>
      </div>
    );
  }
  const label =
    state === "saving"
      ? "Сохраняем на сервере…"
      : state === "error"
        ? "Черновик пока не сохранён"
        : updatedAt
          ? `Сохранён ${new Date(updatedAt).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}`
          : "Серверный черновик готов";
  return (
    <p
      role={state === "error" ? "alert" : "status"}
      className={cn("mb-4 text-[12px]", state === "error" ? "text-warning" : "text-bg-8")}
    >
      {label}
    </p>
  );
}

function ContextEvidence({
  identity,
}: {
  identity: ReturnType<typeof useCampaignWizardDraft>["state"]["identity"];
}) {
  const ready = identity.account_context_state === "ready" && identity.currency === "USD";
  return (
    <div
      role="status"
      className={cn(
        "rounded-[var(--radius-2)] border p-3 text-[13px] leading-5",
        ready
          ? "border-success/35 bg-success/10 text-success"
          : "border-warning/35 bg-warning/10 text-bg-9",
      )}
    >
      <strong className="flex items-center gap-2 text-bg-11">
        {ready ? (
          <ShieldCheck size={16} aria-hidden="true" />
        ) : (
          <AlertTriangle size={16} aria-hidden="true" />
        )}
        {ready ? "USD-контекст подтверждён" : "Контекст не подтверждён"}
      </strong>
      {/* Причина приходит из ответа ручки: без неё оператор гадает, что именно
          не так с кабинетом. */}
      {ready
        ? `${identity.timezone_name} · USD · данные Meta свежие`
        : (identity.account_context_issue ?? "Денежные поля и запуск заблокированы.")}
    </div>
  );
}

function GoalStep({
  state,
  errors,
  dispatch,
  appliedPresetName,
}: {
  state: ReturnType<typeof useCampaignWizardDraft>["state"];
  errors: Record<string, string>;
  dispatch: ReturnType<typeof useCampaignWizardDraft>["dispatch"];
  appliedPresetName: string | null;
}) {
  const patch = (value: Parameters<typeof dispatch>[0] & { type: "patchGoal" }) => dispatch(value);
  return (
    <Card eyebrow="ШАГ 3 · ПАРАМЕТРЫ" title="Бюджет, цель и аудитория">
      <div className="space-y-4">
        {appliedPresetName ? (
          <div
            role="status"
            className="rounded-[var(--radius-2)] border border-accent/30 bg-accent/5 p-3 text-[13px] leading-5 text-bg-9"
          >
            Значения из «{appliedPresetName}» уже подставлены. Проверьте и измените любые из них
            перед запуском.
          </div>
        ) : null}
        <div className="rounded-[var(--radius-2)] border border-accent/25 bg-accent/5 p-3 text-[13px] text-bg-9">
          Все денежные значения — только USD. Campaign, ad set и ad будут созданы PAUSED.
        </div>
        <Input
          label="Дневной бюджет, USD"
          inputMode="decimal"
          value={state.goal.daily_budget}
          errorMessage={errors.daily_budget}
          onChange={(event) =>
            patch({
              type: "patchGoal",
              value: { daily_budget: event.target.value },
            })
          }
        />
        <Input
          label="Целевой CPA, USD"
          inputMode="decimal"
          value={state.goal.bid_amount}
          errorMessage={errors.bid_amount}
          onChange={(event) =>
            patch({
              type: "patchGoal",
              value: { bid_amount: event.target.value },
            })
          }
        />
        <Input
          label="Трекинговая ссылка"
          type="url"
          value={state.goal.destination_link}
          errorMessage={errors.destination_link}
          onChange={(event) =>
            patch({
              type: "patchGoal",
              value: { destination_link: event.target.value },
            })
          }
        />
        <TagListInput
          label="Страны, ISO-2"
          values={state.goal.countries}
          errorMessage={errors.countries}
          placeholder="Введите US и нажмите Enter"
          normalize={(value) => value.toUpperCase()}
          validate={(value) =>
            /^[A-Z]{2}$/.test(value) ? null : `Некорректный ISO-2 код: ${value}`
          }
          onChange={(countries) => patch({ type: "patchGoal", value: { countries } })}
        />
        <Input
          label="Дата старта"
          type="date"
          value={state.goal.start_date}
          errorMessage={errors.start_date}
          onChange={(event) =>
            patch({
              type: "patchGoal",
              value: { start_date: event.target.value },
            })
          }
        />
        <Select
          label="Уровень бюджета"
          value={state.goal.budget_level}
          options={[
            { value: "campaign", label: "CBO · на кампании" },
            { value: "adset", label: "ABO · на ad set" },
          ]}
          onChange={(event) =>
            patch({
              type: "patchGoal",
              value: {
                budget_level: event.target.value === "adset" ? "adset" : "campaign",
              },
            })
          }
        />
        <Select
          label="CTA"
          value={state.goal.cta}
          options={CTA_OPTIONS}
          onChange={(event) => patch({ type: "patchGoal", value: { cta: event.target.value } })}
        />
        <div className="grid grid-cols-2 gap-3">
          <Input
            label="Возраст от"
            type="number"
            min={18}
            max={65}
            value={state.goal.age_min}
            onChange={(event) =>
              patch({
                type: "patchGoal",
                value: { age_min: Number(event.target.value) },
              })
            }
          />
          {/* При Advantage+ билдер форсит 65 (Meta иначе отвергает adset) —
              показываем то, что реально уедет, а не выбор под замену. */}
          <Input
            label="Возраст до"
            type="number"
            min={18}
            max={65}
            disabled={state.goal.advantage_audience}
            value={state.goal.advantage_audience ? 65 : state.goal.age_max}
            helpText={
              state.goal.advantage_audience
                ? "Advantage+ сам расширяет аудиторию"
                : undefined
            }
            onChange={(event) =>
              patch({
                type: "patchGoal",
                value: { age_max: Number(event.target.value) },
              })
            }
          />
        </div>
        <CampaignTagPicker
          label="Пол"
          values={state.goal.genders}
          options={CAMPAIGN_GENDER_OPTIONS}
          emptyLabel="Все полы"
          onChange={(genders) => patch({ type: "patchGoal", value: { genders } })}
        />
        <CampaignTagPicker
          label="Плейсменты"
          values={state.goal.placements}
          options={CAMPAIGN_PLACEMENT_OPTIONS}
          emptyLabel="Автоматические плейсменты Meta"
          onChange={(placements) => patch({ type: "patchGoal", value: { placements } })}
        />
        <div className="rounded-[var(--radius-2)] border border-[var(--color-hairline)] bg-bg-2 p-3">
          <p className="text-[12px] uppercase tracking-[0.08em] text-bg-9">
            Событие оптимизации пикселя
          </p>
          <strong className="mt-1 block text-[14px] text-bg-11">Purchase</strong>
          <p className="mt-1 text-[12px] leading-5 text-bg-8">
            Зафиксировано правилом проекта и не меняется пресетом.
          </p>
        </div>
        <Input
          label="Шаблон нейминга"
          value={state.goal.naming_template}
          placeholder="{byer} | {offer} | adset.pro | {date}"
          helpText="Подставляются {byer}, {offer}, {type}, {date} — остальное уедет буквально"
          onChange={(event) =>
            patch({
              type: "patchGoal",
              value: { naming_template: event.target.value },
            })
          }
        />
        <Input
          label="URL tags"
          value={state.goal.url_tags_template}
          placeholder="sub2=mv&sub5={{campaign.name}}"
          helpText="Строка уедет буквально: подставляет только Meta ({{campaign.name}}). sub8={{ad.id}} сервер добавит сам"
          onChange={(event) =>
            patch({
              type: "patchGoal",
              value: { url_tags_template: event.target.value },
            })
          }
        />
        <div className="grid grid-cols-2 gap-3">
          <Select
            label="Click-through"
            value={String(state.goal.click_through_days)}
            options={ATTRIBUTION_OPTIONS}
            onChange={(event) =>
              patch({
                type: "patchGoal",
                value: { click_through_days: asDays(event.target.value) },
              })
            }
          />
          <Select
            label="View-through"
            value={String(state.goal.view_through_days)}
            options={ATTRIBUTION_OPTIONS}
            onChange={(event) =>
              patch({
                type: "patchGoal",
                value: { view_through_days: asDays(event.target.value) },
              })
            }
          />
        </div>
        <label className="flex min-h-11 items-center gap-3 rounded-[var(--radius-2)] border border-[var(--color-hairline)] px-3 text-[14px] text-bg-10">
          <input
            type="checkbox"
            checked={state.goal.advantage_audience}
            onChange={(event) =>
              patch({
                type: "patchGoal",
                value: { advantage_audience: event.target.checked },
              })
            }
            className="size-5"
          />
          Advantage+ Audience
        </label>
        <Select
          label="Текст объявления"
          value={state.goal.ad_text_mode}
          options={[
            { value: "none", label: "Без текста" },
            { value: "text", label: "Задать primary text" },
          ]}
          onChange={(event) =>
            patch({
              type: "patchGoal",
              value: {
                ad_text_mode: event.target.value === "text" ? "text" : "none",
              },
            })
          }
        />
        {state.goal.ad_text_mode === "text" ? (
          <label className="block text-[12px] uppercase tracking-[.07em] text-bg-9">
            Primary text
            <textarea
              value={state.goal.ad_text_primary}
              onChange={(event) =>
                patch({
                  type: "patchGoal",
                  value: { ad_text_primary: event.target.value },
                })
              }
              className="mt-1 min-h-28 w-full resize-y rounded-[var(--radius-2)] border border-[var(--color-hairline)] bg-bg-2 p-3 text-[14px] normal-case tracking-normal text-bg-11 focus:border-accent focus:outline-none"
            />
          </label>
        ) : null}
      </div>
    </Card>
  );
}

function StructureStep({
  campaigns,
  error,
  onChange,
}: {
  campaigns: CampaignWizardCampaign[];
  error?: string;
  onChange: (campaigns: CampaignWizardCampaign[]) => void;
}) {
  return (
    <Card eyebrow="ШАГ 4 · СТРУКТУРА" title="Кампании и ad set">
      <div className="space-y-3">
        {campaigns.map((campaign, index) => (
          <div
            key={campaign.key}
            className="rounded-[var(--radius-2)] border border-[var(--color-hairline)] bg-bg-2 p-3"
          >
            <div className="grid gap-3">
              <Input
                label="Ключ"
                value={campaign.key}
                onChange={(event) =>
                  onChange(
                    campaigns.map((item, itemIndex) =>
                      itemIndex === index
                        ? {
                            ...item,
                            key: event.target.value.replace(/[^A-Za-z0-9_-]/g, ""),
                          }
                        : item,
                    ),
                  )
                }
              />
              <Input
                label="Метка"
                value={campaign.label ?? ""}
                onChange={(event) =>
                  onChange(
                    campaigns.map((item, itemIndex) =>
                      itemIndex === index ? { ...item, label: event.target.value || null } : item,
                    ),
                  )
                }
              />
              <div className="flex items-end gap-2">
                <Input
                  className="flex-1"
                  label="Ad set"
                  type="number"
                  min={1}
                  max={100}
                  value={campaign.adset_count}
                  onChange={(event) =>
                    onChange(
                      campaigns.map((item, itemIndex) =>
                        itemIndex === index
                          ? {
                              ...item,
                              adset_count: Math.max(1, Number(event.target.value) || 1),
                            }
                          : item,
                      ),
                    )
                  }
                />
                <Button
                  variant="ghost"
                  aria-label={`Удалить ${campaign.key}`}
                  onClick={() => onChange(campaigns.filter((_, itemIndex) => itemIndex !== index))}
                >
                  <Trash2 size={17} aria-hidden="true" />
                </Button>
              </div>
            </div>
          </div>
        ))}
        {error ? (
          <p role="alert" className="text-[13px] text-danger">
            {error}
          </p>
        ) : null}
        <Button
          variant="secondary"
          fullWidth
          onClick={() =>
            onChange([...campaigns, { key: nextCampaignKey(campaigns), adset_count: 3 }])
          }
        >
          <Plus size={16} aria-hidden="true" />
          Добавить кампанию
        </Button>
      </div>
    </Card>
  );
}

function CreativesStep({
  concepts,
  campaigns,
  error,
  uploading,
  onUpload,
  onChange,
}: {
  concepts: CampaignWizardConcept[];
  campaigns: CampaignWizardCampaign[];
  error?: string;
  uploading: boolean;
  onUpload: (files: File[]) => Promise<void>;
  // Число копий на концепт задавать нечем: раскладка всегда равна числу adset'ов
  // кампании, поле в форме ничего не меняло. Оно показано в превью как следствие.
  onChange: (value: { concepts?: CampaignWizardConcept[] }) => void;
}) {
  return (
    <Card eyebrow="ШАГ 5 · КРЕАТИВЫ" title="Файлы и распределение">
      <label className="flex min-h-24 cursor-pointer flex-col items-center justify-center gap-2 rounded-[var(--radius-2)] border border-dashed border-[var(--color-hairline-strong)] bg-bg-2 px-4 text-center text-[13px] text-bg-9 focus-within:border-accent">
        <FileUp size={22} aria-hidden="true" />
        <span>{uploading ? "Загружаем…" : "Выбрать фото или видео"}</span>
        <input
          type="file"
          multiple
          accept="image/*,video/*"
          className="sr-only"
          disabled={uploading}
          onChange={(event) => void onUpload(Array.from(event.target.files ?? []))}
        />
      </label>
      <div className="mt-4 space-y-3">
        {concepts.map((concept) => (
          <div
            key={concept.ref}
            className="rounded-[var(--radius-2)] border border-[var(--color-hairline)] p-3"
          >
            <p className="break-all text-[13px] font-medium text-bg-11">{concept.original_name}</p>
            <div className="mt-2 grid gap-1">
              {campaigns.map((campaign) => {
                const checked = concept.campaign_keys.includes(campaign.key);
                return (
                  <label
                    key={campaign.key}
                    className="flex min-h-11 items-center gap-3 rounded-[var(--radius-2)] px-2 text-[13px] text-bg-10"
                  >
                    <input
                      type="checkbox"
                      className="size-5"
                      checked={checked}
                      onChange={() =>
                        onChange({
                          concepts: concepts.map((item) =>
                            item.ref !== concept.ref
                              ? item
                              : {
                                  ...item,
                                  campaign_keys: checked
                                    ? item.campaign_keys.filter((key) => key !== campaign.key)
                                    : [...item.campaign_keys, campaign.key],
                                },
                          ),
                        })
                      }
                    />
                    {campaign.label || campaign.key}
                  </label>
                );
              })}
            </div>
          </div>
        ))}
      </div>
      {error ? (
        <p role="alert" className="mt-3 text-[13px] text-danger">
          {error}
        </p>
      ) : null}
    </Card>
  );
}

function PreviewStep({
  plan,
  configReady,
  loading,
  onValidate,
}: {
  plan: ReturnType<typeof useCampaignWizardDraft>["plan"];
  configReady: boolean;
  loading: boolean;
  onValidate: () => void;
}) {
  const autoRequested = useRef(false);
  useEffect(() => {
    if (!autoRequested.current && configReady && !plan) {
      autoRequested.current = true;
      onValidate();
    }
  }, [configReady, onValidate, plan]);

  return (
    <Card eyebrow="ШАГ 6 · ПРЕВЬЮ" title="Неизменяемый план">
      <p className="text-[13px] leading-5 text-bg-9">
        Сервер проверит нейминг, снимок кабинета и точное число объектов. Ничего в Meta на этом шаге
        не создаётся.
      </p>
      <Button
        fullWidth
        className="mt-4"
        loading={loading}
        disabled={!configReady}
        onClick={onValidate}
      >
        {plan ? "Пересчитать план" : "Проверить ещё раз"}
      </Button>
      {plan ? (
        <div className="mt-4 space-y-3" role="status">
          <div className="grid grid-cols-3 divide-x divide-[var(--color-hairline)] border-y border-[var(--color-hairline)] py-3 text-center">
            <Metric label="Кампаний" value={plan.campaign_count} />
            <Metric label="Ad set" value={plan.adset_count} />
            <Metric label="Ads" value={plan.ad_count} />
          </div>
          <div className="rounded-[var(--radius-2)] border border-success/35 bg-success/10 p-3 text-[13px] leading-5 text-bg-10">
            <strong className="flex items-center gap-2 text-success">
              <ShieldCheck size={16} aria-hidden="true" />
              ALL PAUSED
            </strong>
            {plan.timezone_name} · {plan.currency} · старт {plan.start_date}
          </div>
        </div>
      ) : null}
    </Card>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <strong className="block font-numeric text-[20px] text-bg-11">{value}</strong>
      <span className="text-[12px] uppercase text-bg-8">{label}</span>
    </div>
  );
}

function LaunchStep({
  config,
  accountIds,
  draftReady,
  receipt,
  loading,
  onLaunch,
}: {
  config: ReturnType<typeof buildCampaignConfig>;
  accountIds: string[];
  draftReady: boolean;
  receipt: LaunchOut | null;
  loading: boolean;
  onLaunch: () => void;
}) {
  const accounts = receipt?.accounts ?? [];
  const accepted = accounts.filter(
    (account): account is typeof account & { run_id: string } =>
      Boolean(account.run_id),
  );
  const detailQueries = useCampaignRunDetails(
    accepted.map((account) => account.run_id),
  );
  const details = new Map(
    accepted.map((account, index) => [
      account.run_id,
      detailQueries[index]?.data,
    ]),
  );
  const observedStates: CampaignLaunchObservedState[] = accounts.map(
    (account) => {
      if (!account.run_id) return "rejected";
      const detail = details.get(account.run_id);
      if (detail?.task?.state === "unknown") return "unknown";
      return (detail?.status ?? account.status) as CampaignLaunchObservedState;
    },
  );
  const aggregate = aggregateCampaignLaunchState(observedStates);

  if (receipt) {
    const aggregateCopy = {
      working: ["Запуски выполняются", "border-warning/35 text-warning"],
      succeeded: [
        "Все кабинеты подтверждены",
        "border-success/35 text-success",
      ],
      partial: ["Частичный результат", "border-warning/40 text-warning"],
      failed: ["Запуски не подтверждены", "border-danger/35 text-danger"],
      unknown: ["Результат неизвестен", "border-danger/35 text-danger"],
    }[aggregate];
    return (
      <Card
        eyebrow="ПО КАБИНЕТАМ"
        title={aggregateCopy[0]}
        className={aggregateCopy[1]}
      >
        <p role="status" className="text-[13px] leading-5 text-bg-9">
          Зелёный итог появится только после подтверждённого успеха всех
          кабинетов.
        </p>
        <div className="mt-4 space-y-2">
          {accounts.map((account) => {
            const detail = account.run_id ? details.get(account.run_id) : null;
            const state = !account.run_id
              ? "rejected"
              : detail?.task?.state === "unknown"
                ? "unknown"
                : (detail?.status ?? account.status);
            return (
              <div
                key={account.account_id}
                className="border-y border-[var(--color-hairline)] px-1 py-3"
              >
                <div className="flex items-center justify-between gap-3">
                  <strong className="font-numeric text-[13px] text-bg-11">
                    act_{account.account_id}
                  </strong>
                  <span className="text-[12px] text-bg-9">
                    {launchStateLabel(state)}
                  </span>
                </div>
                {account.error ? (
                  <p role="alert" className="mt-1 text-[12px] text-danger">
                    {account.error}
                  </p>
                ) : null}
                {state === "unknown" ? (
                  <p role="alert" className="mt-1 text-[12px] text-danger">
                    Автоповтор запрещён до сверки Meta.
                  </p>
                ) : null}
              </div>
            );
          })}
        </div>
        <Link
          to="/campaigns"
          className="mt-4 flex min-h-11 items-center justify-center rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] px-4 text-[14px] text-bg-11"
        >
          Открыть ход выполнения
        </Link>
      </Card>
    );
  }
  return (
    <Card eyebrow="ШАГ 7 · ПОДТВЕРЖДЕНИЕ" title="Поставить в очередь">
      <dl className="space-y-2 text-[13px]">
        <Fact label="Оффер" value={config.offer_code} />
        <Fact
          label="Кабинеты"
          value={accountIds.map((id) => `act_${id}`).join(", ")}
        />
        <Fact label="Бюджет" value={`$${config.daily_budget} / день`} />
        <Fact label="Кампаний" value={String(config.campaigns.length)} />
        <Fact label="Статус объектов" value="PAUSED" />
      </dl>
      {!draftReady ? (
        <p role="status" className="mt-4 text-[13px] text-warning">
          Ждём сохранения точной версии черновика.
        </p>
      ) : null}
      <Button
        size="lg"
        fullWidth
        className="mt-5"
        loading={loading}
        disabled={!draftReady || accountIds.length === 0}
        onClick={onLaunch}
      >
        <ShieldCheck size={17} aria-hidden="true" />
        Подтвердить и поставить в очередь
      </Button>
    </Card>
  );
}

function launchStateLabel(state: string): string {
  if (state === "queued") return "В очереди";
  if (state === "uniquifying") return "Уникализация";
  if (state === "uploading") return "Загрузка";
  if (state === "creating") return "Создание";
  if (state === "succeeded") return "Готово";
  if (state === "unknown") return "UNKNOWN · сверка";
  if (state === "cancelled") return "Отменено";
  if (state === "rejected") return "Отклонено до очереди";
  return "Ошибка";
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-[var(--color-hairline)] py-2">
      <dt className="text-bg-8">{label}</dt>
      <dd className="m-0 text-right font-medium text-bg-11">{value}</dd>
    </div>
  );
}

function asDays(value: string): 1 | 7 | 28 {
  return value === "7" ? 7 : value === "28" ? 28 : 1;
}
