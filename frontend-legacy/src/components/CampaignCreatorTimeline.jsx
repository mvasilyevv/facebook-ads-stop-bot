import { useEffect, useMemo, useRef, useState } from 'react';

const STEP_LABELS = {
  create_campaign: 'Кампания',
  set_budget: 'Бюджет',
  click_next: 'Перейти к адсету',
  set_attribution: 'Атрибуция',
  create_adset: 'Адсеты',
  set_conversion_location: 'Место получения конверсий',
  set_pixel_event: 'Пиксель и событие',
  set_geo: 'География',
  click_next_to_ad: 'Перейти к объявлению',
  upload_creatives: 'Загрузка креативов',
  fill_texts: 'Тексты',
  set_cta: 'CTA',
  set_tracking_url: 'Трекинговая ссылка',
  save_draft: 'Сохранение черновика',
};

function deriveStepState(index, currentIdx, taskStatus) {
  if (currentIdx < 0) {
    return taskStatus === 'SUCCEEDED' ? 'done' : 'pending';
  }
  if (taskStatus === 'SUCCEEDED') return 'done';
  if (index < currentIdx) return 'done';
  if (index === currentIdx) {
    if (taskStatus === 'FAILED') return 'failed';
    if (taskStatus === 'RUNNING') return 'running';
    if (taskStatus === 'SUCCEEDED') return 'done';
    return 'current';
  }
  return 'pending';
}

const RING_BY_STATE = {
  pending: 'border-border bg-surface text-muted',
  current: 'border-accent bg-accent-muted text-accent',
  running: 'border-accent bg-accent text-white shadow-[0_0_0_4px_rgba(99,102,241,0.18)]',
  done: 'border-success bg-success/15 text-success',
  failed: 'border-danger bg-danger/15 text-danger',
};

const ROW_BY_STATE = {
  pending: 'opacity-70 hover:opacity-100',
  current: 'opacity-100',
  running: 'opacity-100',
  done: 'opacity-90',
  failed: 'opacity-100',
};

const CONNECTOR_BY_STATE = {
  pending: 'bg-border',
  current: 'bg-gradient-to-b from-accent to-border',
  running: 'bg-gradient-to-b from-accent to-border',
  done: 'bg-success/60',
  failed: 'bg-gradient-to-b from-danger to-border',
};

