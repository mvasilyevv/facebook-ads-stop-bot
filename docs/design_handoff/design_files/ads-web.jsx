// ads-web.jsx — Ads workhorse (desktop): filter bar + virtual table + bulk bar + drawer. window.WebAds
(function () {
  const { Icon, Eyebrow, FsmBadge, RulePills, Sparkline, PulseDot, FBSidebar, FBTopbar, FSM } = window;

  const COLS = '40px 1fr 64px 130px 96px 74px 62px 62px 62px 66px 40px';
  const STATE_FILTERS = [
    { id: 'normal', label: 'Норма' }, { id: 'warning', label: 'Предупреждение' },
    { id: 'stop', label: 'Стоп' }, { id: 'claimed', label: 'В работе' }, { id: 'disabled', label: 'Отключено' },
  ];
  const money = (v) => '$' + v.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const ROWH = { comfortable: 44, compact: 34, dense: 28 };

  function Num({ v, flag, suff = '', pre = '', muted }) {
    return <div className="mono tnum" style={{ textAlign: 'right', fontSize: 'var(--row-fs)', color: flag ? 'var(--danger)' : muted ? 'var(--bg-9)' : 'var(--bg-11)', padding: '0 8px', alignSelf: 'center' }}>{v === 0 || v == null ? '—' : pre + v + suff}</div>;
  }

  function Thumb({ a }) {
    return (
      <div style={{ width: 40, height: 24, background: 'var(--bg-2)', border: '1px solid var(--bg-6)', flex: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden' }}>
        <span className="mono" style={{ fontSize: 8, color: 'var(--bg-8)', letterSpacing: '0.02em' }}>{a.geo}</span>
      </div>
    );
  }

  function FilterBar({ q, setQ, states, toggleState, offers, toggleOffer, count, searchRef, density }) {
    const [offOpen, setOffOpen] = React.useState(false);
    return (
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--s-3)', marginBottom: 'var(--s-3)' }}>
          <div style={{ position: 'relative', flex: 1, maxWidth: 360 }}>
            <span style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--bg-9)', pointerEvents: 'none' }}><Icon name="search" size={15} /></span>
            <input ref={searchRef} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Поиск по названию / ad_id / offer"
              style={{ width: '100%', height: 32, background: 'var(--bg-2)', border: '1px solid var(--bg-6)', borderRadius: 'var(--r-1)', color: 'var(--bg-11)', font: 'inherit', fontSize: 13, padding: '0 10px 0 32px', outline: 'none' }} />
            <kbd style={{ position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)', fontFamily: 'var(--font-num)', fontSize: 11, color: 'var(--bg-8)', border: '1px solid var(--bg-6)', borderRadius: 2, padding: '0 4px' }}>/</kbd>
          </div>
          {/* state pills */}
          <div style={{ display: 'flex', gap: 6 }}>
            {STATE_FILTERS.map((s) => {
              const on = states.includes(s.id);
              return (
                <button key={s.id} onClick={() => toggleState(s.id)}
                  style={{ height: 30, padding: '0 12px', borderRadius: 'var(--r-full)', border: `1px solid ${on ? 'var(--accent)' : 'var(--bg-6)'}`, background: on ? 'var(--accent-bg)' : 'transparent', color: on ? 'var(--accent)' : 'var(--bg-10)', font: 'inherit', fontSize: 12, fontWeight: 500, cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ width: 7, height: 7, borderRadius: 999, background: `var(--fsm-${s.id})` }} />{s.label}
                </button>
              );
            })}
          </div>
          <div style={{ position: 'relative' }}>
            <button onClick={() => setOffOpen((o) => !o)} className="btn btn-secondary btn-sm" style={{ gap: 6 }}>
              <Icon name="filter" size={13} />offer{offers.length ? ` · ${offers.length}` : ''}<Icon name="chevD" size={12} />
            </button>
            {offOpen && (
              <div style={{ position: 'absolute', top: 'calc(100% + 6px)', left: 0, zIndex: 30, background: 'var(--bg-3)', border: '1px solid var(--bg-6)', padding: 6, width: 150, display: 'flex', flexDirection: 'column', gap: 2 }}>
                {window.AD_OFFERS.map((o) => {
                  const on = offers.includes(o);
                  return (
                    <button key={o} onClick={() => toggleOffer(o)} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px', background: on ? 'var(--bg-4)' : 'transparent', border: 'none', color: 'var(--bg-11)', font: 'inherit', fontSize: 13, cursor: 'pointer', textAlign: 'left' }}>
                      <span style={{ width: 14, height: 14, border: `1px solid ${on ? 'var(--accent)' : 'var(--bg-7)'}`, background: on ? 'var(--accent)' : 'transparent', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{on && <Icon name="check" size={11} color="var(--bg-0)" stroke={3} />}</span>
                      <span className="mono">{o}</span>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
          <div style={{ flex: 1 }} />
          <span className="mono tnum" style={{ fontSize: 12, color: 'var(--bg-9)' }}>{count.toLocaleString('en-US')} объявлений</span>
        </div>
        {/* active chips */}
        {(states.length > 0 || offers.length > 0) && (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 'var(--s-3)' }}>
            {states.map((s) => (
              <span key={s} className="badge" style={{ background: 'var(--bg-2)', border: '1px solid var(--bg-6)', color: 'var(--bg-10)', cursor: 'pointer' }} onClick={() => toggleState(s)}>
                state = {FSM[s].label}<Icon name="x" size={12} />
              </span>
            ))}
            {offers.map((o) => (
              <span key={o} className="badge" style={{ background: 'var(--bg-2)', border: '1px solid var(--bg-6)', color: 'var(--bg-10)', cursor: 'pointer' }} onClick={() => toggleOffer(o)}>
                offer = {o}<Icon name="x" size={12} />
              </span>
            ))}
          </div>
        )}
      </div>
    );
  }

  function AdTable({ rows, selected, toggleSel, onOpen, cursor, rowH }) {
    const ref = React.useRef(null);
    const [scrollTop, setScrollTop] = React.useState(0);
    const [vh, setVh] = React.useState(560);
    React.useEffect(() => {
      if (!ref.current) return;
      const ro = new ResizeObserver((e) => setVh(e[0].contentRect.height));
      ro.observe(ref.current);
      return () => ro.disconnect();
    }, []);
    const total = rows.length;
    const overscan = 6;
    const start = Math.max(0, Math.floor(scrollTop / rowH) - overscan);
    const visible = Math.ceil(vh / rowH) + overscan * 2;
    const end = Math.min(total, start + visible);
    const slice = rows.slice(start, end);
    return (
      <div style={{ border: '1px solid var(--bg-6)', display: 'flex', flexDirection: 'column', minHeight: 0, flex: 1 }}>
        {/* header */}
        <div style={{ display: 'grid', gridTemplateColumns: COLS, background: 'var(--bg-2)', borderBottom: '1px solid var(--bg-6)', height: 32, alignItems: 'center', flex: 'none' }}>
          <span />
          <span className="eyebrow" style={{ paddingLeft: 4 }}>AD</span>
          <span className="eyebrow">OFFER</span>
          <span className="eyebrow">STATE</span>
          {['SPEND', 'CPL', 'FREQ', 'CPM', 'CTR', 'ROAS'].map((h) => <span key={h} className="eyebrow" style={{ textAlign: 'right', paddingRight: 8 }}>{h}</span>)}
          <span />
        </div>
        {/* viewport */}
        <div ref={ref} onScroll={(e) => setScrollTop(e.target.scrollTop)} style={{ flex: 1, overflowY: 'auto', position: 'relative', minHeight: 0 }}>
          <div style={{ height: total * rowH, position: 'relative' }}>
            <div style={{ transform: `translateY(${start * rowH}px)` }}>
              {slice.map((a, i) => {
                const idx = start + i;
                const sel = selected.has(a.id);
                const cur = idx === cursor;
                return (
                  <div key={a.id} onClick={() => onOpen(a)} style={{
                    display: 'grid', gridTemplateColumns: COLS, height: rowH, alignItems: 'center', cursor: 'pointer',
                    borderBottom: '1px solid var(--bg-5)', background: sel ? 'var(--accent-bg)' : cur ? 'var(--bg-2)' : 'transparent',
                    borderLeft: sel ? '2px solid var(--accent)' : '2px solid transparent',
                  }}
                    onMouseEnter={(e) => { if (!sel && !cur) e.currentTarget.style.background = 'var(--bg-1)'; }}
                    onMouseLeave={(e) => { if (!sel && !cur) e.currentTarget.style.background = 'transparent'; }}>
                    <span onClick={(e) => { e.stopPropagation(); toggleSel(a.id); }} style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', cursor: 'pointer' }}>
                      <span style={{ width: 15, height: 15, border: `1px solid ${sel ? 'var(--accent)' : 'var(--bg-7)'}`, background: sel ? 'var(--accent)' : 'transparent', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{sel && <Icon name="check" size={11} color="var(--bg-0)" stroke={3} />}</span>
                    </span>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0, paddingLeft: 4 }}>
                      <Thumb a={a} />
                      <span className="mono" style={{ fontSize: 'var(--row-fs)', color: 'var(--bg-11)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.name}</span>
                      {a.rules.length > 0 && <span style={{ flex: 'none' }}><RulePills rules={a.rules.slice(0, 1)} /></span>}
                    </div>
                    <span style={{ alignSelf: 'center' }}><span className="codechip" style={{ height: 18, fontSize: 10 }}>{a.offer}</span></span>
                    <span style={{ alignSelf: 'center', paddingLeft: 2 }}><FsmBadge state={a.state} sm /></span>
                    <Num v={a.spend.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} pre="$" />
                    <Num v={a.cpl ? a.cpl.toFixed(1) : 0} flag={a.cpl > 30} pre="$" />
                    <Num v={a.freq.toFixed(1)} flag={a.freq > 4} />
                    <Num v={a.cpm.toFixed(1)} pre="$" muted />
                    <Num v={a.ctr.toFixed(1)} suff="%" muted />
                    <Num v={a.roas ? a.roas.toFixed(1) : 0} flag={a.roas && a.roas < 1} suff="×" />
                    <span style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--bg-8)' }} onClick={(e) => e.stopPropagation()}><Icon name="more" size={16} /></span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    );
  }

  function BulkBar({ n, onDisable, onSnooze, onClear }) {
    return (
      <div style={{ position: 'absolute', left: '50%', bottom: 24, transform: 'translateX(-50%)', zIndex: 35, display: 'flex', alignItems: 'center', gap: 'var(--s-4)', background: 'var(--bg-3)', border: '1px solid var(--bg-7)', padding: '10px 14px', boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.04)', animation: 'fbRise var(--dur-base) var(--ease-out)' }}>
        <span style={{ fontSize: 13, color: 'var(--bg-11)' }}><b className="mono tnum">{n}</b> выбрано</span>
        <div style={{ width: 1, height: 22, background: 'var(--bg-6)' }} />
        <button className="btn btn-danger btn-sm" onClick={onDisable}><Icon name="stop" size={14} />Disable</button>
        <button className="btn btn-secondary btn-sm" onClick={onSnooze}><Icon name="snooze" size={14} />Snooze</button>
        <button className="btn btn-ghost btn-sm" onClick={onClear}>Очистить</button>
      </div>
    );
  }

  function ConfirmDisable({ n, onCancel, onConfirm }) {
    const [val, setVal] = React.useState('');
    const ok = val.trim().toUpperCase() === 'DISABLE';
    React.useEffect(() => { const k = (e) => { if (e.key === 'Escape') onCancel(); }; window.addEventListener('keydown', k); return () => window.removeEventListener('keydown', k); }, [onCancel]);
    return (
      <>
        <div onClick={onCancel} style={{ position: 'absolute', inset: 0, background: 'rgba(10,10,11,0.7)', zIndex: 50, animation: 'fbFade var(--dur-base)' }} />
        <div role="dialog" aria-label="Подтверждение" style={{ position: 'absolute', left: '50%', top: '50%', transform: 'translate(-50%,-50%)', zIndex: 51, width: 440, background: 'var(--bg-1)', border: '1px solid var(--bg-6)', borderRadius: 'var(--r-2)' }}>
          <div style={{ padding: 'var(--s-5)', borderBottom: '1px solid var(--bg-5)' }}>
            <div style={{ fontSize: 16, color: 'var(--bg-11)', marginBottom: 6 }}>Отключить <b className="mono tnum">{n}</b> объявлений?</div>
            <div style={{ fontSize: 13, color: 'var(--bg-9)', lineHeight: 1.5 }}>Будет создано {n} disable-задач в outbox. Действие необратимо из этого экрана.</div>
          </div>
          <div style={{ padding: 'var(--s-5)' }}>
            <label style={{ fontSize: 12, color: 'var(--bg-9)', display: 'block', marginBottom: 8 }}>Введите <span className="mono" style={{ color: 'var(--danger)' }}>DISABLE</span> для подтверждения:</label>
            <input autoFocus value={val} onChange={(e) => setVal(e.target.value)} style={{ width: '100%', height: 36, background: 'var(--bg-2)', border: '1px solid var(--bg-6)', borderRadius: 'var(--r-1)', color: 'var(--bg-11)', font: 'inherit', fontFamily: 'var(--font-num)', fontSize: 14, padding: '0 10px', outline: 'none' }} />
          </div>
          <div style={{ padding: 'var(--s-4) var(--s-5)', borderTop: '1px solid var(--bg-5)', display: 'flex', justifyContent: 'flex-end', gap: 'var(--s-3)' }}>
            <button className="btn btn-ghost" onClick={onCancel}>Отмена</button>
            <button className="btn btn-danger" disabled={!ok} style={{ opacity: ok ? 1 : 0.5 }} onClick={() => ok && onConfirm()}>Отключить {n}</button>
          </div>
        </div>
      </>
    );
  }

  function AdDrawer({ ad, onClose }) {
    React.useEffect(() => { const k = (e) => { if (e.key === 'Escape') onClose(); }; window.addEventListener('keydown', k); return () => window.removeEventListener('keydown', k); }, [onClose]);
    const metrics = [
      { k: 'spend', v: money(ad.spend) }, { k: 'CPL', v: '$' + ad.cpl.toFixed(1), flag: ad.cpl > 30 },
      { k: 'CPM', v: '$' + ad.cpm.toFixed(1) }, { k: 'CTR', v: ad.ctr.toFixed(1) + '%' },
      { k: 'freq', v: ad.freq.toFixed(1), flag: ad.freq > 4 }, { k: 'ROAS', v: ad.roas.toFixed(1) + '×', flag: ad.roas < 1 },
      { k: 'leads', v: ad.leads }, { k: 'age', v: ad.ageMin + 'м' },
    ];
    return (
      <>
        <div onClick={onClose} style={{ position: 'absolute', inset: 0, background: 'rgba(10,10,11,0.66)', zIndex: 40, animation: 'fbFade var(--dur-base) var(--ease-out)' }} />
        <div role="dialog" aria-label="Объявление" style={{ position: 'absolute', top: 0, right: 0, bottom: 0, width: 560, background: 'var(--bg-1)', borderLeft: '1px solid var(--bg-6)', zIndex: 41, display: 'flex', flexDirection: 'column', animation: 'fbSlideIn var(--dur-slow) var(--ease-spring)' }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', padding: 'var(--s-5)', borderBottom: '1px solid var(--bg-5)' }}>
            <div style={{ minWidth: 0 }}>
              <Eyebrow num="ОБЪЯВЛЕНИЕ">{ad.geo} · {ad.city}</Eyebrow>
              <div className="mono" style={{ fontSize: 16, color: 'var(--bg-11)', margin: '8px 0', lineHeight: 1.3 }}>{ad.fullName}</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}><FsmBadge state={ad.state} sm /><span className="codechip">{ad.offer}</span><span style={{ fontSize: 11, color: 'var(--bg-8)', fontFamily: 'var(--font-num)' }}>{ad.ad_id}</span></div>
            </div>
            <button onClick={onClose} aria-label="Закрыть" className="btn btn-ghost btn-icon"><Icon name="x" size={18} /></button>
          </div>
          <div style={{ flex: 1, overflowY: 'auto', padding: 'var(--s-5)', display: 'flex', flexDirection: 'column', gap: 'var(--s-6)' }}>
            {ad.rules.length > 0 && (
              <div style={{ background: 'var(--danger-bg)', border: '1px solid color-mix(in srgb, var(--danger) 30%, transparent)', borderLeft: '2px solid var(--danger)', padding: 'var(--s-4)', display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                <span style={{ fontSize: 12, color: 'var(--bg-10)' }}>сработали:</span><RulePills rules={ad.rules} />
              </div>
            )}
            <div>
              <Eyebrow style={{ marginBottom: 12 }}>МЕТРИКИ · СНИМОК</Eyebrow>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', border: '1px solid var(--bg-5)' }}>
                {metrics.map((m, i) => (
                  <div key={m.k} style={{ padding: '10px 12px', borderRight: i % 4 !== 3 ? '1px solid var(--bg-5)' : 'none', borderTop: i >= 4 ? '1px solid var(--bg-5)' : 'none', background: m.flag ? 'var(--danger-bg)' : 'transparent' }}>
                    <div className="eyebrow" style={{ fontSize: 9, color: m.flag ? 'var(--danger)' : 'var(--bg-9)' }}>{m.k}</div>
                    <div className="mono tnum" style={{ fontSize: 15, color: m.flag ? 'var(--danger)' : 'var(--bg-11)', marginTop: 4 }}>{m.v}</div>
                  </div>
                ))}
              </div>
            </div>
            <div>
              <Eyebrow style={{ marginBottom: 12 }}>CPL · 8 ТОЧЕК</Eyebrow>
              <div className="card" style={{ padding: 'var(--s-4)' }}><Sparkline data={ad.spark} color={ad.cpl > 30 ? 'var(--danger)' : 'var(--accent)'} w={500} h={70} fill /></div>
            </div>
            <div>
              <Eyebrow style={{ marginBottom: 12 }}>ИСТОРИЯ ЗАДАЧ</Eyebrow>
              <div style={{ fontSize: 13, color: 'var(--bg-9)' }}>Задач по объявлению нет.</div>
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

  function WebAds({ collapsed, setCollapsed, density = 'comfortable', onToast }) {
    const [q, setQ] = React.useState('');
    const [states, setStates] = React.useState([]);
    const [offers, setOffers] = React.useState([]);
    const [selected, setSelected] = React.useState(() => new Set());
    const [drawer, setDrawer] = React.useState(null);
    const [confirm, setConfirm] = React.useState(false);
    const [cursor, setCursor] = React.useState(-1);
    const searchRef = React.useRef(null);
    const rowH = ROWH[density] || 36;

    const rows = React.useMemo(() => window.adFilter(window.ADS, { search: q, states, offers, sort: 'spend', dir: 'desc' }), [q, states, offers]);

    const toggleState = (s) => setStates((p) => p.includes(s) ? p.filter((x) => x !== s) : [...p, s]);
    const toggleOffer = (o) => setOffers((p) => p.includes(o) ? p.filter((x) => x !== o) : [...p, o]);
    const toggleSel = (id) => setSelected((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n; });
    const clearSel = () => setSelected(new Set());

    React.useEffect(() => {
      const onKey = (e) => {
        if (e.target.tagName === 'INPUT') { if (e.key === 'Escape') e.target.blur(); return; }
        if (e.key === '/') { e.preventDefault(); searchRef.current && searchRef.current.focus(); }
        else if (e.key === 'j' || e.key === 'ArrowDown') { e.preventDefault(); setCursor((c) => Math.min(rows.length - 1, c + 1)); }
        else if (e.key === 'k' || e.key === 'ArrowUp') { e.preventDefault(); setCursor((c) => Math.max(0, c - 1)); }
        else if (e.key === 'x' && cursor >= 0) { e.preventDefault(); toggleSel(rows[cursor].id); }
        else if (e.key === 'Enter' && cursor >= 0) { e.preventDefault(); setDrawer(rows[cursor]); }
        else if (e.key === 'd' && selected.size) { e.preventDefault(); setConfirm(true); }
        else if (e.key === 'Escape') { if (drawer) setDrawer(null); else if (selected.size) clearSel(); }
      };
      window.addEventListener('keydown', onKey);
      return () => window.removeEventListener('keydown', onKey);
    }, [rows, cursor, selected, drawer]);

    return (
      <div className="fb-scope" style={{ display: 'flex', height: '100%', background: 'var(--bg-0)', position: 'relative', overflow: 'hidden' }}>
        <FBSidebar collapsed={collapsed} active="ads" onToggle={() => setCollapsed(!collapsed)} onNav={(id) => onToast(`Экран «${id}» — в следующей итерации`)} />
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
          <FBTopbar crumb="Объявления" />
          <main style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, padding: 'var(--s-6) var(--s-8)' }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 'var(--s-5)' }}>
              <div>
                <Eyebrow num="04">УПРАВЛЕНИЕ · ОБЪЯВЛЕНИЯ</Eyebrow>
                <h1 className="mono" style={{ fontSize: 30, fontWeight: 500, color: 'var(--bg-11)', margin: '8px 0 0', letterSpacing: '-0.02em' }}>Объявления</h1>
              </div>
              <div style={{ display: 'flex', gap: 10 }}>
                {STATE_FILTERS.slice(0, 3).map((s) => (
                  <span key={s.id} className={`badge badge-${s.id}`}><span className="dot" />{(window.AD_COUNTS[s.id] || 0)}</span>
                ))}
              </div>
            </div>
            <FilterBar q={q} setQ={setQ} states={states} toggleState={toggleState} offers={offers} toggleOffer={toggleOffer} count={rows.length} searchRef={searchRef} density={density} />
            <AdTable rows={rows} selected={selected} toggleSel={toggleSel} onOpen={(a) => setDrawer(a)} cursor={cursor} rowH={rowH} />
            <div style={{ marginTop: 10, fontSize: 11, color: 'var(--bg-8)', display: 'flex', gap: 14, fontFamily: 'var(--font-num)' }}>
              <span><b style={{ color: 'var(--bg-9)' }}>J/K</b> навигация</span><span><b style={{ color: 'var(--bg-9)' }}>X</b> выбор</span><span><b style={{ color: 'var(--bg-9)' }}>D</b> disable</span><span><b style={{ color: 'var(--bg-9)' }}>Enter</b> детали</span><span><b style={{ color: 'var(--bg-9)' }}>/</b> поиск</span>
            </div>
          </main>
        </div>
        {selected.size > 0 && <BulkBar n={selected.size} onDisable={() => setConfirm(true)} onSnooze={() => { onToast(`Snooze ${selected.size} объявлений`); clearSel(); }} onClear={clearSel} />}
        {confirm && <ConfirmDisable n={selected.size} onCancel={() => setConfirm(false)} onConfirm={() => { onToast(`Создано ${selected.size} disable-задач`); setConfirm(false); clearSel(); }} />}
        {drawer && <AdDrawer ad={drawer} onClose={() => setDrawer(null)} />}
      </div>
    );
  }

  window.WebAds = WebAds;
})();
