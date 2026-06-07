// mini-dashboard.jsx — Telegram Mini App dashboard (mobile, dark). window.MiniDashboard
(function () {
  const { Icon, Eyebrow, SpendChart, FsmBadge, RulePills, PulseDot, Trend, TaskRow,
    BlueprintBG, HealthBar, CountUp, Sparkline, SPARK, TONE, LiveFeed, WorkerStatus, useScan, CountdownRing, PausedRing, PausedBanner } = window;

  const TABS = [
    { id: 'dashboard', label: 'Панель', icon: 'dashboard' },
    { id: 'ads', label: 'Объявления', icon: 'ads' },
    { id: 'drafts', label: 'Черновики', icon: 'drafts' },
    { id: 'history', label: 'История', icon: 'history' },
    { id: 'settings', label: 'Ещё', icon: 'settings' },
  ];

  const TAB_HREFS = { dashboard: encodeURI('FB Stop Bot — Dashboard.html'), ads: encodeURI('FB Stop Bot — Ads.html') };
  function TabBar({ onNav, active = 'dashboard' }) {
    return (
      <nav style={{ flex: 'none', display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', borderTop: '1px solid var(--bg-6)', background: 'var(--bg-1)', paddingBottom: 22 }}>
        {TABS.map((t) => {
          const on = t.id === active;
          const href = TAB_HREFS[t.id];
          const st = { minHeight: 52, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 4, background: 'transparent', border: 'none', cursor: 'pointer', color: on ? 'var(--accent)' : 'var(--bg-9)', padding: '8px 2px', textDecoration: 'none' };
          const inner = (<><Icon name={t.icon} size={21} stroke={on ? 2 : 1.6} /><span style={{ fontSize: 10, fontWeight: on ? 600 : 500, fontFamily: 'var(--font-body)' }}>{t.label}</span></>);
          return href
            ? <a key={t.id} href={href} aria-current={on ? 'page' : undefined} style={st}>{inner}</a>
            : <button key={t.id} onClick={() => onNav(t.id)} aria-current={on ? 'page' : undefined} style={st}>{inner}</button>;
        })}
      </nav>
    );
  }

  function MiniScanHeader({ onToast, scanOn = true, onEnable }) {
    const { scanning, age, next, interval, doScan } = useScan(30, 14, scanOn);
    return (
      <header style={{ flex: 'none', padding: '8px 16px 12px', borderBottom: '1px solid var(--bg-5)', background: 'var(--bg-0)', position: 'relative' }}>
        {scanning && (
          <div aria-hidden="true" style={{ position: 'absolute', bottom: -1, left: 0, right: 0, height: 2, overflow: 'hidden' }}>
            <div style={{ height: '100%', width: '100%', background: 'var(--accent)', animation: 'fbBarSweep 1.4s cubic-bezier(0.4,0,0.2,1)' }} />
          </div>
        )}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            <Eyebrow num="01">ОБЗОР · {scanOn ? 'LIVE' : 'ПАУЗА'}</Eyebrow>
            <h1 className="mono" style={{ fontSize: 26, fontWeight: 500, color: 'var(--bg-11)', margin: '4px 0 0', letterSpacing: '-0.02em' }}>Панель</h1>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            {scanOn ? (
              <>
                <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                  <CountdownRing value={scanning ? interval : next} max={interval} active={scanning} size={30} />
                  <div style={{ lineHeight: 1.2 }}>
                    <div className="eyebrow" style={{ fontSize: 8 }}>{scanning ? 'СКАН' : 'СЛЕД.'}</div>
                    <div className="mono tnum" style={{ fontSize: 11, color: 'var(--bg-10)' }}>{scanning ? '···' : next + 'с'}</div>
                  </div>
                </div>
                <button aria-label="Сканировать" onClick={() => { if (!scanning) { doScan(); onToast && onToast('scan-now'); } }} disabled={scanning}
                  className="btn btn-primary" style={{ width: 44, height: 44, padding: 0, justifyContent: 'center', opacity: scanning ? 0.7 : 1 }}>
                  <Icon name="refresh" size={18} stroke={1.8} style={scanning ? { animation: 'fbSpin 1s linear infinite' } : undefined} />
                </button>
              </>
            ) : (
              <>
                <PausedRing size={30} />
                <button aria-label="Включить Observer" onClick={onEnable} className="btn btn-primary" style={{ width: 44, height: 44, padding: 0, justifyContent: 'center' }}>
                  <Icon name="play" size={18} />
                </button>
              </>
            )}
          </div>
        </div>
        <div className="mono tnum" style={{ fontSize: 12, color: 'var(--bg-9)', marginTop: 10, display: 'flex', alignItems: 'center', gap: 6 }}>
          скан {scanning ? '···' : age + 'с'} назад <span style={{ color: 'var(--bg-7)' }}>·</span> <WorkerStatus placement="left" />
        </div>
      </header>
    );
  }

  function MiniSheet({ event, onClose }) {
    React.useEffect(() => {
      const onKey = (e) => { if (e.key === 'Escape') onClose(); };
      window.addEventListener('keydown', onKey);
      return () => window.removeEventListener('keydown', onKey);
    }, [onClose]);
    const d = window.DATA.eventDetail.e1;
    return (
      <div style={{ position: 'absolute', inset: 0, zIndex: 50, display: 'flex', flexDirection: 'column', justifyContent: 'flex-end' }}>
        <div onClick={onClose} style={{ position: 'absolute', inset: 0, background: 'rgba(10,10,11,0.7)', animation: 'fbFade var(--dur-base) var(--ease-out)' }} />
        <div role="dialog" aria-label="Детали события" style={{ position: 'relative', background: 'var(--bg-1)', borderTop: '1px solid var(--bg-6)', maxHeight: '82%', display: 'flex', flexDirection: 'column', animation: 'fbSheetUp var(--dur-slow) var(--ease-spring)', paddingBottom: 24 }}>
          <div style={{ display: 'flex', justifyContent: 'center', padding: '8px 0 4px' }}><div style={{ width: 36, height: 4, borderRadius: 999, background: 'var(--bg-6)' }} /></div>
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', padding: '8px 16px 14px', borderBottom: '1px solid var(--bg-5)' }}>
            <div style={{ minWidth: 0 }}>
              <Eyebrow num="СОБЫТИЕ">{event.time}</Eyebrow>
              <div className="mono" style={{ fontSize: 14, color: 'var(--bg-11)', marginTop: 6, lineHeight: 1.3 }}>{d.ad}</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8 }}><FsmBadge state={d.state} sm /><span className="codechip">{d.offer}</span></div>
            </div>
            <button onClick={onClose} aria-label="Закрыть" className="btn btn-ghost btn-icon" style={{ width: 44, height: 44 }}><Icon name="x" size={20} /></button>
          </div>
          <div style={{ flex: 1, overflowY: 'auto', padding: 16, display: 'flex', flexDirection: 'column', gap: 18 }}>
            <div style={{ background: 'var(--danger-bg)', border: '1px solid color-mix(in srgb, var(--danger) 30%, transparent)', borderLeft: '2px solid var(--danger)', padding: 12 }}>
              <span className="rulepill" style={{ color: 'var(--danger)', background: 'transparent', border: '1px solid color-mix(in srgb, var(--danger) 40%, transparent)' }}>{d.rule.code}</span>
              <div style={{ fontSize: 13, color: 'var(--bg-11)', lineHeight: 1.5, marginTop: 8 }}>{d.rule.detail}</div>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', border: '1px solid var(--bg-5)' }}>
              {d.metrics.slice(0, 6).map((m, i) => (
                <div key={m.k} style={{ padding: '10px 12px', borderRight: i % 2 === 0 ? '1px solid var(--bg-5)' : 'none', borderTop: i >= 2 ? '1px solid var(--bg-5)' : 'none', background: m.flag ? 'var(--danger-bg)' : 'transparent' }}>
                  <div className="eyebrow" style={{ fontSize: 9, color: m.flag ? 'var(--danger)' : 'var(--bg-9)' }}>{m.k}</div>
                  <div className="mono tnum" style={{ fontSize: 16, color: m.flag ? 'var(--danger)' : 'var(--bg-11)', marginTop: 4 }}>{m.v}</div>
                </div>
              ))}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 10, padding: '12px 16px 0' }}>
            <button className="btn btn-secondary" style={{ flex: 1, height: 44 }}><Icon name="snooze" size={16} />Snooze</button>
            <button className="btn btn-danger" style={{ flex: 1, height: 44 }}><Icon name="stop" size={16} />Disable</button>
          </div>
        </div>
      </div>
    );
  }

  function MiniEmpty({ icon, title, sub }) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8, padding: '32px 24px', textAlign: 'center' }}>
        <span style={{ color: 'var(--bg-7)' }}><Icon name={icon} size={28} stroke={1.3} /></span>
        <div style={{ fontSize: 14, color: 'var(--bg-11)', fontWeight: 500 }}>{title}</div>
        <div style={{ fontSize: 12, color: 'var(--bg-9)', lineHeight: 1.5 }}>{sub}</div>
      </div>
    );
  }

  function MiniDashboard({ scenario, accent, onToast, scanOn = true, onEnable }) {
    const D = window.DATA;
    const live = scenario === 'live';
    const [sheet, setSheet] = React.useState(null);
    const kpi = live ? D.kpi.live : D.kpi.calm;
    const spend = live ? D.spend.live : D.spend.calm;
    const dis = live ? D.disableTasks.live : D.disableTasks.calm;
    const en = live ? D.enableTasks.live : D.enableTasks.calm;
    const total = kpi[0].value, warn = kpi[1].value, stop = kpi[2].value;

    return (
      <div className="fb-scope" style={{ position: 'relative', height: '100%', display: 'flex', flexDirection: 'column', background: 'var(--bg-0)', paddingTop: 50, overflow: 'hidden' }}>
        <MiniScanHeader onToast={onToast} scanOn={scanOn} onEnable={onEnable} />
        <div style={{ flex: 1, overflowY: 'auto', position: 'relative' }}>
          <BlueprintBG ticks={false} />
          <div style={{ position: 'relative', padding: 16, display: 'flex', flexDirection: 'column', gap: 20 }}>
            {!scanOn && <PausedBanner since="14:32" onEnable={onEnable} />}
            {/* hero */}
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginBottom: 10 }}>
                <PulseDot size={9} color={live ? 'var(--warning)' : 'var(--success)'} />
                <span className="eyebrow" style={{ color: live ? 'var(--warning)' : 'var(--success)' }}>{live ? 'ТРЕБУЕТ ВНИМАНИЯ' : 'СИСТЕМА В НОРМЕ'}</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 16 }}>
                <CountUp value={total} style={{ fontSize: 64, fontWeight: 500, lineHeight: 0.82, color: 'var(--bg-11)', letterSpacing: '-0.03em' }} />
                <span style={{ fontSize: 14, color: 'var(--bg-10)', maxWidth: 130, lineHeight: 1.3 }}>объявлений под контролем</span>
              </div>
              <HealthBar normal={total} warning={warn} stop={stop} compact />
            </div>

            {/* spend chart */}
            <div className="card" style={{ padding: 14 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 10 }}>
                <Eyebrow>SPEND × ЧАС · 24Ч</Eyebrow>
                <span className="mono tnum" style={{ fontSize: 15, color: 'var(--bg-11)' }}>${spend.reduce((a, b) => a + b, 0).toLocaleString('en-US')}</span>
              </div>
              <SpendChart data={spend} height={120} accent={accent} animate live={live} />
            </div>

            {/* KPI 2x2 */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', border: '1px solid var(--bg-5)' }}>
              {kpi.map((d, i) => (
                <div key={d.key} style={{ padding: 14, borderRight: i % 2 === 0 ? '1px solid var(--bg-5)' : 'none', borderTop: i >= 2 ? '1px solid var(--bg-5)' : 'none' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                    <Eyebrow>{d.eyebrow}</Eyebrow><Trend value={d.trend} pct={d.trendPct} tone={d.tone} />
                  </div>
                  <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 6 }}>
                    <CountUp value={d.value} style={{ fontSize: 28, fontWeight: 500, color: TONE[d.tone], lineHeight: 0.9 }} />
                    <Sparkline data={SPARK[d.key]} color={TONE[d.tone]} w={52} h={22} fill />
                  </div>
                </div>
              ))}
            </div>

            {/* live-tail feed */}
            <div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                <Eyebrow num="02">СОБЫТИЯ ПО ОБЪЯВЛЕНИЯМ</Eyebrow>
                <span style={{ fontSize: 11, color: 'var(--bg-9)', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <PulseDot size={6} color={!scanOn ? 'var(--warning)' : live ? 'var(--success)' : 'var(--bg-7)'} />{!scanOn ? 'пауза' : live ? 'live' : 'тихо'}
                </span>
              </div>
              <div className="card">
                {live
                  ? <LiveFeed rows={D.events.live.slice(0, 4)} max={6} live={scanOn} onRow={(d) => setSheet(d)} />
                  : <MiniEmpty icon="activity" title="Алертов за 24ч нет" sub="Правила работают, трафик ровный" />}
              </div>
            </div>

            {/* task queues */}
            <div>
              <Eyebrow num="03" style={{ marginBottom: 10, display: 'flex' }}>ОЧЕРЕДЬ ЗАДАЧ</Eyebrow>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                {[{ t: 'DISABLE QUEUE', rows: dis, c: 'var(--danger)' }, { t: 'ENABLE QUEUE', rows: en, c: 'var(--success)' }].map((q) => (
                  <div key={q.t} className="card">
                    <div className="card-hd" style={{ padding: '10px 14px' }}><Eyebrow>{q.t}</Eyebrow><span className="mono tnum" style={{ fontSize: 13, color: q.rows.length ? q.c : 'var(--bg-8)' }}>{q.rows.length}</span></div>
                    {q.rows.length === 0 ? <MiniEmpty icon="inbox" title="Очередь пуста" sub="Нет задач в работе" /> : <div>{q.rows.map((d) => <TaskRow key={d.id} d={d} />)}</div>}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
        <TabBar onNav={(id) => { if (id !== 'dashboard') onToast(`«${id}» — в следующей итерации`); }} />
        {sheet && <MiniSheet event={sheet} onClose={() => setSheet(null)} />}
      </div>
    );
  }

  window.MiniDashboard = MiniDashboard;
  window.MiniTabBar = TabBar;
})();
