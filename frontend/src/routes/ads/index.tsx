import { useState, type FormEvent } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { ChevronLeft, ChevronRight, Search } from "lucide-react";

import type { OperatorAdsQuery, OperatorSeverity } from "@fb/shared/operator/contracts";
import { confirmedOperatorCurrency } from "@fb/shared/operator/adsViewModel";
import { adsForRealtimeState } from "@fb/shared/operator/viewModel";
import { DataStateBadge, DataStateNotice } from "@fb/operator-ui";
import { useOperatorRealtimeStatus } from "@fb/operator-api";

import { Eyebrow } from "@/components/data/Eyebrow";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { Skeleton } from "@/components/ui/Skeleton";
import { OperatorAdCards, OperatorAdsTable } from "@/features/operator/OperatorAds";
import { operatorProblemMessage, useOperatorAds } from "@/lib/api/operator";

type AdsSort = NonNullable<OperatorAdsQuery["sort"]>;
type AdsDirection = NonNullable<OperatorAdsQuery["direction"]>;

interface AdsSearch {
  q?: string;
  severity?: OperatorSeverity;
  sort?: AdsSort;
  direction?: AdsDirection;
  page?: number;
}

const SEVERITIES: Array<{ value: OperatorSeverity | ""; label: string }> = [
  { value: "", label: "Все состояния" },
  { value: "critical", label: "Опасность" },
  { value: "warning", label: "Внимание" },
  { value: "ok", label: "Норма" },
  { value: "unknown", label: "Неизвестно" },
];

const SORTS: Array<{ value: AdsSort; label: string }> = [
  { value: "updated", label: "Обновление" },
  { value: "spend", label: "Расход" },
  { value: "clicks", label: "Клики" },
  { value: "registrations", label: "Регистрации" },
  { value: "ftd", label: "FTD" },
  { value: "name", label: "Название" },
];

export const Route = createFileRoute("/ads/")({
  component: AdsPage,
  validateSearch: (raw: Record<string, unknown>): AdsSearch => ({
    q: typeof raw.q === "string" && raw.q.trim() ? raw.q.slice(0, 200) : undefined,
    severity: isSeverity(raw.severity) ? raw.severity : undefined,
    sort: isSort(raw.sort) ? raw.sort : undefined,
    direction: raw.direction === "asc" || raw.direction === "desc" ? raw.direction : undefined,
    page: positiveInt(raw.page),
  }),
});

