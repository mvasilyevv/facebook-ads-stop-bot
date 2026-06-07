// web-dashboard.jsx — full live desktop dashboard (1280px+). window.WebDashboard
(function () {
  const { Icon, Eyebrow, SpendChart, FsmBadge, RulePills, TaskRow, PulseDot, Trend,
    BlueprintBG, HealthBar, CountUp, Sparkline, SPARK, TONE, LiveFeed, WorkerStatus, useScan, CountdownRing, PausedRing, PausedBanner } = window;

  const NAV = [
    { group: '01', label: 'OPERATE', items: [
      { id: 'dashboard', label: 'Панель', icon: 'dashboard' },
      { id: 'ads', label: 'Объявления', icon: 'ads', badge: 16 },
      { id: 'drafts', label: 'Черновики', icon: 'drafts', badge: 7 },
    ] },
    { group: '02', label: 'CATALOG', items: [{ id: 'offers', label: 'Офферы', icon: 'offers' }] },
    { group: '03', label: 'HISTORY', items: [{ id: 'history', label: 'История', icon: 'history' }] },
    { group: '04', label: 'SYSTEM', items: [{ id: 'settings', label: 'Настройки', icon: 'settings' }] },
  ];

  const HREFS = { dashboard: encodeURI('FB Stop Bot — Dashboard.html'), ads: encodeURI('FB Stop Bot — Ads.html') };

  function Sidebar({ collapsed, onToggle, onNav, active = 'dashboard' }) {
    return (
      <aside style={{ width: collapsed ? 64 : 240, flex: 'none', borderRight: '1px solid var(--bg-5)', background: 'var(--bg-0)', display: 'flex', flexDirection: 'column', transition: 'width var(--dur-base) var(--ease-out)', overflow: 'hidden' }}>
        <div style={{ height: 56, display: 'flex', alignItems: 'center', gap: 10, padding: collapsed ? 0 : '0 20px', justifyContent: collapsed ? 'center' : 'flex-start', borderBottom: '1px solid var(--bg-5)' }}>
          <div style={{ width: 26, height: 26, background: 'var(--accent)', display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 'none' }}>
            <span className="mono" style={{ fontSize: 14, fontWeight: 700, color: 'var(--bg-0)' }}>FB</span>
          </div>
          {!collapsed && (
            <div style={{ minWidth: 0 }}>
              <div className="mono" style={{ fontSize: 13, fontWeight: 600, color: 'var(--bg-11)', lineHeight: 1.1, whiteSpace: 'nowrap' }}>STOP BOT</div>
              <div style={{ fontSize: 10, color: 'var(--bg-9)', letterSpacing: '0.04em' }}>operator</div>
            </div>
          )}
        </div>
        <nav style={{ flex: 1, overflowY: 'auto', padding: '12px 0' }}>
          {NAV.map((g) => (
            <div key={g.group} style={{ marginBottom: 14 }}>
              {!collapsed
                ? <div className="eyebrow" style={{ padding: '0 20px', marginBottom: 8, fontSize: 9 }}><span className="num">{g.group}</span> {g.label}</div>
                : <div style={{ height: 1, background: 'var(--bg-5)', margin: '0 16px 8px' }} />}
              {g.items.map((it) => {
                const on = it.id === active;
                const href = HREFS[it.id];
                const itemStyle = { position: 'relative', display: 'flex', alignItems: 'center', gap: 11, height: 36, width: '100%', boxSizing: 'border-box', padding: collapsed ? 0 : '0 20px', justifyContent: collapsed ? 'center' : 'flex-start', background: on ? 'var(--bg-2)' : 'transparent', border: 'none', color: on ? 'var(--accent)' : 'var(--bg-10)', cursor: 'pointer', font: 'inherit', fontSize: 13, textDecoration: 'none', transition: 'background var(--dur-fast), color var(--dur-fast)' };
                const enter = (e) => { if (!on) e.currentTarget.style.background = 'var(--bg-1)'; };
                const leave = (e) => { if (!on) e.currentTarget.style.background = 'transparent'; };
                const inner = (<>
                  {on && <span style={{ position: 'absolute', left: 0, top: 8, bottom: 8, width: 3, background: 'var(--accent)' }} />}
                  <Icon name={it.icon} size={18} stroke={1.6} />
                  {!collapsed && <span style={{ flex: 1, textAlign: 'left' }}>{it.label}</span>}
                  {!collapsed && it.badge && <span className="mono tnum" style={{ fontSize: 11, color: on ? 'var(--accent)' : 'var(--bg-9)' }}>{it.badge}</span>}
                </>);
                return href
                  ? <a key={it.id} href={href} title={it.label} aria-current={on ? 'page' : undefined} style={itemStyle} onMouseEnter={enter} onMouseLeave={leave}>{inner}</a>
                  : <button key={it.id} onClick={() => onNav(it.id)} title={it.label} style={itemStyle} onMouseEnter={enter} onMouseLeave={leave}>{inner}</button>;
              })}
            </div>
          ))}
        </nav>
        <div style={{ borderTop: '1px solid var(--bg-5)', padding: collapsed ? '12px 0' : '12px 16px', display: 'flex', alignItems: 'center', justifyContent: collapsed ? 'center' : 'flex-end' }}>
          <button onClick={onToggle} aria-label={collapsed ? 'Развернуть меню' : 'Свернуть меню'} className="btn btn-ghost btn-icon btn-sm" style={{ width: 28, height: 28 }}><Icon name="panel" size={16} /></button>
        </div>
      </aside>
    );
  }

  function Topbar({ crumb = 'Панель' }) {
    return (
      <header style={{ height: 56, flex: 'none', borderBottom: '1px solid var(--bg-5)', background: 'var(--bg-0)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 var(--s-8)', position: 'relative', zIndex: 20 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--bg-9)', whiteSpace: 'nowrap', flex: 'none' }} className="mono">
          <span>FB Stop Bot</span><span style={{ color: 'var(--bg-7)' }}>/</span><span style={{ color: 'var(--bg-11)' }}>{crumb}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--s-4)' }}>
          <button className="btn btn-secondary btn-sm" style={{ color: 'var(--bg-9)', gap: 8 }}>
            <Icon name="search" size={14} /><span>Поиск</span>
            <kbd style={{ fontFamily: 'var(--font-num)', fontSize: 11, color: 'var(--bg-8)', border: '1px solid var(--bg-6)', borderRadius: 2, padding: '0 4px', marginLeft: 6 }}>⌘K</kbd>
          </button>
          <WorkerStatus />
          <div style={{ width: 1, height: 22, background: 'var(--bg-5)' }} />
          <button aria-label="Уведомления" className="btn btn-ghost btn-icon"><Icon name="bell" size={17} /></button>
          <button aria-label="Профиль" className="btn btn-secondary btn-icon" style={{ borderRadius: 'var(--r-full)', width: 30, height: 30 }}>
            <span className="mono" style={{ fontSize: 12, color: 'var(--bg-11)' }}>MV</span>
          </button>
        </div>
      </header>
    );
  }

  function ScanHeaderControl({ onToast, scanOn, onEnable }) {
    const { scanning, age, next, interval, doScan } = useScan(30, 14, scanOn);
    if (!scanOn) {
      return (
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--s-4)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <PausedRing />
            <div className="eyebrow" style={{ fontSize: 9, color: 'var(--warning)' }}>СКАН ВЫКЛЮЧЕН</div>
          </div>
          <div style={{ width: 1, height: 28, background: 'var(--bg-5)' }} />
          <div style={{ lineHeight: 1.3 }}>
            <div className="eyebrow" style={{ fontSize: 9 }}>ПОСЛЕДНИЙ СКАН</div>
            <div className="mono tnum" style={{ fontSize: 13, color: 'var(--bg-10)', whiteSpace: 'nowrap' }}>{age}с назад <span style={{ color: 'var(--bg-7)' }}>·</span> <span style={{ color: 'var(--warning)' }}>стоп</span></div>
          </div>
          <button className="btn btn-primary" onClick={onEnable} style={{ marginLeft: 4 }}>
            <Icon name="play" size={14} />Включить
          </button>
        </div>
      );
    }
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--s-4)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <CountdownRing value={scanning ? interval : next} max={interval} active={scanning} />
          <div className="eyebrow" style={{ fontSize: 9, color: scanning ? 'var(--accent)' : 'var(--bg-9)' }}>{scanning ? 'ИДЁТ\u00A0СКАН' : 'СЛЕД.\u00A0СКАН'}</div>
        </div>
        <div style={{ width: 1, height: 28, background: 'var(--bg-5)' }} />
        <div style={{ lineHeight: 1.3 }}>
          <div className="eyebrow" style={{ fontSize: 9 }}>ПОСЛЕДНИЙ СКАН</div>
          <div className="mono tnum" style={{ fontSize: 13, color: 'var(--bg-10)', whiteSpace: 'nowrap' }}>{scanning ? 'сканирую…' : `${age}с назад`}</div>
        </div>
        <button className="btn btn-primary" onClick={() => { if (!scanning) { doScan(); onToast && onToast('Observer · scan-now запущен'); } }} disabled={scanning} style={{ opacity: scanning ? 0.7 : 1, marginLeft: 4 }}>
          <Icon name="refresh" size={16} stroke={1.8} style={scanning ? { animation: 'fbSpin 1s linear infinite' } : undefined} />{scanning ? 'Сканирую' : 'Сканировать'}
        </button>
      </div>
    );
  }

  function EmptyState({ icon, title, sub }) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 10, padding: '40px 24px', textAlign: 'center' }}>
        <span style={{ color: 'var(--bg-7)' }}><Icon name={icon} size={32} stroke={1.3} /></span>
        <div style={{ fontSize: 14, color: 'var(--bg-11)', fontWeight: 500 }}>{title}</div>
        <div style={{ fontSize: 13, color: 'var(--bg-9)', maxWidth: 300, lineHeight: 1.5 }}>{sub}</div>
      </div>
    );
  }

  function EventDrawer({ event, onClose }) {
    React.useEffect(() => {
      const onKey = (e) => { if (e.key === 'Escape') onClose(); };
      window.addEventListener('keydown', onKey);
      return () => window.removeEventListener('keydown', onKey);
    }, [onClose]);
    const detail = window.DATA.eventDetail.e1;
    return (
      <>
        <div onClick={onClose} style={{ position: 'absolute', inset: 0, background: 'rgba(10,10,11,0.66)', zIndex: 40, animation: 'fbFade var(--dur-base) var(--ease-out)' }} />
        <div role="dialog" aria-label="Детали события" style={{ position: 'absolute', top: 0, right: 0, bottom: 0, width: 480, background: 'var(--bg-1)', borderLeft: '1px solid var(--bg-6)', zIndex: 41, display: 'flex', flexDirection: 'column', animation: 'fbSlideIn var(--dur-slow) var(--ease-spring)' }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', padding: 'var(--s-5)', borderBottom: '1px solid var(--bg-5)' }}>
            <div style={{ minWidth: 0 }}>
              <Eyebrow num="СОБЫТИЕ">{event.time}</Eyebrow>
              <div className="mono" style={{ fontSize: 15, color: 'var(--bg-11)', marginTop: 8, lineHeight: 1.3 }}>{detail.ad}</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 10 }}>
                <FsmBadge state={detail.state} sm /><span className="codechip">{detail.offer}</span>
                <span style={{ fontSize: 11, color: 'var(--bg-9)' }}>{detail.cabinet}</span>
              </div>
            </div>
            <button onClick={onClose} aria-label="Закрыть" className="btn btn-ghost btn-icon"><Icon name="x" size={18} /></button>
          </div>
          <div style={{ flex: 1, overflowY: 'auto', padding: 'var(--s-5)', display: 'flex', flexDirection: 'column', gap: 'var(--s-6)' }}>
            <div style={{ fontSize: 11, fontFamily: 'var(--font-num)', color: 'var(--bg-8)' }}>ad_id {detail.ad_id}</div>
            <div style={{ background: 'var(--danger-bg)', border: '1px solid color-mix(in srgb, var(--danger) 30%, transparent)', borderLeft: '2px solid var(--danger)', padding: 'var(--s-4)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <span className="rulepill" style={{ color: 'var(--danger)', background: 'transparent', border: '1px solid color-mix(in srgb, var(--danger) 40%, transparent)' }}>{detail.rule.code}</span>
                <span style={{ fontSize: 11, color: 'var(--bg-9)' }}>окно {detail.rule.window}</span>
              </div>
              <div style={{ fontSize: 13, color: 'var(--bg-11)', lineHeight: 1.5 }}>{detail.rule.detail}</div>
            </div>
            <div>
              <Eyebrow style={{ marginBottom: 12 }}>МЕТРИКИ · СНИМОК</Eyebrow>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', border: '1px solid var(--bg-5)' }}>
                {detail.metrics.map((m, i) => (
                  <div key={m.k} style={{ padding: '10px 12px', borderRight: (i % 4 !== 3) ? '1px solid var(--bg-5)' : 'none', borderTop: i >= 4 ? '1px solid var(--bg-5)' : 'none', background: m.flag ? 'var(--danger-bg)' : 'transparent' }}>
                    <div className="eyebrow" style={{ fontSize: 9, color: m.flag ? 'var(--danger)' : 'var(--bg-9)' }}>{m.k}</div>
                    <div className="mono tnum" style={{ fontSize: 15, color: m.flag ? 'var(--danger)' : 'var(--bg-11)', marginTop: 4 }}>{m.v}</div>
                  </div>
                ))}
              </div>
            </div>
            <div>
              <Eyebrow style={{ marginBottom: 12 }}>ТАЙМЛАЙН АЛЕРТА</Eyebrow>
              {detail.timeline.map((t, i) => {
                const c = { warning: 'var(--fsm-warning)', stop: 'var(--fsm-stop)', normal: 'var(--fsm-normal)' }[t.stage];
                return (
                  <div key={i} style={{ display: 'grid', gridTemplateColumns: 'auto auto 1fr', gap: 12, alignItems: 'start', paddingBottom: i < detail.timeline.length - 1 ? 16 : 0 }}>
                    <span className="mono tnum" style={{ fontSize: 12, color: 'var(--bg-9)', paddingTop: 1 }}>{t.t}</span>
                    <span style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', alignSelf: 'stretch' }}>
                      <span style={{ width: 8, height: 8, borderRadius: 999, background: c, flex: 'none', marginTop: 4 }} />
                      {i < detail.timeline.length - 1 && <span style={{ width: 1, flex: 1, background: 'var(--bg-6)', marginTop: 3 }} />}
                    </span>
                    <span style={{ fontSize: 13, color: 'var(--bg-10)', paddingTop: 1 }}>{t.label}</span>
                  </div>
                );
              })}
            </div>
          </div>
          <div style={{ borderTop: '1px solid var(--bg-5)', padding: 'var(--s-4) var(--s-5)', display: 'flex', gap: 'var(--s-3)' }}>
            <button className="btn btn-secondary" style={{ flex: 1 }}><Icon name="snooze" size={15} />Snooze 1ч</button>
            <button className="btn btn-danger" style={{ flex: 1 }}><Icon name="stop" size={15} />Disable</button>
          </div>
        </div>
      </>
    );
  }

  function WebDashboard({ collapsed, setCollapsed, scenario, accent, onToast, scanOn = true, onEnable }) {
    const D = window.DATA;
    const live = scenario === 'live';
    const [drawer, setDrawer] = React.useState(null);
    const kpi = live ? D.kpi.live : D.kpi.calm;
    const spend = live ? D.spend.live : D.spend.calm;
    const dis = live ? D.disableTasks.live : D.disableTasks.calm;
    const en = live ? D.enableTasks.live : D.enableTasks.calm;
    const total = kpi[0].value, warn = kpi[1].value, stop = kpi[2].value;
    const spendTotal = spend.reduce((a, b) => a + b, 0);

    return (
      <div className="fb-scope" style={{ display: 'flex', height: '100%', background: 'var(--bg-0)', position: 'relative', overflow: 'hidden' }}>
        <Sidebar collapsed={collapsed} onToggle={() => setCollapsed(!collapsed)} onNav={(id) => { if (id !== 'dashboard') onToast(`Экран «${id}» — в следующей итерации`); }} />
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
          <Topbar />
          <main style={{ flex: 1, overflowY: 'auto', padding: 'var(--s-6) var(--s-8)', position: 'relative' }}>
            <BlueprintBG ticks={false} />
            <div style={{ position: 'relative' }}>
              {/* page header */}
              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 'var(--s-6)', gap: 24, flexWrap: 'wrap' }}>
                <div>
                  <Eyebrow num="01">ОБЗОР · ПО ОБЪЯВЛЕНИЯМ · {scanOn ? 'LIVE' : 'ПАУЗА'}</Eyebrow>
                  <h1 className="mono" style={{ fontSize: 30, fontWeight: 500, color: 'var(--bg-11)', margin: '8px 0 0', letterSpacing: '-0.02em' }}>Панель</h1>
                </div>
                <ScanHeaderControl onToast={onToast} scanOn={scanOn} onEnable={onEnable} />
              </div>

              {!scanOn && <div style={{ marginBottom: 'var(--s-6)' }}><PausedBanner since="14:32" onEnable={onEnable} /></div>}

              {/* hero + chart */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.1fr', gap: 'var(--s-8)', alignItems: 'center', paddingBottom: 'var(--s-6)', borderBottom: '1px solid var(--bg-5)', marginBottom: 'var(--s-6)' }}>
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 11, marginBottom: 12 }}>
                    <PulseDot size={10} color={live ? 'var(--warning)' : 'var(--success)'} />
                    <span className="eyebrow" style={{ color: live ? 'var(--warning)' : 'var(--success)' }}>{live ? 'ТРЕБУЕТ ВНИМАНИЯ' : 'СИСТЕМА В НОРМЕ'}</span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, marginBottom: 22 }}>
                    <CountUp value={total} style={{ fontSize: 88, fontWeight: 500, lineHeight: 0.82, color: 'var(--bg-11)', letterSpacing: '-0.04em' }} />
                    <span style={{ fontSize: 16, color: 'var(--bg-10)', maxWidth: 160, lineHeight: 1.3 }}>объявлений под контролем</span>
                  </div>
                  <HealthBar normal={total} warning={warn} stop={stop} />
                </div>
                <div className="card" style={{ padding: 'var(--s-5)' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 12 }}>
                    <Eyebrow>SPEND × ЧАС · 24Ч</Eyebrow>
                    <span className="mono tnum" style={{ fontSize: 18, color: 'var(--bg-11)' }}>${spendTotal.toLocaleString('en-US')}</span>
                  </div>
                  <SpendChart data={spend} height={170} accent={accent} animate live={live} />
                </div>
              </div>

              {/* sparkline KPI row */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', border: '1px solid var(--bg-5)', marginBottom: 'var(--s-8)' }}>
                {kpi.map((d, i) => (
                  <div key={d.key} style={{ padding: 'var(--s-5)', borderRight: i < 3 ? '1px solid var(--bg-5)' : 'none', display: 'flex', flexDirection: 'column', gap: 12 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <Eyebrow>{d.eyebrow}</Eyebrow><Trend value={d.trend} pct={d.trendPct} tone={d.tone} />
                    </div>
                    <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 8 }}>
                      <CountUp value={d.value} style={{ fontSize: 34, fontWeight: 500, color: TONE[d.tone], lineHeight: 0.9 }} />
                      <Sparkline data={SPARK[d.key]} color={TONE[d.tone]} w={72} h={26} fill />
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--bg-9)' }}><span style={{ color: 'var(--bg-10)' }}>{d.label}</span> · {d.note}</div>
                  </div>
                ))}
              </div>

              {/* live-tail feed */}
              <div style={{ marginBottom: 'var(--s-8)' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 'var(--s-4)' }}>
                  <Eyebrow num="02">СОБЫТИЯ ПО ОБЪЯВЛЕНИЯМ · LIVE-TAIL</Eyebrow>
                  <span style={{ fontSize: 12, color: 'var(--bg-9)', display: 'inline-flex', alignItems: 'center', gap: 7 }}>
                    <PulseDot size={6} color={!scanOn ? 'var(--warning)' : live ? 'var(--success)' : 'var(--bg-7)'} />{!scanOn ? 'на паузе' : live ? 'поток активен' : 'тихо'}
                  </span>
                </div>
                <div className="card">
                  {live
                    ? <LiveFeed rows={D.events.live.slice(0, 5)} max={8} live={scanOn} onRow={(d) => setDrawer(d)} />
                    : <EmptyState icon="activity" title="Алертов за 24ч нет" sub="Что приятно — значит правила работают, а трафик льётся ровно" />}
                </div>
              </div>

              {/* task queues */}
              <div>
                <Eyebrow num="03" style={{ marginBottom: 'var(--s-4)', display: 'flex' }}>ОЧЕРЕДЬ ЗАДАЧ</Eyebrow>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--s-4)' }}>
                  {[{ t: 'DISABLE QUEUE', rows: dis, c: 'var(--danger)' }, { t: 'ENABLE QUEUE', rows: en, c: 'var(--success)' }].map((q) => (
                    <div key={q.t} className="card">
                      <div className="card-hd"><Eyebrow>{q.t}</Eyebrow><span className="mono tnum" style={{ fontSize: 13, color: q.rows.length ? q.c : 'var(--bg-8)' }}>{q.rows.length}</span></div>
                      {q.rows.length === 0 ? <EmptyState icon="inbox" title="Очередь пуста" sub="Нет задач в работе" /> : <div>{q.rows.map((d) => <TaskRow key={d.id} d={d} />)}</div>}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </main>
        </div>
        {drawer && <EventDrawer event={drawer} onClose={() => setDrawer(null)} />}
      </div>
    );
  }

  window.WebDashboard = WebDashboard;
  window.FBSidebar = Sidebar;
  window.FBTopbar = Topbar;
})();
