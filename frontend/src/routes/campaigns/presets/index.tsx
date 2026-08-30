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
import { ArrowLeft, ArrowRight, Layers3, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { CampaignTagPicker } from "@/components/domain/campaigns/CampaignTagPicker";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button, buttonStyles } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { CountryMultiSelect } from "@/components/ui/CountryMultiSelect";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { Input } from "@/components/ui/Input";
import { Modal, ModalFooter } from "@/components/ui/Modal";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { toast } from "@/components/ui/Toast";
import {
  type PresetOut,
  useCreatePreset,
  useDeletePreset,
  usePresets,
  useUpdatePreset,
} from "@/lib/api/campaigns";
import { getWizardFeatureState } from "@/stores/campaignWizard";

export const Route = createFileRoute("/campaigns/presets/")({
  component: CampaignPresetsPage,
});

type EditorState =
  | { kind: "create"; draft: CampaignPresetDraft }
  | { kind: "edit"; preset: PresetOut }
  | null;

function CampaignPresetsPage() {
  const presetsQuery = usePresets();
  const deletePreset = useDeletePreset();
  const [editor, setEditor] = useState<EditorState>(null);
  const [deleting, setDeleting] = useState<PresetOut | null>(null);
  const presets = presetsQuery.data ?? [];
  const dataState = campaignPresetsDataState({
    isPending: presetsQuery.isPending,
    isError: presetsQuery.isError,
    count: presets.length,
  });

  const openCreate = () => {
    const fromWizard = createCampaignPresetDraft(getWizardFeatureState());
    setEditor({
      kind: "create",
      draft:
        fromWizard.countries.length > 0 || fromWizard.daily_budget
          ? fromWizard
          : createCampaignPresetDraft(),
    });
  };

  return (
    <>
      <PageHeader
        title="Пресеты кампаний"
        subtitle="Копируют повторяемые параметры в визард и не меняют уже созданные кампании"
        action={
          <div className="flex flex-wrap gap-2">
            <Link
              to="/campaigns/create"
              className={buttonStyles({ variant: "secondary", size: "sm" })}
            >
              <ArrowLeft size={15} aria-hidden="true" />В визард
            </Link>
            <Button variant="primary" size="sm" onClick={openCreate}>
              <Plus size={15} aria-hidden="true" />
              Создать пресет
            </Button>
          </div>
        }
      />

      <section aria-label="Сохранённые пресеты" data-state={dataState}>
        {dataState === "stale" ? (
          <div className="grid gap-3 sm:grid-cols-2" aria-busy="true">
            <Skeleton className="h-48 w-full" />
            <Skeleton className="h-48 w-full" />
          </div>
        ) : dataState === "unavailable" ? (
          <ErrorState
            title="Пресеты недоступны"
            error={safeApiProblemMessage(
              presetsQuery.error,
              "Список не загружен. Новую кампанию можно заполнить вручную.",
            )}
            onRetry={() => void presetsQuery.refetch()}
          />
        ) : dataState === "empty" ? (
          <EmptyState
            icon={<Layers3 size={34} />}
            title="Пресетов пока нет"
            description="Сохраните гео, аудиторию, бюджет и нейминг, чтобы не заполнять их в каждом заливе."
            action={
              <Button variant="primary" onClick={openCreate}>
                Создать первый пресет
              </Button>
            }
          />
        ) : (
          <div className="grid gap-px overflow-hidden border-y border-[var(--color-hairline)] bg-[var(--color-hairline)] lg:grid-cols-2">
            {presets.map((preset) => (
              <PresetRecord
                key={preset.id}
                preset={preset}
                onEdit={() => setEditor({ kind: "edit", preset })}
                onDelete={() => setDeleting(preset)}
              />
            ))}
          </div>
        )}
      </section>

      {editor ? (
        <PresetEditorModal
          key={editor.kind === "edit" ? editor.preset.id : "create"}
          preset={editor.kind === "edit" ? editor.preset : null}
          initialDraft={
            editor.kind === "edit" ? createCampaignPresetDraft(editor.preset) : editor.draft
          }
          onClose={() => setEditor(null)}
        />
      ) : null}

      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => !open && setDeleting(null)}
        title={`Удалить пресет «${deleting?.name ?? ""}»?`}
        description="Пресет исчезнет из списка. Черновики уже получили копию его значений, а созданные кампании и история запусков не изменятся."
        confirmLabel="Удалить пресет"
        onConfirm={async () => {
          if (!deleting) return;
          await deletePreset.mutateAsync(deleting.id);
          toast.success(`Пресет «${deleting.name}» удалён`);
          setDeleting(null);
        }}
      />
    </>
  );
}

