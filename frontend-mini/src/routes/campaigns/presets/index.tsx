import {
  CAMPAIGN_GENDER_OPTIONS,
  CAMPAIGN_PLACEMENT_OPTIONS,
  campaignPresetPayload,
  campaignPresetsDataState,
  createCampaignPresetDraft,
  validateCampaignPresetDraft,
  type CampaignPresetDraft,
} from "@fb/features/campaigns";
import { safeApiProblemMessage } from "@fb/operator-api";
import { createFileRoute, Link } from "@tanstack/react-router";
import { ArrowLeft, Layers3, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { MiniHeader } from "@/components/layout/MiniHeader";
import { Button, Card, Input, Select, Sheet, Skeleton, TagListInput } from "@/components/ui";
import { CampaignTagPicker } from "@/features/campaigns/CampaignTagPicker";
import { getStoredRole } from "@/lib/auth";
import {
  type CampaignPreset,
  useCampaignPresets,
  useCreateCampaignPreset,
  useDeleteCampaignPreset,
  useUpdateCampaignPreset,
} from "@/lib/campaigns";

export const Route = createFileRoute("/campaigns/presets/")({
  component: CampaignPresetsPage,
});

type EditorState =
  | { kind: "create"; draft: CampaignPresetDraft }
  | { kind: "edit"; preset: CampaignPreset }
  | null;

function CampaignPresetsPage() {
  const owner = getStoredRole() === "owner";
  const presetsQuery = useCampaignPresets();
  const deletePreset = useDeleteCampaignPreset();
  const [editor, setEditor] = useState<EditorState>(null);
  const [deleting, setDeleting] = useState<CampaignPreset | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const presets = presetsQuery.data ?? [];
  const dataState = campaignPresetsDataState({
    isPending: presetsQuery.isPending,
    isError: presetsQuery.isError,
    count: presets.length,
  });

  return (
    <div className="flex min-h-full flex-col pb-24">
      <MiniHeader
        eyebrowNum="05"
        eyebrow="РЕКЛАМА · ШАБЛОНЫ"
        title="Пресеты"
        right={
          <Link
            to="/campaigns/create"
            aria-label="Вернуться в визард"
            className="flex size-11 items-center justify-center rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] text-bg-10 focus-visible:outline-2 focus-visible:outline-accent"
          >
            <ArrowLeft size={18} aria-hidden="true" />
          </Link>
        }
      />

      {!owner ? (
        <div className="px-4 py-6">
          <p
            role="status"
            className="rounded-[var(--radius-2)] border border-warning/35 bg-warning/10 p-4 text-[14px] leading-5 text-bg-10"
          >
            Управление пресетами доступно только owner.
          </p>
        </div>
      ) : (
        <main className="space-y-4 px-4 py-5" data-state={dataState}>
          <div className="flex items-start justify-between gap-3">
            <p className="max-w-[260px] text-[13px] leading-5 text-bg-8">
              Пресет копирует повторяемые параметры в визард. Уже созданные кампании от него не
              зависят.
            </p>
            <Button
              size="sm"
              onClick={() =>
                setEditor({
                  kind: "create",
                  draft: createCampaignPresetDraft(),
                })
              }
            >
              <Plus size={15} aria-hidden="true" />
              Создать
            </Button>
          </div>

          {problem ? (
            <p
              role="alert"
              className="rounded-[var(--radius-2)] border border-danger/35 bg-danger/10 p-3 text-[13px] leading-5 text-danger"
            >
              {problem}
            </p>
          ) : null}

          {dataState === "stale" ? (
            <div className="space-y-3" aria-busy="true">
              <Skeleton className="h-48 w-full" />
              <Skeleton className="h-48 w-full" />
            </div>
          ) : dataState === "unavailable" ? (
            <Card className="border-warning/35">
              <p role="alert" className="text-[14px] leading-5 text-bg-10">
                {safeApiProblemMessage(
                  presetsQuery.error,
                  "Пресеты недоступны. Визард можно заполнить вручную.",
                )}
              </p>
              <Button
                variant="secondary"
                fullWidth
                className="mt-4"
                onClick={() => void presetsQuery.refetch()}
              >
                Повторить загрузку
              </Button>
            </Card>
          ) : dataState === "empty" ? (
            <Card className="py-7 text-center">
              <Layers3 className="mx-auto text-bg-8" size={32} aria-hidden="true" />
              <h2 className="mt-3 text-[16px] font-semibold text-bg-11">Пресетов пока нет</h2>
              <p className="mt-2 text-[13px] leading-5 text-bg-8">
                Сохраните гео, аудиторию, бюджет и нейминг для следующих заливов.
              </p>
              <Button
                fullWidth
                className="mt-5"
                onClick={() =>
                  setEditor({
                    kind: "create",
                    draft: createCampaignPresetDraft(),
                  })
                }
              >
                Создать первый пресет
              </Button>
            </Card>
          ) : (
            <div className="space-y-3">
              {presets.map((preset) => (
                <PresetCard
                  key={preset.id}
                  preset={preset}
                  onEdit={() => setEditor({ kind: "edit", preset })}
                  onDelete={() => setDeleting(preset)}
                />
              ))}
            </div>
          )}
        </main>
      )}

      {editor ? (
        <PresetEditorSheet
          key={editor.kind === "edit" ? editor.preset.id : "create"}
          preset={editor.kind === "edit" ? editor.preset : null}
          initialDraft={
            editor.kind === "edit" ? createCampaignPresetDraft(editor.preset) : editor.draft
          }
          onClose={() => setEditor(null)}
        />
      ) : null}

      <Sheet
        open={deleting !== null}
        onClose={() => setDeleting(null)}
        eyebrow="УДАЛЕНИЕ"
        title={`Удалить «${deleting?.name ?? ""}»?`}
      >
        <div className="pb-2">
          <p className="text-[14px] leading-6 text-bg-9">
            Пресет исчезнет из списка. Уже созданные кампании и история запусков сохранят
            собственную копию параметров.
          </p>
          <div className="mt-5 grid grid-cols-2 gap-3">
            <Button variant="secondary" onClick={() => setDeleting(null)}>
              Отмена
            </Button>
            <Button
              variant="danger"
              loading={deletePreset.isPending}
              onClick={() => {
                if (!deleting) return;
                setProblem(null);
                void deletePreset
                  .mutateAsync(deleting.id)
                  .then(() => setDeleting(null))
                  .catch((error) => {
                    setProblem(safeApiProblemMessage(error, "Пресет не удалён. Повторите."));
                    setDeleting(null);
                  });
              }}
            >
              Удалить
            </Button>
          </div>
        </div>
      </Sheet>
    </div>
  );
}

function PresetCard({
  preset,
  onEdit,
  onDelete,
}: {
  preset: CampaignPreset;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const legacyIncomplete = preset.daily_budget === null;
  return (
    <Card>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="truncate text-[16px] font-semibold text-bg-11">{preset.name}</h2>
          <p className="mt-1 text-[12px] leading-5 text-bg-8">
            {preset.countries.join(" · ")} · {preset.age_min}–{preset.age_max} ·{" "}
            {preset.budget_level === "campaign" ? "CBO" : "ABO"} · $
            {preset.daily_budget ?? "не задан"}
          </p>
        </div>
        {legacyIncomplete ? (
          <span className="rounded-[var(--radius-1)] bg-warning/10 px-2 py-1 text-[12px] text-warning">
            Требует суммы
          </span>
        ) : null}
      </div>
      <dl className="mt-4 grid grid-cols-2 gap-x-3 gap-y-2 text-[12px]">
        <PresetFact label="Пол" value={preset.genders.length ? preset.genders.join(", ") : "Все"} />
        <PresetFact
          label="Плейсменты"
          value={preset.placements.length ? preset.placements.join(", ") : "Авто"}
        />
        <PresetFact label="Оптимизация" value="Purchase" />
        <PresetFact label="URL tags" value={preset.url_tags_template || "Не заданы"} />
      </dl>
      <div className="mt-4 grid grid-cols-2 gap-3">
        <Button variant="secondary" onClick={onEdit}>
          <Pencil size={15} aria-hidden="true" />
          Изменить
        </Button>
        <Button variant="danger" onClick={onDelete}>
          <Trash2 size={15} aria-hidden="true" />
          Удалить
        </Button>
      </div>
    </Card>
  );
}

function PresetFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-bg-8">{label}</dt>
      <dd className="mt-0.5 truncate text-bg-10" title={value}>
        {value}
      </dd>
    </div>
  );
}