function AdsPage() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: "/ads/" });
  const realtimeStatus = useOperatorRealtimeStatus();
  const [draftSearch, setDraftSearch] = useState(search.q ?? "");
  const page = search.page ?? 1;
  const query = useOperatorAds({
    search: search.q,
    severity: search.severity,
    sort: search.sort ?? "updated",
    direction: search.direction ?? "desc",
    page,
    page_size: 50,
  });
  const payload = query.data;
  const displayPayload = payload
    ? adsForRealtimeState(payload, realtimeStatus === "connected" && !query.isError)
    : null;
  const displayState = displayPayload?.state;
  const displayRows = displayPayload?.rows;
  const currency = confirmedOperatorCurrency(displayPayload?.scope);
  const hasConfirmedCount = displayState === "ready" || displayState === "empty";

  function patchSearch(next: Partial<AdsSearch>) {
    void navigate({ search: (previous) => ({ ...previous, ...next }), replace: true });
  }

  function submitSearch(event: FormEvent) {
    event.preventDefault();
    patchSearch({ q: draftSearch.trim() || undefined, page: undefined });
  }

  if (query.isError && !payload) {
    return (
      <ErrorState
        title="Объявления недоступны"
        error={operatorProblemMessage(query.error)}
        onRetry={() => void query.refetch()}
      />
    );
  }

  return (
    <div className="mx-auto max-w-[1440px]">
      <header className="mb-5 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <Eyebrow num="04">УПРАВЛЕНИЕ · ОБЪЯВЛЕНИЯ</Eyebrow>
          <h1 className="m-0 mt-2 font-display text-[clamp(30px,4vw,44px)] font-medium text-bg-11">
            Объявления
          </h1>
          <p className="mt-2 max-w-2xl text-[16px] text-bg-9">
            Серверные поиск, фильтрация и сортировка. Любая команда получает отдельный lifecycle.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {displayState ? <DataStateBadge state={displayState} /> : null}
          <span className="font-numeric text-[14px] text-bg-9">
            {payload && hasConfirmedCount
              ? `${payload.total.toLocaleString("ru-RU")} строк`
              : payload
                ? "— строк"
                : "Загрузка…"}
          </span>
        </div>
      </header>

      <section
        aria-label="Фильтры объявлений"
        className="mb-4 rounded-[var(--radius-3)] border border-[var(--color-hairline)] bg-bg-1 p-4"
      >
        <form
          onSubmit={submitSearch}
          className="grid gap-3 lg:grid-cols-[minmax(260px,1fr)_190px_190px_150px_auto]"
        >
          <label className="relative block">
            <span className="sr-only">Поиск по объявлениям</span>
            <Search
              aria-hidden="true"
              className="absolute left-3 top-1/2 -translate-y-1/2 text-bg-8"
              size={16}
            />
            <input
              value={draftSearch}
              onChange={(event) => setDraftSearch(event.target.value)}
              placeholder="Название, кампания или ID"
              className="min-h-11 w-full rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] bg-bg-0 pl-10 pr-3 text-[16px] text-bg-11 outline-none placeholder:text-bg-8 focus:border-accent focus:ring-1 focus:ring-accent"
            />
          </label>
          <Select
            label="Риск"
            value={search.severity ?? ""}
            onChange={(value) =>
              patchSearch({
                severity: (value || undefined) as OperatorSeverity | undefined,
                page: undefined,
              })
            }
          >
            {SEVERITIES.map((item) => (
              <option key={item.value || "all"} value={item.value}>
                {item.label}
              </option>
            ))}
          </Select>
          <Select
            label="Сортировка"
            value={search.sort ?? "updated"}
            onChange={(value) => patchSearch({ sort: value as AdsSort, page: undefined })}
          >
            {SORTS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </Select>
          <Select
            label="Направление"
            value={search.direction ?? "desc"}
            onChange={(value) => patchSearch({ direction: value as AdsDirection, page: undefined })}
          >
            <option value="desc">По убыванию</option>
            <option value="asc">По возрастанию</option>
          </Select>
          <Button type="submit" variant="primary" className="min-h-11">
            Найти
          </Button>
        </form>
      </section>

      {displayPayload && displayState && displayState !== "ready" ? (
        <div className="mb-4">
          <DataStateNotice state={displayState} issues={displayPayload.issues} />
        </div>
      ) : null}

      <section className="rounded-[var(--radius-3)] border border-[var(--color-hairline)] bg-bg-1 p-3 sm:p-4">
        {query.isPending && !payload ? (
          <div role="status" aria-label="Загрузка объявлений" className="grid gap-3">
            {Array.from({ length: 6 }, (_, index) => (
              <Skeleton key={index} className="h-20 w-full" />
            ))}
          </div>
        ) : displayRows?.length ? (
          <>
            <OperatorAdsTable rows={displayRows} currency={currency} />
            <OperatorAdCards rows={displayRows} currency={currency} />
          </>
        ) : displayState === "empty" ? (
          <EmptyState
            title="Объявлений не найдено"
            description="Сервер подтвердил пустой результат. Измените фильтр или поисковый запрос."
          />
        ) : (
          <EmptyState
            title="Список не подтверждён"
            description="Дождитесь сверки live-снимка или обновите данные. Неподтверждённый результат не считается нулём."
          />
        )}
      </section>

      {displayPayload && displayPayload.pages > 1 ? (
        <nav
          aria-label="Страницы объявлений"
          className="mt-4 flex items-center justify-between gap-3"
        >
          <Button
            variant="secondary"
            className="min-h-11"
            leftIcon={<ChevronLeft aria-hidden="true" />}
            disabled={page <= 1 || query.isFetching}
            onClick={() => patchSearch({ page: page - 1 })}
          >
            Назад
          </Button>
          <span className="text-[14px] text-bg-9" aria-live="polite">
            Страница {page} из {displayPayload.pages}
          </span>
          <Button
            variant="secondary"
            className="min-h-11"
            rightIcon={<ChevronRight aria-hidden="true" />}
            disabled={page >= displayPayload.pages || query.isFetching}
            onClick={() => patchSearch({ page: page + 1 })}
          >
            Далее
          </Button>
        </nav>
      ) : null}
    </div>
  );
}

function Select({
  label,
  value,
  onChange,
  children,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  children: React.ReactNode;
}) {
  return (
    <label>
      <span className="sr-only">{label}</span>
      <select
        aria-label={label}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="min-h-11 w-full rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] bg-bg-0 px-3 text-[14px] text-bg-11 outline-none focus:border-accent focus:ring-1 focus:ring-accent"
      >
        {children}
      </select>
    </label>
  );
}

function isSeverity(value: unknown): value is OperatorSeverity {
  return value === "ok" || value === "warning" || value === "critical" || value === "unknown";
}

function isSort(value: unknown): value is AdsSort {
  return (
    value === "name" ||
    value === "spend" ||
    value === "clicks" ||
    value === "registrations" ||
    value === "ftd" ||
    value === "updated"
  );
}

function positiveInt(value: unknown): number | undefined {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : undefined;
}
