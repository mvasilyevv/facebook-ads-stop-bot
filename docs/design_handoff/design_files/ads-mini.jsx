// ads-mini.jsx — Telegram Mini App: Ads list + filter chips + ad detail sheet. window.MiniAds
(function () {
  const { Icon, Eyebrow, FsmBadge, RulePills, Sparkline, MiniTabBar } = window;
  const STATE_FILTERS = [
    { id: 'normal', label: 'Норма' }, { id: 'warning', label: 'Предупр.' },
    { id: 'stop', label: 'Стоп' }, { id: 'claimed', label: 'В работе' }, { id: 'disabled', label: 'Откл.' },
  ];
  const money = (v) => '$' + v.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 });

  function AdSheet({ ad, onClose }) {
    React.useEffect(() => { const k = (e) => { if (e.key === 'Escape') onClose(); }; window.addEventListener('keydown', k); return () => window.removeEventListener('keydown', k); }, [onClose]);
    const metrics = [
      { k: 'spend', v: money(ad.spend) }, { k: 'CPL', v: '$' + ad.cpl.toFixed(1), flag: ad.cpl > 30 },
      { k: 'CPM', v: '$' + ad.cpm.toFixed(1) }, { k: 'CTR', v: ad.ctr.toFixed(1) + '%' },
      { k: 'freq', v: ad.freq.toFixed(1), flag: ad.freq > 4 }, { k: 'ROAS', v: ad.roas.toFixed(1) + '×', flag: ad.roas < 1 },
    ];
    return (
      <div style={{ position: 'absolute', inset: 0, zIndex: 50, display: 'flex', flexDirection: 'column', justifyContent: 'flex-end' }}>
        <div onClick={onClose} style={{ position: 'absolute', inset: 0, background: 'rgba(10,10,11,0.7)', animation: 'fbFade var(--dur-base)' }} />
        <div role="dialog" aria-label="Объявление" style={{ position: 'relative', background: 'var(--bg-1)', borderTop: '1px solid var(--bg-6)', maxHeight: '84%', display: 'flex', flexDirection: 'column', animation: 'fbSheetUp var(--dur-slow) var(--ease-spring)', paddingBottom: 24 }}>
          <div style={{ display: 'flex', justifyContent: 'center', padding: '8px 0 4px' }}><div style={{ width: 36, height: 4, borderRadius: 999, background: 'var(--bg-6)' }} /></div>
          <div style={{ padding: '8px 16px 14px', borderBottom: '1px solid var(--bg-5)' }}>
            <Eyebrow num="ОБЪЯВЛЕНИЕ">{ad.geo} · {ad.city}</Eyebrow>
            <div className="mono" style={{ fontSize: 14, color: 'var(--bg-11)', margin: '6px 0', lineHeight: 1.3 }}>{ad.fullName}</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}><FsmBadge state={ad.state} sm /><span className="codechip">{ad.offer}</span></div>
          </div>
          <div style={{ flex: 1, overflowY: 'auto', padding: 16, display: 'flex', flexDirection: 'column', gap: 16 }}>
            {ad.rules.length > 0 && <div style={{ background: 'var(--danger-bg)', border: '1px solid color-mix(in srgb, var(--danger) 30%, transparent)', borderLeft: '2px solid var(--danger)', padding: 12, display: 'flex', gap: 6, flexWrap: 'wrap' }}><RulePills rules={ad.rules} /></div>}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', border: '1px solid var(--bg-5)' }}>
              {metrics.map((m, i) => (
                <div key={m.k} style={{ padding: '10px 12px', borderRight: i % 3 !== 2 ? '1px solid var(--bg-5)' : 'none', borderTop: i >= 3 ? '1px solid var(--bg-5)' : 'none', background: m.flag ? 'var(--danger-bg)' : 'transparent' }}>
                  <div className="eyebrow" style={{ fontSize: 9, color: m.flag ? 'var(--danger)' : 'var(--bg-9)' }}>{m.k}</div>
                  <div className="mono tnum" style={{ fontSize: 15, color: m.flag ? 'var(--danger)' : 'var(--bg-11)', marginTop: 4 }}>{m.v}</div>
                </div>
              ))}
            </div>
            <div className="card" style={{ padding: 12 }}>
              <Eyebrow style={{ marginBottom: 10 }}>CPL · 8 ТОЧЕК</Eyebrow>
              <Sparkline data={ad.spark} color={ad.cpl > 30 ? 'var(--danger)' : 'var(--accent)'} w={300} h={56} fill />
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

  function AdRow({ a, onOpen }) {
    const c = `var(--fsm-${a.state})`;
    return (
      <button onClick={() => onOpen(a)} style={{ display: 'grid', gridTemplateColumns: '40px 1fr auto auto', gap: 10, alignItems: 'center', width: '100%', textAlign: 'left', background: 'transparent', border: 'none', borderBottom: '1px solid var(--bg-5)', padding: '10px 14px', cursor: 'pointer', font: 'inherit', color: 'inherit' }}>
        <div style={{ width: 40, height: 26, background: 'var(--bg-2)', border: '1px solid var(--bg-6)', display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 'none' }}><span className="mono" style={{ fontSize: 8, color: 'var(--bg-8)' }}>{a.geo}</span></div>
        <div style={{ minWidth: 0 }}>
          <div className="mono" style={{ fontSize: 13, color: 'var(--bg-11)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.name}</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4 }}>
            <span style={{ width: 6, height: 6, borderRadius: 999, background: c, flex: 'none' }} />
            <span style={{ fontSize: 11, color: 'var(--bg-9)' }}>{window.FSM[a.state].label}</span>
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div className="mono tnum" style={{ fontSize: 13, color: 'var(--bg-11)' }}>{money(a.spend)}</div>
          <div className="mono tnum" style={{ fontSize: 11, color: a.cpl > 30 ? 'var(--danger)' : 'var(--bg-9)', marginTop: 3 }}>CPL ${a.cpl.toFixed(1)}</div>
        </div>
        <span style={{ color: 'var(--bg-8)' }}><Icon name="chevR" size={14} /></span>
      </button>
    );
  }

  function MiniAds({ onToast }) {
    const [q, setQ] = React.useState('');
    const [states, setStates] = React.useState([]);
    const [sheet, setSheet] = React.useState(null);
    const toggleState = (s) => setStates((p) => p.includes(s) ? p.filter((x) => x !== s) : [...p, s]);
    const rows = React.useMemo(() => window.adFilter(window.ADS, { search: q, states, sort: 'spend', dir: 'desc' }), [q, states]);
    const shown = rows.slice(0, 120);

    return (
      <div className="fb-scope" style={{ position: 'relative', height: '100%', display: 'flex', flexDirection: 'column', background: 'var(--bg-0)', paddingTop: 50, overflow: 'hidden' }}>
        <header style={{ flex: 'none', padding: '8px 16px 12px', borderBottom: '1px solid var(--bg-5)', background: 'var(--bg-0)' }}>
          <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: 12 }}>
            <div>
              <Eyebrow num="04">УПРАВЛЕНИЕ</Eyebrow>
              <h1 className="mono" style={{ fontSize: 26, fontWeight: 500, color: 'var(--bg-11)', margin: '4px 0 0', letterSpacing: '-0.02em' }}>Объявления</h1>
            </div>
            <span className="mono tnum" style={{ fontSize: 12, color: 'var(--bg-9)' }}>{rows.length.toLocaleString('en-US')}</span>
          </div>
          <div style={{ position: 'relative', marginBottom: 10 }}>
            <span style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--bg-9)' }}><Icon name="search" size={15} /></span>
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Поиск" style={{ width: '100%', height: 40, background: 'var(--bg-2)', border: '1px solid var(--bg-6)', borderRadius: 'var(--r-1)', color: 'var(--bg-11)', font: 'inherit', fontSize: 14, padding: '0 10px 0 34px', outline: 'none', boxSizing: 'border-box' }} />
          </div>
          <div style={{ display: 'flex', gap: 6, overflowX: 'auto', paddingBottom: 2 }}>
            {STATE_FILTERS.map((s) => {
              const on = states.includes(s.id);
              return (
                <button key={s.id} onClick={() => toggleState(s.id)} style={{ flex: 'none', height: 30, padding: '0 12px', borderRadius: 'var(--r-full)', border: `1px solid ${on ? 'var(--accent)' : 'var(--bg-6)'}`, background: on ? 'var(--accent-bg)' : 'transparent', color: on ? 'var(--accent)' : 'var(--bg-10)', font: 'inherit', fontSize: 12, fontWeight: 500, cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ width: 6, height: 6, borderRadius: 999, background: `var(--fsm-${s.id})` }} />{s.label}
                </button>
              );
            })}
          </div>
        </header>
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {shown.map((a) => <AdRow key={a.id} a={a} onOpen={(x) => setSheet(x)} />)}
          {rows.length > shown.length && <div style={{ padding: 16, textAlign: 'center', fontSize: 12, color: 'var(--bg-8)' }}>+{(rows.length - shown.length).toLocaleString('en-US')} ещё · уточни фильтр</div>}
          {rows.length === 0 && <div style={{ padding: '40px 24px', textAlign: 'center', color: 'var(--bg-9)', fontSize: 13 }}>Ничего не найдено</div>}
        </div>
        <MiniTabBar active="ads" onNav={(id) => onToast(`«${id}» — скоро`)} />
        {sheet && <AdSheet ad={sheet} onClose={() => setSheet(null)} />}
      </div>
    );
  }

  window.MiniAds = MiniAds;
})();
