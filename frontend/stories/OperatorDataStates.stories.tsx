import type { Meta, StoryObj } from "@storybook/react-vite";

import { AccessibleChartFrame, DataStateBadge, DataStateNotice } from "@fb/operator-ui";
import type { DataState } from "@fb/shared";

const states: DataState[] = ["ready", "empty", "partial", "stale", "unavailable"];

function OperatorDataStates() {
  return (
    <div className="mx-auto max-w-3xl space-y-5 bg-bg-0 p-8 text-bg-11">
      <div>
        <div className="font-display text-[12px] uppercase tracking-[.08em] text-bg-8">
          Operator UI primitive
        </div>
        <h1 className="mt-2 font-display text-[28px]">Состояния данных</h1>
        <p className="mt-2 text-[16px] text-bg-9">
          Empty, partial, stale и unavailable визуально и семантически отличаются от ready. Partial
          — amber degraded; stale и unavailable остаются нейтрально-серыми, пока отдельная severity
          не подтверждает active danger.
        </p>
      </div>
      <div className="flex flex-wrap gap-3">
        {states.map((state) => (
          <DataStateBadge key={state} state={state} />
        ))}
      </div>
      <div className="space-y-3">
        {states
          .filter((state) => state !== "ready")
          .map((state) => (
            <DataStateNotice key={state} state={state as Exclude<DataState, "ready">} />
          ))}
      </div>
    </div>
  );
}

const meta = {
  title: "Operator/Data states",
  component: OperatorDataStates,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof OperatorDataStates>;

export default meta;
type Story = StoryObj<typeof meta>;

export const AllStates: Story = {};

export const KnownZeroAndUnknown: Story = {
  render: () => (
    <div className="mx-auto grid max-w-2xl gap-4 bg-bg-0 p-8 text-bg-11 sm:grid-cols-2">
      <article className="rounded-[var(--radius-3)] border border-[var(--color-hairline)] bg-bg-1 p-5">
        <DataStateBadge state="ready" />
        <div className="mt-4 text-[14px] text-bg-9">Подтверждённые FTD</div>
        <div className="mt-1 font-display text-[32px] tabular-nums">0</div>
        <p className="mt-2 text-[14px] text-bg-9">Источник подтвердил нулевое значение.</p>
      </article>
      <article className="rounded-[var(--radius-3)] border border-[var(--color-hairline)] bg-bg-1 p-5">
        <DataStateBadge state="unavailable" />
        <div className="mt-4 text-[14px] text-bg-9">FTD</div>
        <div className="mt-1 font-display text-[32px] tabular-nums" aria-label="Не подтверждено">
          —
        </div>
        <p className="mt-2 text-[14px] text-bg-9">Ноль не подставляется вместо неизвестного.</p>
      </article>
    </div>
  ),
};

export const LongNamesAndAmounts: Story = {
  render: () => (
    <div className="mx-auto max-w-xl bg-bg-0 p-8 text-bg-11">
      <article className="min-w-0 rounded-[var(--radius-3)] border border-[var(--color-hairline)] bg-bg-1 p-5">
        <DataStateBadge state="partial" />
        <h2 className="mt-4 break-words font-display text-[22px]">
          act_987654321 · Extremely long cabinet name for stress testing operator layouts
        </h2>
        <div className="mt-5 font-display text-[clamp(24px,7vw,42px)] tabular-nums">
          $9 999 999.99
        </div>
        <p className="mt-2 text-[14px] text-bg-9">
          Длинное имя и крупная сумма не создают horizontal scroll.
        </p>
      </article>
    </div>
  ),
};

export const ChartGapAndDataTable: Story = {
  render: () => (
    <div className="mx-auto max-w-3xl bg-bg-0 p-8 text-bg-11">
      <AccessibleChartFrame
        title="Расход с пропуском"
        summary="В 09:00 факт не подтверждён, поэтому линия разорвана и значение не заменено нулём."
        timezone="Europe/Kaliningrad"
        asOf="2026-07-18T10:15:00Z"
        sources={["meta"]}
        completeness="partial"
        chart={
          <svg
            viewBox="0 0 600 180"
            className="h-auto w-full"
            aria-label="Расход: разрыв между 08:00 и 10:00"
          >
            <path d="M20 150 L180 100" fill="none" stroke="var(--color-accent)" strokeWidth="4" />
            <path d="M420 72 L580 28" fill="none" stroke="var(--color-accent)" strokeWidth="4" />
          </svg>
        }
        table={
          <table>
            <caption className="sr-only">Расход с пропуском по времени</caption>
            <thead>
              <tr>
                <th>Время</th>
                <th>Факт</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <th scope="row">08:00</th>
                <td>$4.20</td>
              </tr>
              <tr>
                <th scope="row">09:00</th>
                <td>—</td>
              </tr>
              <tr>
                <th scope="row">10:00</th>
                <td>$18.40</td>
              </tr>
            </tbody>
          </table>
        }
      />
    </div>
  ),
};

export const ReconnectAndReconcile: Story = {
  render: () => (
    <div className="mx-auto max-w-xl bg-bg-0 p-8 text-bg-11">
      <div role="status" className="rounded-[var(--radius-3)] border border-warning/40 bg-bg-1 p-5">
        <div className="flex items-center justify-between gap-3">
          <h2 className="m-0 font-display text-[20px]">Восстановление live-связи</h2>
          <DataStateBadge state="stale" />
        </div>
        <p className="mt-3 text-[14px] text-bg-9">
          Обнаружен разрыв sequence. Выполняется одна сверка snapshot revision; старые значения не
          помечаются актуальными.
        </p>
      </div>
    </div>
  ),
};

export const UnknownCabinetTimezone: Story = {
  render: () => (
    <div className="mx-auto max-w-xl bg-bg-0 p-8 text-bg-11">
      <DataStateNotice
        state="partial"
        issues={[
          {
            code: "cabinet_timezone_unknown",
            title: "Часовой пояс кабинета неизвестен",
            detail: "Границы суток оценочные; UTC не считается подтверждённой зоной кабинета.",
            severity: "unknown",
            correlation_id: null,
          },
        ]}
      />
    </div>
  ),
};

export const TelegramSafeAreas: Story = {
  render: () => (
    <div className="mx-auto max-w-[430px] bg-bg-4 p-5 text-bg-11">
      <div className="rounded-[32px] border border-[var(--color-hairline-strong)] bg-bg-0 p-[24px_18px_32px_28px]">
        <div className="rounded-[var(--radius-3)] border border-dashed border-accent/60 bg-bg-1 p-4">
          <div className="font-display text-[12px] uppercase tracking-[.08em] text-accent">
            contentSafeAreaInset
          </div>
          <h2 className="mt-3 font-display text-[24px]">Действие остаётся внутри safe area</h2>
          <button
            type="button"
            className="mt-5 min-h-11 w-full rounded-[var(--radius-2)] bg-accent px-4 text-[14px] font-semibold text-bg-0"
          >
            Открыть инцидент
          </button>
        </div>
      </div>
    </div>
  ),
};