function PresetRecord({
  preset,
  onEdit,
  onDelete,
}: {
  preset: PresetOut;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const incomplete = !preset.daily_budget || preset.countries.length === 0;
  return (
    <article className="bg-bg-0 p-5" data-state={incomplete ? "partial" : "ready"}>
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="font-display text-[12px] uppercase tracking-[0.12em] text-bg-8">
            {incomplete ? "Требует заполнения" : "Готов к применению"}
          </p>
          <h2 className="mt-1 break-words font-display text-[18px] font-medium text-bg-11">
            {preset.name}
          </h2>
        </div>
        <div className="flex shrink-0 gap-1">
          <Button
            size="icon"
            variant="ghost"
            aria-label={`Изменить ${preset.name}`}
            onClick={onEdit}
          >
            <Pencil size={15} aria-hidden="true" />
          </Button>
          <Button
            size="icon"
            variant="ghost-danger"
            aria-label={`Удалить ${preset.name}`}
            onClick={onDelete}
          >
            <Trash2 size={15} aria-hidden="true" />
          </Button>
        </div>
      </div>
      <dl className="mt-5 grid grid-cols-2 gap-x-5 gap-y-3 text-[13px] sm:grid-cols-3">
        <Fact label="Гео" value={preset.countries.join(" · ") || "Не задано"} />
        <Fact label="Возраст" value={`${preset.age_min}–${preset.age_max}`} />
        <Fact
          label="Бюджет"
          value={
            preset.daily_budget
              ? `$${preset.daily_budget} · ${preset.budget_level === "campaign" ? "CBO" : "ABO"}`
              : "Не задан"
          }
        />
        <Fact label="Пол" value={preset.genders.length ? preset.genders.join(" · ") : "Все"} />
        <Fact
          label="Плейсменты"
          value={preset.placements.length ? preset.placements.join(" · ") : "Авто"}
        />
        <Fact label="Оптимизация" value="Purchase · fixed" />
      </dl>
      <div className="mt-5 border-t border-[var(--color-hairline)] pt-3 text-[12px] text-bg-8">
        Нейминг: {preset.naming_template || "стандартный"} · URL tags:{" "}
        {preset.url_tags_template ? "свой шаблон" : "SOP"}
      </div>
      <div className="mt-4 flex justify-end">
        <Link
          to="/campaigns/create"
          search={{ preset: preset.id }}
          className={buttonStyles({ variant: "secondary", size: "sm" })}
        >
          Применить и создать
          <ArrowRight size={14} aria-hidden="true" />
        </Link>
      </div>
    </article>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-bg-8">{label}</dt>
      <dd className="mt-0.5 break-words text-bg-10">{value}</dd>
    </div>
  );
}

function PresetEditorModal({
  preset,
  initialDraft,
  onClose,
}: {
  preset: PresetOut | null;
  initialDraft: CampaignPresetDraft;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(initialDraft);
  const [errors, setErrors] = useState<Partial<Record<keyof CampaignPresetDraft, string>>>({});
  const createPreset = useCreatePreset();
  const updatePreset = useUpdatePreset(preset?.id ?? "");
  const pending = createPreset.isPending || updatePreset.isPending;
  const patch = (value: Partial<CampaignPresetDraft>) =>
    setDraft((current) => ({ ...current, ...value }));

  const submit = async () => {
    const nextErrors = validateCampaignPresetDraft(draft);
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;
    try {
      if (preset) {
        await updatePreset.mutateAsync(campaignPresetPayload(draft));
        toast.success(`Пресет «${draft.name.trim()}» обновлён`);
      } else {
        await createPreset.mutateAsync(campaignPresetPayload(draft));
        toast.success(`Пресет «${draft.name.trim()}» создан`);
      }
      onClose();
    } catch (error) {
      toast.error(safeApiProblemMessage(error, "Пресет не сохранён. Проверьте поля и повторите."));
    }
  };

  return (
    <Modal
      open
      onOpenChange={(open) => !open && onClose()}
      title={preset ? "Изменить пресет" : "Новый пресет"}
      description="Сохраняются только повторяемые параметры. После применения все поля визарда остаются редактируемыми."
      size="lg"
    >
      <div className="space-y-5">
        <Input
          label="Название пресета"
          value={draft.name}
          errorMessage={errors.name}
          placeholder="GH · broad · CBO 200"
          onChange={(event) => patch({ name: event.target.value })}
        />
        <CountryMultiSelect
          label="Гео"
          values={draft.countries}
          errorMessage={errors.countries}
          placeholder="Начните вводить страну"
          onChange={(countries) => patch({ countries })}
        />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Input
            label="Возраст от"
            type="number"
            min={18}
            max={65}
            value={String(draft.age_min)}
            errorMessage={errors.age_min}
            onChange={(event) => patch({ age_min: Number(event.target.value) })}
          />
          <Input
            label="Возраст до"
            type="number"
            min={18}
            max={65}
            value={String(draft.age_max)}
            onChange={(event) => patch({ age_max: Number(event.target.value) })}
          />
        </div>
        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
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
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Select
            label="Тип бюджета"
            value={draft.budget_level}
            options={[
              { value: "campaign", label: "CBO · на кампании" },
              { value: "adset", label: "ABO · на ad set" },
            ]}
            onChange={(event) =>
              patch({ budget_level: event.target.value === "adset" ? "adset" : "campaign" })
            }
          />
          <Input
            label="Дневной бюджет, USD"
            inputMode="decimal"
            value={draft.daily_budget}
            errorMessage={errors.daily_budget}
            placeholder="200.00"
            onChange={(event) => patch({ daily_budget: event.target.value })}
          />
        </div>
        <div className="border-y border-[var(--color-hairline)] py-3 text-[13px] text-bg-10">
          <span className="text-bg-8">Событие оптимизации</span>
          <strong className="ml-3 font-medium text-bg-11">Purchase</strong>
          <span className="ml-2 text-bg-8">зафиксировано правилом проекта</span>
        </div>
        <Input
          label="Шаблон нейминга"
          value={draft.naming_template}
          placeholder="{byer} | {offer} | adset.pro | {date}"
          helpText="Пусто — стандартный шаблон."
          onChange={(event) => patch({ naming_template: event.target.value })}
        />
        <Input
          label="URL Tags"
          value={draft.url_tags_template}
          placeholder="sub2={byer}&sub5={{campaign.name}}"
          helpText="Пусто — SOP-теги; sub8={{ad.id}} сервер гарантирует всегда."
          onChange={(event) => patch({ url_tags_template: event.target.value })}
        />
      </div>
      <ModalFooter>
        <Button variant="ghost" onClick={onClose} disabled={pending}>
          Отмена
        </Button>
        <Button variant="primary" loading={pending} onClick={() => void submit()}>
          {preset ? "Сохранить изменения" : "Создать пресет"}
        </Button>
      </ModalFooter>
    </Modal>
  );
}