function PresetEditorSheet({
  preset,
  initialDraft,
  onClose,
}: {
  preset: CampaignPreset | null;
  initialDraft: CampaignPresetDraft;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(initialDraft);
  const [problem, setProblem] = useState<string | null>(null);
  const [errors, setErrors] = useState<Partial<Record<keyof CampaignPresetDraft, string>>>({});
  const createPreset = useCreateCampaignPreset();
  const updatePreset = useUpdateCampaignPreset(preset?.id ?? "");
  const pending = createPreset.isPending || updatePreset.isPending;

  const patch = (value: Partial<CampaignPresetDraft>) =>
    setDraft((current) => ({ ...current, ...value }));

  const submit = async () => {
    const nextErrors = validateCampaignPresetDraft(draft);
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;
    setProblem(null);
    try {
      const payload = campaignPresetPayload(draft);
      if (preset) await updatePreset.mutateAsync(payload);
      else await createPreset.mutateAsync(payload);
      onClose();
    } catch (error) {
      setProblem(safeApiProblemMessage(error, "Пресет не сохранён. Проверьте поля и повторите."));
    }
  };

  return (
    <Sheet
      open
      onClose={onClose}
      eyebrow={preset ? "РЕДАКТИРОВАНИЕ" : "НОВЫЙ ПРЕСЕТ"}
      title={preset ? preset.name : "Параметры пресета"}
    >
      <form
        className="space-y-4 pb-2"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        {problem ? (
          <p
            role="alert"
            className="rounded-[var(--radius-2)] border border-danger/35 bg-danger/10 p-3 text-[13px] leading-5 text-danger"
          >
            {problem}
          </p>
        ) : null}
        <Input
          label="Название"
          value={draft.name}
          errorMessage={errors.name}
          placeholder="US · CBO · 25+"
          onChange={(event) => patch({ name: event.target.value })}
        />
        <TagListInput
          label="Страны, ISO-2"
          values={draft.countries}
          errorMessage={errors.countries}
          placeholder="Введите US и нажмите Enter"
          normalize={(value) => value.toUpperCase()}
          validate={(value) =>
            /^[A-Z]{2}$/.test(value) ? null : `Некорректный ISO-2 код: ${value}`
          }
          onChange={(countries) => patch({ countries })}
        />
        <div className="grid grid-cols-2 gap-3">
          <Input
            label="Возраст от"
            type="number"
            min={18}
            max={65}
            value={draft.age_min}
            errorMessage={errors.age_min}
            onChange={(event) => patch({ age_min: Number(event.target.value) })}
          />
          <Input
            label="Возраст до"
            type="number"
            min={18}
            max={65}
            value={draft.age_max}
            onChange={(event) => patch({ age_max: Number(event.target.value) })}
          />
        </div>
        <CampaignTagPicker
          label="Пол"
          values={draft.genders}
          options={CAMPAIGN_GENDER_OPTIONS}
          emptyLabel="Все полы"
          onChange={(genders) => patch({ genders })}
        />
        <CampaignTagPicker
          label="Плейсменты"
          values={draft.placements}
          options={CAMPAIGN_PLACEMENT_OPTIONS}
          emptyLabel="Автоматические плейсменты Meta"
          onChange={(placements) => patch({ placements })}
        />
        <Select
          label="Тип бюджета"
          value={draft.budget_level}
          options={[
            { value: "campaign", label: "CBO · на кампании" },
            { value: "adset", label: "ABO · на ad set" },
          ]}
          onChange={(event) =>
            patch({
              budget_level: event.target.value === "adset" ? "adset" : "campaign",
            })
          }
        />
        <Input
          label="Дневной бюджет, USD"
          inputMode="decimal"
          value={draft.daily_budget}
          errorMessage={errors.daily_budget}
          onChange={(event) => patch({ daily_budget: event.target.value })}
        />
        <div className="rounded-[var(--radius-2)] border border-[var(--color-hairline)] bg-bg-2 p-3">
          <p className="text-[12px] uppercase tracking-[0.08em] text-bg-9">
            Событие оптимизации пикселя
          </p>
          <strong className="mt-1 block text-[14px] text-bg-11">Purchase</strong>
          <p className="mt-1 text-[12px] leading-5 text-bg-8">
            Зафиксировано правилом проекта и не редактируется.
          </p>
        </div>
        <Input
          label="Шаблон нейминга"
          value={draft.naming_template}
          placeholder="{date} | {country} | {byer}"
          onChange={(event) => patch({ naming_template: event.target.value })}
        />
        <Input
          label="URL tags"
          value={draft.url_tags_template}
          placeholder="utm_source=facebook&utm_campaign={campaign_key}"
          onChange={(event) => patch({ url_tags_template: event.target.value })}
        />
        <div className="sticky bottom-0 grid grid-cols-2 gap-3 border-t border-[var(--color-hairline)] bg-bg-1 py-3">
          <Button type="button" variant="secondary" onClick={onClose}>
            Отмена
          </Button>
          <Button type="submit" loading={pending}>
            {preset ? "Сохранить" : "Создать"}
          </Button>
        </div>
      </form>
    </Sheet>
  );
}
