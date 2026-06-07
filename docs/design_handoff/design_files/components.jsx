// components.jsx — shared FB Stop Bot primitives (used by web + mini). Babel.
(function () {
  const Icon = window.Icon;

  // Shared pulse clock — keeps every .wp-dot in phase regardless of mount time.
  // A negative animation-delay anchored to one epoch makes phase = (now-epoch)%dur
  // for all dots, so they breathe in unison even as live rows mount later.
  const PULSE_MS = 2400;
  const PULSE_EPOCH = Date.now();
  function syncPulse() { return { animationDelay: `-${(Date.now() - PULSE_EPOCH) % PULSE_MS}ms` }; }
  window.syncPulse = syncPulse;

  // PulseDot — synced breathing dot. Computes its negative animation-delay ONCE
  // at mount (stable ref, never recomputed on re-render) so phase = (now-epoch)
  // % dur for every instance → all dots breathe in unison, even late arrivals.
  function PulseDot({ size = 7, color, style }) {
    const delayRef = React.useRef(null);
    if (delayRef.current === null) delayRef.current = `-${(Date.now() - PULSE_EPOCH) % PULSE_MS}ms`;
    return (
      <span className="wp-dot" style={{
        width: size, height: size, borderRadius: 999, background: color, color,
        animationDelay: delayRef.current, flex: 'none', ...style,
      }} />
    );
  }
  window.PulseDot = PulseDot;

  // FSM state → {label (ru), cls}
  const FSM = {
    normal:       { label: 'Норма',          cls: 'badge-normal' },
    warning:      { label: 'Предупреждение', cls: 'badge-warning' },
    warning_sent: { label: 'Предупреждение', cls: 'badge-warning' },
    stop:         { label: 'Стоп',           cls: 'badge-stop' },
    stop_sent:    { label: 'Стоп',           cls: 'badge-stop' },
    claimed:      { label: 'В работе',       cls: 'badge-claimed' },
    disabled:     { label: 'Отключено',      cls: 'badge-disabled' },
  };
  const TASK_STATUS = {
    pending:     { label: 'в очереди', color: 'var(--bg-10)' },
    in_progress: { label: 'в работе',  color: 'var(--info)' },
    failed:      { label: 'ошибка',    color: 'var(--danger)' },
    done:        { label: 'готово',    color: 'var(--success)' },
  };

  function FsmBadge({ state, sm }) {
    const f = FSM[state] || FSM.normal;
    return (
      <span className={`badge ${f.cls}${sm ? ' sm' : ''}`}>
        <span className="dot" />{f.label}
      </span>
    );
  }

  function Eyebrow({ num, children, style }) {
    return (
      <span className="eyebrow" style={style}>
        {num && <span className="num">{num}</span>}
        {num && <span style={{ color: 'var(--bg-7)' }}>/</span>}
        {children}
      </span>
    );
  }

  function RulePills({ rules }) {
    return (
      <span style={{ display: 'inline-flex', gap: 4, flexWrap: 'wrap' }}>
        {rules.map((r) => <span key={r} className="rulepill">{r}</span>)}
      </span>
    );
  }

  // Trend chip
  function Trend({ value, pct, tone }) {
    if (!value) return <span style={{ fontSize: 12, color: 'var(--bg-8)' }} className="tnum">—</span>;
    const up = value > 0;
    const danger = tone === 'stop' || tone === 'warning';
    const color = up
      ? (danger ? 'var(--danger)' : 'var(--success)')
      : (danger ? 'var(--success)' : 'var(--bg-10)');
    return (
      <span className="tnum" style={{ display: 'inline-flex', alignItems: 'center', gap: 2, fontSize: 12, fontWeight: 500, color }}>
        <Icon name={up ? 'arrowU' : 'arrowD'} size={12} stroke={2} />{pct}
      </span>
    );
  }

  // KPI card
  function KpiCard({ d, big = 40 }) {
    const toneColor = {
      normal: 'var(--bg-11)', warning: 'var(--warning)',
      stop: 'var(--danger)', disabled: 'var(--bg-9)',
    }[d.tone] || 'var(--bg-11)';
    return (
      <div className="card" style={{ padding: 'var(--s-5)', display: 'flex', flexDirection: 'column', gap: 'var(--s-3)', minWidth: 0 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
          <Eyebrow>{d.eyebrow}</Eyebrow>
          <span className="dot-mini" style={{ width: 7, height: 7, borderRadius: 999, background: toneColor, opacity: d.tone === 'normal' ? 0.5 : 1 }} />
        </div>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 8 }}>
          <span className="mono tnum" style={{ fontSize: big, fontWeight: 500, lineHeight: 0.95, color: toneColor, letterSpacing: '-0.02em' }}>{d.value}</span>
          <Trend value={d.trend} pct={d.trendPct} tone={d.tone} />
        </div>
        <div style={{ fontSize: 12, color: 'var(--bg-9)' }}>
          <span style={{ color: 'var(--bg-10)' }}>{d.label}</span> · {d.note}
        </div>
      </div>
    );
  }

  // Spend × hour area chart (interactive hover, optional draw-on + live pulse)
  function SpendChart({ data, height = 200, accent = 'var(--accent)', animate = false, live = false }) {
    const ref = React.useRef(null);
    const lineRef = React.useRef(null);
    const [w, setW] = React.useState(640);
    const [hover, setHover] = React.useState(null);
    React.useEffect(() => {
      const el = lineRef.current;
      if (!animate || !el || !el.getTotalLength) return;
      if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
      const len = el.getTotalLength();
      el.style.transition = 'none';
      el.style.strokeDasharray = len;
      el.style.strokeDashoffset = len;
      void el.getBoundingClientRect();
      requestAnimationFrame(() => {
        el.style.transition = 'stroke-dashoffset 900ms cubic-bezier(0.2,0.8,0.2,1)';
        el.style.strokeDashoffset = '0';
      });
    }, [animate, w, data]);
    React.useEffect(() => {
      if (!ref.current) return;
      const ro = new ResizeObserver((e) => setW(e[0].contentRect.width));
      ro.observe(ref.current);
      return () => ro.disconnect();
    }, []);
    const padB = 22, padT = 10, padL = 0, padR = 0;
    const H = height, innerH = H - padB - padT;
    const max = Math.max(...data) * 1.1;
    const n = data.length;
    const x = (i) => padL + (i / (n - 1)) * (w - padL - padR);
    const y = (v) => padT + innerH - (v / max) * innerH;
    const linePts = data.map((v, i) => `${x(i)},${y(v)}`).join(' ');
    const areaPath = `M${x(0)},${y(data[0])} ` + data.map((v, i) => `L${x(i)},${y(v)}`).join(' ') + ` L${x(n - 1)},${H - padB} L${x(0)},${H - padB} Z`;
    const gid = React.useId ? React.useId().replace(/:/g, '') : 'sg';

    const onMove = (e) => {
      const rect = ref.current.getBoundingClientRect();
      const px = e.clientX - rect.left;
      const i = Math.max(0, Math.min(n - 1, Math.round((px / rect.width) * (n - 1))));
      setHover(i);
    };
    const fmt = (v) => '$' + v.toLocaleString('en-US');
    return (
      <div ref={ref} style={{ position: 'relative', width: '100%' }}
        onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        <svg width="100%" height={H} style={{ display: 'block', overflow: 'visible' }}>
          <defs>
            <linearGradient id={`fill${gid}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={accent} stopOpacity="0.18" />
              <stop offset="100%" stopColor={accent} stopOpacity="0" />
            </linearGradient>
          </defs>
          {[0.25, 0.5, 0.75, 1].map((g, i) => (
            <line key={i} x1={0} x2={w} y1={padT + innerH * g} y2={padT + innerH * g}
              stroke="var(--bg-5)" strokeWidth="1" strokeDasharray={i === 3 ? '0' : '2 4'} opacity={i === 3 ? 1 : 0.6} />
          ))}
          <path d={areaPath} fill={`url(#fill${gid})`} />
          <polyline ref={lineRef} points={linePts} fill="none" stroke={accent} strokeWidth="1.5" strokeLinejoin="round" />
          {[0, 6, 12, 18, 23].map((i) => (
            <text key={i} x={x(i)} y={H - 6} fontSize="10" fontFamily="var(--font-num)"
              fill="var(--bg-8)" textAnchor={i === 0 ? 'start' : i === 23 ? 'end' : 'middle'}>
              {String(i).padStart(2, '0')}:00
            </text>
          ))}
          {hover != null && (
            <g>
              <line x1={x(hover)} x2={x(hover)} y1={padT} y2={H - padB} stroke="var(--bg-7)" strokeWidth="1" />
              <circle cx={x(hover)} cy={y(data[hover])} r="3.5" fill="var(--bg-0)" stroke={accent} strokeWidth="1.5" />
            </g>
          )}
        </svg>
        {live && (
          <PulseDot size={8} color={accent} style={{ position: 'absolute', pointerEvents: 'none', right: -1, top: y(data[n - 1]) - 4 }} />
        )}
        {hover != null && (
          <div style={{
            position: 'absolute', top: 0, pointerEvents: 'none',
            left: Math.min(Math.max(x(hover) - 50, 0), w - 100),
            background: 'var(--bg-3)', border: '1px solid var(--bg-6)', padding: '6px 8px', minWidth: 92,
          }}>
            <div className="eyebrow" style={{ fontSize: 9, marginBottom: 2 }}>{String(hover).padStart(2, '0')}:00 · SPEND</div>
            <div className="mono tnum" style={{ fontSize: 14, color: 'var(--bg-11)' }}>{fmt(data[hover])}</div>
          </div>
        )}
      </div>
    );
  }

  // Incident row (compact list item)
  function IncidentRow({ d, onClick }) {
    return (
      <button onClick={onClick} className="incident-row" style={{
        display: 'grid', gridTemplateColumns: '1fr auto', gap: 8, alignItems: 'center',
        width: '100%', textAlign: 'left', background: 'transparent', border: 'none',
        borderBottom: '1px solid var(--bg-5)', padding: '11px var(--s-4)', cursor: 'pointer',
        color: 'inherit', font: 'inherit',
      }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 5 }}>
            <FsmBadge state={d.state} sm />
            <span style={{ fontSize: 12, color: 'var(--bg-9)' }} className="tnum">{d.age}</span>
          </div>
          <div className="mono" style={{ fontSize: 13, color: 'var(--bg-11)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.ad}</div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div className="mono tnum" style={{ fontSize: 13, color: 'var(--bg-11)' }}>${d.spend.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}</div>
          <div style={{ marginTop: 4 }}><RulePills rules={d.rules} /></div>
        </div>
      </button>
    );
  }

  // Event row
  function EventRow({ d, onClick, dense }) {
    const dotColor = { warning: 'var(--fsm-warning)', stop: 'var(--fsm-stop)', claimed: 'var(--fsm-claimed)', normal: 'var(--fsm-normal)' }[d.stage];
    return (
      <button onClick={onClick} className="event-row" style={{
        display: 'grid', gridTemplateColumns: 'auto auto 1fr auto auto', gap: 'var(--s-3)', alignItems: 'center',
        width: '100%', textAlign: 'left', background: 'transparent', border: 'none',
        borderBottom: '1px solid var(--bg-5)', padding: '0 var(--s-4)', height: 'var(--row-h)',
        cursor: 'pointer', color: 'inherit', font: 'inherit',
      }}>
        <span className="mono tnum" style={{ fontSize: 'var(--row-fs)', color: 'var(--bg-9)', minWidth: 62 }}>{d.time}</span>
        <span style={{ width: 7, height: 7, borderRadius: 999, background: dotColor }} />
        <span className="mono" style={{ fontSize: 'var(--row-fs)', color: 'var(--bg-11)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.ad}</span>
        <RulePills rules={d.rules} />
        <span style={{ color: 'var(--bg-8)' }}><Icon name="chevR" size={14} /></span>
      </button>
    );
  }

  // Task queue row
  function TaskRow({ d }) {
    const st = TASK_STATUS[d.status];
    return (
      <div style={{
        display: 'grid', gridTemplateColumns: '1fr auto auto', gap: 'var(--s-3)', alignItems: 'center',
        borderBottom: '1px solid var(--bg-5)', padding: '0 var(--s-3)', height: 'var(--row-h)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
          <span style={{ width: 6, height: 6, borderRadius: 999, background: st.color, flex: 'none' }} />
          <span className="mono" style={{ fontSize: 'var(--row-fs)', color: 'var(--bg-11)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.ad}</span>
        </div>
        <span style={{ fontSize: 11, color: st.color, minWidth: 56, textAlign: 'right' }}>{st.label}</span>
        <span className="mono tnum" style={{ fontSize: 11, color: 'var(--bg-8)', minWidth: 52, textAlign: 'right' }}>
          {d.attempts > 0 ? `×${d.attempts} · ` : ''}{d.age}
        </span>
      </div>
    );
  }

  // Worker pulse dot
  function WorkerPulse({ label = 'Observer', health = 'success' }) {
    const c = { success: 'var(--success)', warning: 'var(--warning)', danger: 'var(--danger)' }[health];
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 12, color: 'var(--bg-10)' }}>
        <PulseDot size={7} color={c} style={{ boxShadow: `0 0 0 0 ${c}` }} />
        {label}
      </span>
    );
  }

  Object.assign(window, {
    FSM, FsmBadge, Eyebrow, RulePills, Trend, KpiCard,
    SpendChart, IncidentRow, EventRow, TaskRow, WorkerPulse,
  });
})();