export default function CampaignCreatorTimeline({
  steps,
  task,
  busy,
  onRunStep,
  onRunFromStep,
  onResume,
  onCancel,
}) {
  const [openIdx, setOpenIdx] = useState(null);
  const [copiedAt, setCopiedAt] = useState(0);
  const containerRef = useRef(null);

  const currentIdx = useMemo(() => {
    if (!task?.current_step) return -1;
    return steps.findIndex((s) => s.name === task.current_step);
  }, [steps, task?.current_step]);

  const taskStatus = task?.status;
  const isRunning = taskStatus === 'RUNNING';

  useEffect(() => {
    if (openIdx === null) return;
    const onClick = (e) => {
      if (!containerRef.current?.contains(e.target)) setOpenIdx(null);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') setOpenIdx(null);
    };
    document.addEventListener('mousedown', onClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [openIdx]);

  useEffect(() => {
    if (isRunning) setOpenIdx(null);
  }, [isRunning]);

  if (!steps?.length) return null;

  const total = steps.length;
  const doneCount = currentIdx >= 0
    ? (taskStatus === 'SUCCEEDED' ? total : Math.max(0, currentIdx))
    : (taskStatus === 'SUCCEEDED' ? total : 0);
  const progressPct = Math.round((doneCount / total) * 100);

  return (
    <div ref={containerRef} className="flex flex-col gap-md">
      <TimelineHeader
        task={task}
        progressPct={progressPct}
        doneCount={doneCount}
        total={total}
        onResume={onResume}
        onCancel={onCancel}
        busy={busy}
      />

      <ol className="relative flex flex-col">
        {steps.map((step, idx) => {
          const state = deriveStepState(idx, currentIdx, taskStatus);
          const isLast = idx === steps.length - 1;
          const isOpen = openIdx === idx;
          const label = STEP_LABELS[step.name] || step.name;

          const canInteract = !isRunning && !busy;

          return (
            <li key={step.name} className={`group relative flex gap-3 pb-1 ${ROW_BY_STATE[state]}`}>
              <div className="relative flex w-8 shrink-0 flex-col items-center">
                <div
                  className={`relative z-10 flex h-7 w-7 items-center justify-center rounded-full border-2 font-mono text-[11px] font-semibold transition-all ${RING_BY_STATE[state]}`}
                >
                  {state === 'running' ? (
                    <SpinnerIcon />
                  ) : state === 'done' ? (
                    <CheckIcon />
                  ) : state === 'failed' ? (
                    <BangIcon />
                  ) : (
                    String(idx + 1).padStart(2, '0').slice(-2)
                  )}
                  {state === 'running' && (
                    <span className="absolute inset-0 -z-0 animate-ping rounded-full bg-accent/40" />
                  )}
                </div>
                {!isLast && (
                  <span
                    aria-hidden
                    className={`mt-0.5 w-px flex-1 ${CONNECTOR_BY_STATE[state]}`}
                  />
                )}
              </div>

              <div className="flex min-w-0 flex-1 pb-3">
                <button
                  type="button"
                  disabled={!canInteract}
                  onClick={() => setOpenIdx(isOpen ? null : idx)}
                  className={`group/row w-full rounded-md border px-3 py-2 text-left transition-all ${
                    isOpen
                      ? 'border-accent bg-accent-muted'
                      : state === 'current'
                        ? 'border-accent/40 bg-surface'
                        : state === 'failed'
                          ? 'border-danger/40 bg-danger/5'
                          : state === 'running'
                            ? 'border-accent/30 bg-surface'
                            : 'border-transparent bg-surface/40 hover:border-border hover:bg-surface'
                  } ${canInteract ? 'cursor-pointer' : 'cursor-default'}`}
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex min-w-0 items-center gap-2">
                      <span className="truncate text-sm text-primary">{label}</span>
                      {step.idempotent && (
                        <span
                          className="shrink-0 rounded-sm border border-success/30 px-1.5 py-px font-mono text-[9px] uppercase tracking-wider text-success"
                          title="Шаг идемпотентен — безопасно перезапускать"
                        >
                          idem
                        </span>
                      )}
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <StateLabel state={state} />
                      {canInteract && (
                        <ChevronIcon className={`text-muted transition-transform ${isOpen ? 'rotate-180' : ''}`} />
                      )}
                    </div>
                  </div>

                  <div className="mt-0.5 truncate font-mono text-[10px] text-muted">{step.name}</div>

                  {state === 'failed' && task?.error_message && (
                    <button
                      type="button"
                      onClick={async (e) => {
                        e.stopPropagation();
                        try {
                          await navigator.clipboard.writeText(task.error_message);
                          setCopiedAt(Date.now());
                        } catch {
                          // тихо игнорируем
                        }
                      }}
                      className="group/err mt-2 flex w-full items-start gap-2 rounded-sm border border-danger/30 bg-danger/10 px-2 py-1.5 text-left font-mono text-[11px] leading-snug text-danger transition-colors hover:bg-danger/20"
                      title="Кликните, чтобы скопировать"
                    >
                      <span className="flex-1 break-words">{task.error_message}</span>
                      <span className="shrink-0 self-start font-sans text-[9px] uppercase tracking-wider opacity-60 group-hover/err:opacity-100">
                        {copiedAt && Date.now() - copiedAt < 1500 ? '✓ скопировано' : 'copy'}
                      </span>
                    </button>
                  )}

                  {isOpen && (
                    <div className="mt-3 flex flex-wrap gap-2 border-t border-border pt-3">
                      {state === 'failed' && (
                        <ActionButton
                          tone="danger"
                          onClick={(e) => {
                            e.stopPropagation();
                            onResume();
                            setOpenIdx(null);
                          }}
                          disabled={busy}
                        >
                          <RetryIcon /> Повторить с этого шага
                        </ActionButton>
                      )}
                      <ActionButton
                        tone={state === 'failed' ? 'ghost' : 'primary'}
                        onClick={(e) => {
                          e.stopPropagation();
                          onRunStep(step.name);
                          setOpenIdx(null);
                        }}
                        disabled={busy}
                      >
                        <PlayIcon /> Только этот шаг
                      </ActionButton>
                      <ActionButton
                        tone="ghost"
                        onClick={(e) => {
                          e.stopPropagation();
                          onRunFromStep(step.name);
                          setOpenIdx(null);
                        }}
                        disabled={busy}
                      >
                        <FastForwardIcon /> С этого шага до конца
                      </ActionButton>
                    </div>
                  )}
                </button>
              </div>
            </li>
          );
        })}
      </ol>

      {isRunning && (
        <p className="text-center text-[11px] text-muted">
          Пайплайн выполняется. Дождитесь завершения или ошибки, чтобы взаимодействовать с шагами.
        </p>
      )}
    </div>
  );
}

function TimelineHeader({ task, progressPct, doneCount, total, onResume, onCancel, busy }) {
  const status = task?.status;
  const headerTone =
    status === 'SUCCEEDED' ? 'text-success' :
    status === 'FAILED' ? 'text-danger' :
    status === 'RUNNING' ? 'text-accent' : 'text-secondary';

  return (
    <div className="flex flex-col gap-3 rounded-md border border-border bg-surface px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-col">
          <div className="text-[10px] uppercase tracking-[0.18em] text-muted">Пайплайн</div>
          <div className="mt-0.5 truncate text-sm text-primary">
            {task?.campaign_name || '—'}
          </div>
        </div>
        <div className={`flex items-center gap-2 font-mono text-xs ${headerTone}`}>
          {status === 'RUNNING' && <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-accent" />}
          {status === 'FAILED' && <span className="inline-block h-2 w-2 rounded-full bg-danger" />}
          {status === 'SUCCEEDED' && <span className="inline-block h-2 w-2 rounded-full bg-success" />}
          <span className="uppercase tracking-wider">{status || '—'}</span>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <div className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-elevated">
          <div
            className={`h-full transition-all duration-500 ${
              status === 'FAILED' ? 'bg-danger' :
              status === 'SUCCEEDED' ? 'bg-success' : 'bg-accent'
            }`}
            style={{ width: `${progressPct}%` }}
          />
        </div>
        <div className="font-mono text-[11px] tabular-nums text-muted">
          <span className="text-primary">{doneCount}</span>
          <span className="mx-0.5">/</span>
          <span>{total}</span>
        </div>
      </div>

      {status === 'FAILED' && task?.current_step && (
        <button
          type="button"
          disabled={busy}
          onClick={onResume}
          className="group flex items-center justify-center gap-2 self-start rounded-md border border-danger/40 bg-danger/10 px-3 py-1.5 text-xs font-medium text-danger hover:bg-danger/20 disabled:opacity-50"
        >
          <RetryIcon />
          Возобновить с «{STEP_LABELS[task.current_step] || task.current_step}»
        </button>
      )}

      {status === 'RUNNING' && onCancel && (
        <button
          type="button"
          disabled={busy}
          onClick={onCancel}
          className="group flex items-center justify-center gap-2 self-start rounded-md border border-danger/40 bg-danger/10 px-3 py-1.5 text-xs font-medium text-danger hover:bg-danger/20 disabled:opacity-50"
        >
          <StopIcon />
          Остановить выполнение
        </button>
      )}
    </div>
  );
}

function StateLabel({ state }) {
  if (state === 'running') return <span className="font-mono text-[10px] uppercase tracking-wider text-accent">идёт</span>;
  if (state === 'current') return <span className="font-mono text-[10px] uppercase tracking-wider text-accent">след.</span>;
  if (state === 'done') return <span className="font-mono text-[10px] uppercase tracking-wider text-success">ок</span>;
  if (state === 'failed') return <span className="font-mono text-[10px] uppercase tracking-wider text-danger">ошибка</span>;
  return null;
}

function ActionButton({ tone, onClick, disabled, children }) {
  const cls =
    tone === 'primary'
      ? 'bg-accent text-white hover:bg-accent-hover'
      : tone === 'danger'
        ? 'bg-danger text-white hover:opacity-90'
        : 'border border-border bg-surface text-secondary hover:border-border-hover hover:text-primary';
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[11px] font-medium transition-all disabled:opacity-50 ${cls}`}
    >
      {children}
    </button>
  );
}

function CheckIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
      <path d="M2.5 6.5L4.8 8.8L9.5 3.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function BangIcon() {
  return (
    <svg width="10" height="10" viewBox="0 0 12 12" fill="none">
      <path d="M6 2.5V7" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      <circle cx="6" cy="9.2" r="0.9" fill="currentColor" />
    </svg>
  );
}

function SpinnerIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" className="animate-spin">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeDasharray="14 60" />
    </svg>
  );
}

function ChevronIcon({ className = '' }) {
  return (
    <svg width="10" height="10" viewBox="0 0 12 12" fill="none" className={className}>
      <path d="M3 4.5L6 7.5L9 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function PlayIcon() {
  return (
    <svg width="9" height="9" viewBox="0 0 12 12" fill="none">
      <path d="M3 2.5L9.5 6L3 9.5V2.5Z" fill="currentColor" />
    </svg>
  );
}

function FastForwardIcon() {
  return (
    <svg width="10" height="10" viewBox="0 0 12 12" fill="none">
      <path d="M2 2.5L6 6L2 9.5V2.5Z" fill="currentColor" />
      <path d="M6.5 2.5L10.5 6L6.5 9.5V2.5Z" fill="currentColor" />
    </svg>
  );
}

function RetryIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 12 12" fill="none">
      <path
        d="M9.5 5C9 3.3 7.4 2 5.5 2A4 4 0 1 0 9.5 6"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        fill="none"
      />
      <path d="M9.5 1.5V4.5H6.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" fill="none" />
    </svg>
  );
}

function StopIcon() {
  return (
    <svg width="10" height="10" viewBox="0 0 12 12" fill="none">
      <rect x="2.5" y="2.5" width="7" height="7" rx="1" fill="currentColor" />
    </svg>
  );
}
