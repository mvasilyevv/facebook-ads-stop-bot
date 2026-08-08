import { useState, type FormEvent } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { ChevronLeft, ChevronRight, Search } from "lucide-react";

import type {
  OperatorAdsQuery,
  OperatorSeverity,
} from "@fb/shared/operator/contracts";
import { confirmedOperatorCurrency } from "@fb/shared/operator/adsViewModel";
import { adsForRealtimeState } from "@fb/shared/operator/viewModel";
import { DataStateBadge, DataStateNotice } from "@fb/operator-ui";
import { useOperatorRealtimeStatus } from "@fb/operator-api";

import { MiniHeader } from "@/components/layout/MiniHeader";
import { Button, EmptyState, ErrorState, Skeleton } from "@/components/ui";
import { MiniOperatorAdCard } from "@/features/operator/OperatorAds";
import { operatorProblemMessage, useOperatorAds } from "@/lib/operatorApi";
import { haptic } from "@/lib/tg";

type AdsSort = NonNullable<OperatorAdsQuery["sort"]>;

export const Route = createFileRoute("/ads/")({ component: AdsPage });

const FILTERS: Array<{ value?: OperatorSeverity; label: string }> = [
  { label: "Все" },
  { value: "critical", label: "Опасность" },
  { value: "warning", label: "Внимание" },
  { value: "ok", label: "Норма" },
  { value: "unknown", label: "Неизвестно" },
];

function AdsPage() {
  const realtimeStatus = useOperatorRealtimeStatus();
  const [draftSearch, setDraftSearch] = useState("");
  const [search, setSearch] = useState("");
  const [severity, setSeverity] = useState<OperatorSeverity | undefined>();
  const [sort, setSort] = useState<AdsSort>("updated");
  const [page, setPage] = useState(1);
  const query = useOperatorAds({
    search: search || undefined,
    severity,
    sort,
    direction: "desc",
    page,
    page_size: 30,
  });
  const payload = query.data;
  const displayPayload = payload
    ? adsForRealtimeState(
        payload,
        realtimeStatus === "connected" && !query.isError,
      )
    : null;
  const displayState = displayPayload?.state;
  const displayRows = displayPayload?.rows;
  const currency = confirmedOperatorCurrency(displayPayload?.scope);
  const confirmedEmpty =
    realtimeStatus === "connected" &&
    !query.isError &&
    displayState === "empty";

  function submit(event: FormEvent) {
    event.preventDefault();
    setPage(1);
    setSearch(draftSearch.trim());
  }

  return (
    <div className="flex flex-col pb-5">
      <MiniHeader
        eyebrowNum="04"
        eyebrow="УПРАВЛЕНИЕ"
        title="Объявления"
        right={
          displayState ? <DataStateBadge state={displayState} compact /> : null
        }
      />

      <section
        aria-label="Фильтры объявлений"
        className="border-b border-[var(--color-hairline)] px-4 py-3"
      >
        <form onSubmit={submit} className="flex gap-2">
          <label className="relative min-w-0 flex-1">
            <span className="sr-only">Поиск объявлений</span>
            <Search
              aria-hidden="true"
              className="absolute left-3 top-1/2 -translate-y-1/2 text-bg-8"
              size={16}
            />
            <input
              type="search"
              value={draftSearch}
              onChange={(event) => setDraftSearch(event.target.value)}
              placeholder="Название, кампания или ID"
              className="min-h-11 w-full rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] bg-bg-1 pl-10 pr-3 text-[16px] text-bg-11 outline-none placeholder:text-bg-8 focus:border-accent"
            />
          </label>
          <Button type="submit" variant="secondary" className="min-h-11">
            Найти
          </Button>
        </form>

        <div
          className="mt-3 flex gap-2 overflow-x-auto pb-1"
          role="group"
          aria-label="Фильтр по риску"
        >
          {FILTERS.map((filter) => (
            <button
              key={filter.label}
              type="button"
              aria-pressed={filter.value === severity}
              onClick={() => {
                haptic.selection();
                setSeverity(filter.value);
                setPage(1);
              }}
              className={`min-h-11 shrink-0 rounded-full border px-4 text-[14px] font-semibold ${
                filter.value === severity
                  ? "border-accent bg-accent-bg text-accent"
                  : "border-[var(--color-hairline-strong)] bg-bg-1 text-bg-9"
              }`}
            >
              {filter.label}
            </button>
          ))}
        </div>

        <label className="mt-3 flex min-h-11 items-center justify-between gap-3 text-[14px] text-bg-9">
          <span>Сортировка</span>
          <select
            value={sort}
            onChange={(event) => {
              setSort(event.target.value as AdsSort);
              setPage(1);
            }}
            className="min-h-11 rounded-[var(--radius-2)] border border-[var(--color-hairline-strong)] bg-bg-1 px-3 text-[14px] text-bg-11"
          >
            <option value="updated">Обновление</option>
            <option value="spend">Расход</option>
            <option value="clicks">Клики</option>
            <option value="registrations">Регистрации</option>
            <option value="ftd">FTD</option>
            <option value="name">Название</option>
          </select>
        </label>
      </section>

      {payload && displayState && displayState !== "ready" ? (
        <div className="px-4 pt-3">
          <DataStateNotice
            state={displayState}
            issues={displayPayload?.issues ?? []}
            compact
          />
        </div>
      ) : null}

      <section className="grid gap-3 px-4 pt-4" aria-label="Объявления">
        {query.isError && !payload ? (
          <ErrorState
            message={operatorProblemMessage(query.error)}
            onRetry={() => void query.refetch()}
          />
        ) : query.isPending && !payload ? (
          <div
            role="status"
            aria-label="Загрузка объявлений"
            className="grid gap-3"
          >
            {Array.from({ length: 5 }, (_, index) => (
              <Skeleton key={index} className="h-40 w-full" />
            ))}
          </div>
        ) : displayRows?.length ? (
          displayRows.map((ad) => (
            <MiniOperatorAdCard key={ad.id} ad={ad} currency={currency} />
          ))
        ) : confirmedEmpty ? (
          <EmptyState
            title="Объявлений не найдено"
            description="Сервер подтвердил пустой результат. Измените фильтр или поиск."
          />
        ) : (
          <EmptyState
            title="Список не подтверждён"
            description="Дождитесь сверки live-снимка. Неподтверждённый результат не считается нулём."
          />
        )}
      </section>

      {displayPayload && displayPayload.pages > 1 ? (
        <nav
          aria-label="Страницы объявлений"
          className="mt-4 flex items-center justify-between gap-2 px-4"
        >
          <Button
            variant="secondary"
            disabled={page <= 1 || query.isFetching}
            onClick={() => setPage((value) => value - 1)}
          >
            <ChevronLeft aria-hidden="true" size={16} /> Назад
          </Button>
          <span className="text-[14px] text-bg-9" aria-live="polite">
            {page} / {displayPayload.pages}
          </span>
          <Button
            variant="secondary"
            disabled={page >= displayPayload.pages || query.isFetching}
            onClick={() => setPage((value) => value + 1)}
          >
            Далее <ChevronRight aria-hidden="true" size={16} />
          </Button>
        </nav>
      ) : null}
    </div>
  );
}
