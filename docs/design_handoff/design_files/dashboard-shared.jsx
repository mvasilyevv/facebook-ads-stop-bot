// dashboard-shared.jsx — reusable FB Stop Bot dashboard elements (web + mini).
// Depends on: window.Icon, Eyebrow, RulePills, Trend, PulseDot, SpendChart (icons.jsx/components.jsx)
(function () {
  const { Icon, Eyebrow, RulePills, Trend, PulseDot } = window;

  // ── Blueprint grid texture (Vercel/Geist) ──────────────────────────────────
  function BlueprintBG({ ticks = true, dots = true }) {
    return (
      <div aria-hidden="true" style={{ position: 'absolute', inset: 0, pointerEvents: 'none', overflow: 'hidden' }}>
        {dots && <div style={{
          position: 'absolute', inset: 0,
          backgroundImage: 'radial-gradient(circle, var(--bg-6) 0.75px, transparent 0.75px)',
          backgroundSize: '24px 24px', opacity: 0.4,
        }} />}
        <div style={{
          position: 'absolute', inset: 0,
          backgroundImage: 'linear-gradient(var(--bg-5) 1px, transparent 1px), linear-gradient(90deg, var(--bg-5) 1px, transparent 1px)',
          backgroundSize: '96px 96px', opacity: 0.5,
          maskImage: 'radial-gradient(ellipse 80% 70% at 50% 0%, #000 40%, transparent 100%)',
          WebkitMaskImage: 'radial-gradient(ellipse 80% 70% at 50% 0%, #000 40%, transparent 100%)',
        }} />
        {ticks && [
          { top: 14, left: 14, rot: 0 }, { top: 14, right: 14, rot: 90 },
          { bottom: 14, right: 14, rot: 180 }, { bottom: 14, left: 14, rot: 270 },
        ].map((c, i) => (
          <span key={i} style={{
            position: 'absolute', width: 9, height: 9,
            top: c.top ?? 'auto', bottom: c.bottom ?? 'auto', left: c.left ?? 'auto', right: c.right ?? 'auto',
            borderTop: '1px solid var(--bg-7)', borderLeft: '1px solid var(--bg-7)',
            transform: `rotate(${c.rot}deg)`, transformOrigin: 'center',
          }} />
        ))}
      </div>
    );
  }

  // ── Sparkline ───────────────────────────────────────────────────────────────
  function Sparkline({ data, w = 88, h = 26, color = 'var(--accent)', fill = false }) {
    const max = Math.max(...data), min = Math.min(...data);
    const rng = max - min || 1;
    const pts = data.map((v, i) => `${(i / (data.length - 1)) * w},${h - ((v - min) / rng) * (h - 4) - 2}`);
    return (
      <svg width={w} height={h} style={{ display: 'block', overflow: 'visible' }} aria-hidden="true">
        {fill && <polygon points={`0,${h} ${pts.join(' ')} ${w},${h}`} fill={color} opacity="0.12" />}
        <polyline points={pts.join(' ')} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
        <circle cx={w} cy={h - ((data[data.length - 1] - min) / rng) * (h - 4) - 2} r="2" fill={color} />
      </svg>
    );
  }
  const SPARK = {
    active:   [240, 238, 244, 241, 245, 243, 247, 246, 247],
    warning:  [4, 5, 6, 5, 8, 9, 7, 10, 12],
    stop:     [2, 3, 5, 4, 6, 5, 4, 5, 4],
    disabled: [62, 65, 68, 70, 74, 79, 83, 86, 89],
  };
  const TONE = { normal: 'var(--bg-11)', warning: 'var(--warning)', stop: 'var(--danger)', disabled: 'var(--bg-9)' };

  // ── Count-up ─────────────────────────────────────────────────────────────────
  function useCountUp(target, dur = 750) {
    const [v, setV] = React.useState(0);
    React.useEffect(() => {
      if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) { setV(target); return; }
      let raf, start;
      const step = (t) => {
        if (!start) start = t;
        const p = Math.min(1, (t - start) / dur);
        setV(Math.round(target * (1 - Math.pow(1 - p, 3))));
        if (p < 1) raf = requestAnimationFrame(step);
      };
      raf = requestAnimationFrame(step);
      return () => cancelAnimationFrame(raf);
    }, [target, dur]);
    return v;
  }
  function CountUp({ value, ...rest }) {
    const v = useCountUp(value);
    return <span className="mono tnum" {...rest}>{v}</span>;
  }

  // ── Health-bar ────────────────────────────────────────────────────────────────
  function HealthBar({ normal, warning, stop, compact }) {
    const total = normal + warning + stop;
    const [mounted, setMounted] = React.useState(false);
    React.useEffect(() => { const r = requestAnimationFrame(() => setMounted(true)); return () => cancelAnimationFrame(r); }, []);
    const segs = [
      { k: 'Норма', n: normal, c: 'var(--bg-7)' },
      { k: 'Предупреждение', n: warning, c: 'var(--warning)' },
      { k: 'Стоп', n: stop, c: 'var(--danger)' },
    ];
    return (
      <div>
        <div style={{ display: 'flex', height: compact ? 6 : 8, border: '1px solid var(--bg-6)', overflow: 'hidden', background: 'var(--bg-2)' }}>
          {segs.map((s) => (
            <div key={s.k} style={{
              width: mounted ? `${(s.n / total) * 100}%` : '0%', background: s.c,
              transition: 'width 800ms cubic-bezier(0.2,0.8,0.2,1)', borderRight: '1px solid var(--bg-0)',
            }} />
          ))}
        </div>
        <div style={{ display: 'flex', gap: compact ? 12 : 18, marginTop: 10, flexWrap: 'wrap' }}>
          {segs.map((s) => (
            <span key={s.k} style={{ display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 12, color: 'var(--bg-9)' }}>
              <span style={{ width: 7, height: 7, borderRadius: 999, background: s.c }} />
              {s.k} <b className="mono tnum" style={{ color: 'var(--bg-11)', marginLeft: 2 }}>{s.n}</b>
            </span>
          ))}
        </div>
      </div>
    );
  }

  // ── Live-tail feed (ad-level) ──────────────────────────────────────────────────
  const FEED_POOL = [
    { stage: 'warning', ad: 'CR2 | DRC | MV | Tyver | 25.03', rules: ['CPL_HIGH'] },
    { stage: 'stop', ad: 'UA17 | SP | MV | Kyiv | 30.01', rules: ['CPL_HIGH', 'FREQ_HIGH'] },
    { stage: 'warning', ad: 'BR9 | DRC | AK | Lima | 19.02', rules: ['SPEND_NO_EVENT'] },
    { stage: 'claimed', ad: 'PL4 | NUT | MV | Warsaw | 02.03', rules: ['ROAS_LOW'] },
    { stage: 'warning', ad: 'DE2 | SP | TK | Berlin | 11.04', rules: ['CTR_LOW'] },
    { stage: 'stop', ad: 'IT8 | DRC | AK | Roma | 27.02', rules: ['CPL_HIGH', 'BUDGET_OVER'] },
    { stage: 'warning', ad: 'ES6 | DRC | MV | Madrid | 09.04', rules: ['CPL_HIGH'] },
    { stage: 'warning', ad: 'NL1 | SP | AK | Amsterdam | 21.02', rules: ['FREQ_HIGH'] },
  ];
  function nowTime(offset = 0) {
    return new Date(Date.now() - offset * 1000).toTimeString().slice(0, 8);
  }
  function LiveFeed({ rows: initial, onRow, max = 7, live = true }) {
    const [rows, setRows] = React.useState(() => initial.map((r, i) => ({ ...r, id: 'f' + i, time: nowTime(i * 47), fresh: false })));
    const idRef = React.useRef(1000);
    React.useEffect(() => {
      if (!live) return;
      if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
      const iv = setInterval(() => {
        const pick = FEED_POOL[Math.floor(Math.random() * FEED_POOL.length)];
        setRows((prev) => [{ ...pick, id: 'f' + (idRef.current++), time: nowTime(0), fresh: true }, ...prev].slice(0, max));
      }, 3200);
      return () => clearInterval(iv);
    }, [live, max]);
    return (
      <div style={{ overflow: 'hidden' }}>
        {rows.map((d) => {
          const c = { warning: 'var(--fsm-warning)', stop: 'var(--fsm-stop)', claimed: 'var(--fsm-claimed)', normal: 'var(--fsm-normal)' }[d.stage];
          return (
            <button key={d.id} onClick={() => onRow && onRow(d)} className={d.fresh ? 'feed-row-new' : ''} style={{
              display: 'grid', gridTemplateColumns: 'auto auto 1fr auto auto', gap: 'var(--s-3)', alignItems: 'center',
              borderBottom: '1px solid var(--bg-5)', padding: '0 var(--s-4)', height: 'var(--row-h)', width: '100%',
              background: 'transparent', border: 'none', borderBottom: '1px solid var(--bg-5)', textAlign: 'left',
              cursor: onRow ? 'pointer' : 'default', color: 'inherit', font: 'inherit',
            }}>
              <span className="mono tnum" style={{ fontSize: 'var(--row-fs)', color: 'var(--bg-9)', minWidth: 62 }}>{d.time}</span>
              {d.stage === 'stop'
                ? <PulseDot size={7} color={c} />
                : <span style={{ width: 7, height: 7, borderRadius: 999, background: c, flex: 'none' }} />}
              <span className="mono" style={{ fontSize: 'var(--row-fs)', color: 'var(--bg-11)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.ad}</span>
              <RulePills rules={d.rules} />
              <span style={{ color: 'var(--bg-8)' }}><Icon name="chevR" size={14} /></span>
            </button>
          );
        })}
      </div>
    );
  }

  // ── Worker health (count + popover) ────────────────────────────────────────────
  const WORKERS = [
    { name: 'observer', status: 'up' },
    { name: 'vision-pool-1', status: 'up' },
    { name: 'vision-pool-2', status: 'down', err: 'browser crash · рестарт ×2' },
    { name: 'telegram-bot', status: 'up' },
    { name: 'alert-dispatcher', status: 'up' },
    { name: 'disable-worker', status: 'up' },
    { name: 'enable-worker', status: 'up' },
    { name: 'scheduler', status: 'up' },
    { name: 'metrics-poller', status: 'up' },
    { name: 'ai-analyzer', status: 'up' },
    { name: 'outbox-drainer', status: 'up' },
    { name: 'health-monitor', status: 'up' },
    { name: 'cabinet-sync', status: 'up' },
  ];
  function WorkerStatus({ placement = 'right' }) {
    const [open, setOpen] = React.useState(false);
    const up = WORKERS.filter((w) => w.status === 'up').length;
    const total = WORKERS.length, down = total - up;
    const health = down === 0 ? 'success' : down <= 2 ? 'warning' : 'danger';
    const c = { success: 'var(--success)', warning: 'var(--warning)', danger: 'var(--danger)' }[health];
    const sorted = [...WORKERS].sort((a, b) => (a.status === 'down' ? -1 : 0) - (b.status === 'down' ? -1 : 0));
    const pos = placement === 'left' ? { left: 0 } : placement === 'center' ? { left: '50%', transform: 'translateX(-50%)' } : { right: 0 };
    return (
      <span style={{ position: 'relative', display: 'inline-flex' }}
        onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
        <span tabIndex={0} role="button" aria-expanded={open} aria-label={`${up} из ${total} воркеров онлайн`}
          onFocus={() => setOpen(true)} onBlur={() => setOpen(false)} onClick={() => setOpen((o) => !o)}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'pointer', color: down ? c : 'var(--bg-10)', borderBottom: '1px dotted var(--bg-7)', paddingBottom: 1 }}>
          <PulseDot size={7} color={c} />
          <span className="tnum" style={{ whiteSpace: 'nowrap' }}>{up}/{total} воркеров</span>
        </span>
        {open && (
          <div role="tooltip" style={{
            position: 'absolute', top: 'calc(100% + 8px)', zIndex: 60, width: 248, ...pos,
            background: 'var(--bg-3)', border: '1px solid var(--bg-6)', padding: 'var(--s-3)',
            boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.03)',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
              <span className="eyebrow">ВОРКЕРЫ</span>
              <span className="mono tnum" style={{ fontSize: 11, color: c }}>{up}/{total} online</span>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column' }}>
              {sorted.map((w) => (
                <div key={w.name} style={{ display: 'grid', gridTemplateColumns: 'auto 1fr auto', gap: 8, alignItems: 'center', padding: '4px 0', borderTop: '1px solid var(--bg-5)' }}>
                  <span style={{ width: 6, height: 6, borderRadius: 999, background: w.status === 'up' ? 'var(--success)' : 'var(--danger)', flex: 'none' }} />
                  <span className="mono" style={{ fontSize: 12, color: w.status === 'up' ? 'var(--bg-10)' : 'var(--bg-11)', overflow: 'hidden', textOverflow: 'ellipsis' }}>{w.name}</span>
                  {w.status === 'up'
                    ? <span style={{ fontSize: 10, color: 'var(--bg-8)' }}>up</span>
                    : <span style={{ fontSize: 10, color: 'var(--danger)' }}>down</span>}
                  {w.status === 'down' && <span style={{ gridColumn: '2 / 4', fontSize: 11, color: 'var(--bg-9)', marginTop: 1 }}>{w.err}</span>}
                </div>
              ))}
            </div>
          </div>
        )}
      </span>
    );
  }

  // ── Countdown ring + scan control ──────────────────────────────────────────────
  function CountdownRing({ value, max, size = 34, active }) {
    const r = (size - 5) / 2, circ = 2 * Math.PI * r;
    const frac = Math.max(0, Math.min(1, value / max));
    return (
      <span style={{ position: 'relative', width: size, height: size, flex: 'none', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
        <svg width={size} height={size} style={{ transform: 'rotate(-90deg)' }}>
          <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--bg-6)" strokeWidth="2" />
          <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={active ? 'var(--accent)' : 'var(--accent-muted)'} strokeWidth="2"
            strokeDasharray={circ} strokeDashoffset={circ * (1 - frac)} strokeLinecap="round" style={{ transition: 'stroke-dashoffset 0.95s linear' }} />
        </svg>
        <span className="mono tnum" style={{ position: 'absolute', fontSize: size > 30 ? 11 : 10, color: 'var(--bg-10)' }}>{value}</span>
      </span>
    );
  }
  // useScan — shared scan state machine (countdown + age + sweep)
  function useScan(interval = 30, startAge = 14, enabled = true) {
    const [scanning, setScanning] = React.useState(false);
    const [age, setAge] = React.useState(startAge);
    const [next, setNext] = React.useState(Math.max(1, interval - startAge));
    const ref = React.useRef(false);
    const doScan = React.useCallback(() => {
      setScanning(true); ref.current = true;
      setTimeout(() => { setScanning(false); ref.current = false; setAge(0); setNext(interval); }, 1400);
    }, [interval]);
    React.useEffect(() => {
      const iv = setInterval(() => { if (ref.current) return; setAge((a) => a + 1); if (enabled) setNext((n) => Math.max(0, n - 1)); }, 1000);
      return () => clearInterval(iv);
    }, [enabled]);
    React.useEffect(() => { if (enabled && next === 0 && !ref.current) doScan(); }, [next, enabled, doScan]);
    return { scanning, age, next, interval, doScan, enabled };
  }
  // Paused-scan banner (Observer off) + paused ring glyph.
  function PausedRing({ size = 34 }) {
    const r = (size - 5) / 2;
    return (
      <span style={{ position: 'relative', width: size, height: size, flex: 'none', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
        <svg width={size} height={size}><circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--bg-6)" strokeWidth="2" strokeDasharray="3 4" /></svg>
        <span style={{ position: 'absolute', color: 'var(--warning)', display: 'flex' }}><Icon name="pause" size={size > 30 ? 14 : 12} stroke={2} /></span>
      </span>
    );
  }
  function PausedBanner({ since = '14:32', onEnable }) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 16px', border: '1px solid color-mix(in srgb, var(--warning) 34%, transparent)', borderLeft: '2px solid var(--warning)', background: 'var(--warning-bg)' }}>
        <span style={{ color: 'var(--warning)', flex: 'none', display: 'flex' }}><Icon name="pause" size={18} stroke={2} /></span>
        <div style={{ flex: 1, fontSize: 13, color: 'var(--bg-11)', lineHeight: 1.45 }}>
          <b style={{ color: 'var(--warning)' }}>Observer выключен</b> — объявления не мониторятся с <span className="mono tnum">{since}</span>. Алерты, авто-disable и live-tail на паузе.
        </div>
        {onEnable && (
          <button className="btn" onClick={onEnable} style={{ flex: 'none', background: 'var(--warning)', color: 'var(--bg-0)', fontWeight: 600 }}>
            <Icon name="play" size={14} />Включить
          </button>
        )}
      </div>
    );
  }

  // Desktop scan cluster: ring + next + last-scan + workers + button
  function ScanSweep() {
    const { scanning, age, next, interval, doScan } = useScan();
    return (
      <>
        {scanning && (
          <div aria-hidden="true" style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 2, overflow: 'hidden', zIndex: 5 }}>
            <div style={{ height: '100%', width: '100%', background: 'var(--accent)', animation: 'fbBarSweep 1.4s cubic-bezier(0.4,0,0.2,1)' }} />
          </div>
        )}
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--s-4)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <CountdownRing value={scanning ? interval : next} max={interval} active={scanning} />
            <div className="eyebrow" style={{ fontSize: 9, color: scanning ? 'var(--accent)' : 'var(--bg-9)' }}>{scanning ? 'ИДЁТ СКАН' : 'СЛЕД.\u00A0СКАН'}</div>
          </div>
          <div style={{ width: 1, height: 28, background: 'var(--bg-5)' }} />
          <div style={{ lineHeight: 1.3 }}>
            <div className="eyebrow" style={{ fontSize: 9 }}>ПОСЛЕДНИЙ СКАН</div>
            <div className="mono tnum" style={{ fontSize: 13, color: 'var(--bg-10)', whiteSpace: 'nowrap', display: 'flex', alignItems: 'center', gap: 6 }}>
              {scanning ? 'сканирую…' : <>{age}с назад <span style={{ color: 'var(--bg-7)' }}>·</span> <WorkerStatus /></>}
            </div>
          </div>
          <button className="btn btn-primary" onClick={() => { if (!scanning) doScan(); }} disabled={scanning} style={{ opacity: scanning ? 0.7 : 1, marginLeft: 4 }}>
            <Icon name="refresh" size={16} stroke={1.8} style={scanning ? { animation: 'fbSpin 1s linear infinite' } : undefined} />{scanning ? 'Сканирую' : 'Сканировать'}
          </button>
        </div>
      </>
    );
  }

  Object.assign(window, {
    BlueprintBG, Sparkline, SPARK, TONE, useCountUp, CountUp, HealthBar,
    LiveFeed, FEED_POOL, nowTime, WORKERS, WorkerStatus, CountdownRing, useScan, ScanSweep,
    PausedRing, PausedBanner,
  });
})();
