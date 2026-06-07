// templates.jsx — static hi-fi templates for remaining web screens. window.TEMPLATES
(function () {
  const { Icon, Eyebrow, FsmBadge, RulePills, Sparkline, FBSidebar, FBTopbar } = window;

  function Shell({ active, crumb, children }) {
    return (
      <div className="fb-scope" style={{ display: 'flex', height: '100%', background: 'var(--bg-0)', position: 'relative', overflow: 'hidden' }}>
        <FBSidebar collapsed={false} active={active} onToggle={() => {}} onNav={() => {}} />
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
          <FBTopbar crumb={crumb} />
          <main style={{ flex: 1, overflowY: 'auto', padding: 'var(--s-6) var(--s-8)' }}>{children}</main>
        </div>
      </div>
    );
  }
  function PageHead({ num, eyebrow, title, sub, action }) {
    return (
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 'var(--s-8)', gap: 24 }}>
        <div>
          <Eyebrow num={num}>{eyebrow}</Eyebrow>
          <h1 className="mono" style={{ fontSize: 30, fontWeight: 500, color: 'var(--bg-11)', margin: '8px 0 6px', letterSpacing: '-0.02em' }}>{title}</h1>
          {sub && <div style={{ fontSize: 13, color: 'var(--bg-9)' }}>{sub}</div>}
        </div>
        {action}
      </div>
    );
  }

  // ═══ DRAFTS ════════════════════════════════════════════════════════════════
  function DiffRow({ k, cur, target, changed }) {
    return (
      <div style={{ display: 'grid', gridTemplateColumns: '160px 1fr', gap: 12, padding: '7px 12px', borderLeft: changed ? '2px solid var(--accent)' : '2px solid transparent', background: changed ? 'var(--accent-bg)' : 'transparent' }}>
        <span className="mono" style={{ fontSize: 12, color: 'var(--bg-9)' }}>{k}</span>
        <span className="mono" style={{ fontSize: 12, color: 'var(--bg-11)', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          {target != null ? <><span style={{ color: 'var(--bg-9)' }}>{cur}</span><Icon name="chevR" size={12} /><span style={{ color: changed ? 'var(--accent)' : 'var(--bg-11)' }}>{target}</span></> : cur}
        </span>
      </div>
    );
  }
  function DraftCard({ d }) {
    const owner = d.owner;
    return (
      <div className="card">
        <div style={{ padding: 'var(--s-5)', borderBottom: '1px solid var(--bg-5)' }}>
          <div className="eyebrow" style={{ marginBottom: 8 }}>DRAFT <span style={{ color: 'var(--bg-7)' }}>·</span> {d.ago} <span style={{ color: 'var(--bg-7)' }}>·</span> <span style={{ color: 'var(--accent-muted)' }}>{d.op}</span></div>
          <div style={{ fontSize: 16, color: 'var(--bg-11)', fontWeight: 500 }}>{d.summary}</div>
          <div style={{ fontSize: 12, color: 'var(--bg-9)', marginTop: 6 }}>Запросил <span className="mono" style={{ color: 'var(--bg-10)' }}>{owner}</span></div>
        </div>
        <div style={{ padding: 'var(--s-4) var(--s-3)', borderBottom: '1px solid var(--bg-5)' }}>
          {d.diff.map((r) => <DiffRow key={r.k} {...r} />)}
        </div>
        <div style={{ padding: 'var(--s-4) var(--s-5)', borderBottom: '1px solid var(--bg-5)' }}>
          <span className="eyebrow" style={{ marginBottom: 6, display: 'inline-block' }}>AI · ОБОСНОВАНИЕ</span>
          <div style={{ fontSize: 13, color: 'var(--bg-10)', lineHeight: 1.5 }}>{d.reason}</div>
        </div>
        <div style={{ padding: 'var(--s-4) var(--s-5)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16 }}>
          <span style={{ fontSize: 12, color: d.expSoon ? 'var(--warning)' : 'var(--bg-9)', display: 'inline-flex', alignItems: 'center', gap: 7 }}>
            <Icon name="clock" size={14} />Истекает через <span className="mono tnum">{d.expires}</span>
          </span>
          <div style={{ display: 'flex', gap: 'var(--s-3)' }}>
            <button className="btn btn-ghost">Отклонить</button>
            <button className="btn btn-primary"><Icon name="check" size={15} stroke={2.4} />Одобрить и выполнить</button>
          </div>
        </div>
      </div>
    );
  }
  const DRAFTS = [
    { ago: '12 мин назад', op: 'meta_api / pause_ad', summary: 'Поставит на паузу 1 объявление', owner: '@markvasilev', expires: '23ч 47м', expSoon: false,
      diff: [
        { k: 'ad_id', cur: '120211…8761' },
        { k: 'ad_name', cur: 'CR2 | DRC | MV | Tyver | 25.03' },
        { k: 'state', cur: 'ACTIVE', target: 'PAUSED', changed: true },
      ],
      reason: 'Spend $234 / CPL $42 держится выше порога $15 на окне 3ч. ROAS 0.7×. Рекомендую паузу до ревизии креатива.' },
    { ago: '1 ч назад', op: 'meta_api / set_adset_budget', summary: 'Обновит дневной бюджет adset', owner: '@markvasilev', expires: '22ч 50м', expSoon: false,
      diff: [
        { k: 'adset_id', cur: '120211…4420' },
        { k: 'daily_budget', cur: '$200.00', target: '$350.00 (+75%)', changed: true },
        { k: 'safety_cap', cur: '$100,000 (под лимитом)' },
      ],
      reason: 'CPL $11.4, ROAS 2.8× стабильно 48ч. Масштабирование +75% в пределах safety-cap.' },
    { ago: '3 ч назад', op: 'meta_api / activate_ad', summary: 'Включит 1 объявление', owner: '@dnpro', expires: '20ч 12м', expSoon: true,
      diff: [
        { k: 'ad_id', cur: '120211…1190' },
        { k: 'state', cur: 'PAUSED', target: 'ACTIVE', changed: true },
      ],
      reason: 'Auto-enable: CPL вернулся в норму ($13.2) после правки аудитории.' },
  ];
  function DraftsTemplate() {
    return (
      <Shell active="drafts" crumb="Черновики">
        <PageHead num="04" eyebrow="OPERATE · ОДОБРЕНИЕ" title="Черновики" sub={<>7 в ожидании · <span style={{ color: 'var(--warning)' }}>3 истекают в течение 1ч</span></>} />
        <div style={{ display: 'flex', gap: 8, marginBottom: 'var(--s-6)' }}>
          {['Все', 'pause', 'activate', 'budget', 'campaign'].map((f, i) => (
            <button key={f} className="btn btn-sm" style={{ borderRadius: 'var(--r-full)', border: `1px solid ${i === 0 ? 'var(--accent)' : 'var(--bg-6)'}`, background: i === 0 ? 'var(--accent-bg)' : 'transparent', color: i === 0 ? 'var(--accent)' : 'var(--bg-10)' }}>{f}</button>
          ))}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--s-4)', maxWidth: 760 }}>
          {DRAFTS.map((d, i) => <DraftCard key={i} d={d} />)}
        </div>
      </Shell>
    );
  }

  // ═══ OFFERS ════════════════════════════════════════════════════════════════
  const OFFERS = [
    { code: 'DRC_CR2', active: true, spend: '$1,234.56', leads: 45, cpl: '$27.43', alerts: 3 },
    { code: 'UA17_MV', active: true, spend: '$891.12', leads: 32, cpl: '$27.84', alerts: 0 },
    { code: 'SP_TK', active: true, spend: '$642.00', leads: 51, cpl: '$12.59', alerts: 1 },
    { code: 'NUT_MV', active: true, spend: '$1,580.20', leads: 38, cpl: '$41.58', alerts: 5 },
    { code: 'FX_AK', active: false, spend: '$0.00', leads: 0, cpl: '—', alerts: 0 },
    { code: 'KETO_DN', active: true, spend: '$402.70', leads: 14, cpl: '$28.76', alerts: 0 },
  ];
  function OfferCard({ o }) {
    return (
      <div className="card" style={{ display: 'flex', flexDirection: 'column' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: 'var(--s-4) var(--s-5)' }}>
          <span className="mono" style={{ fontSize: 15, fontWeight: 600, color: 'var(--bg-11)' }}>{o.code}</span>
          <span className="badge" style={{ background: o.active ? 'var(--success-bg)' : 'var(--bg-2)', color: o.active ? 'var(--success)' : 'var(--bg-9)', border: `1px solid ${o.active ? 'color-mix(in srgb, var(--success) 30%, transparent)' : 'var(--bg-6)'}` }}>
            <span className="dot" style={{ background: o.active ? 'var(--success)' : 'var(--bg-8)' }} />{o.active ? 'active' : 'inactive'}
          </span>
        </div>
        <div style={{ borderTop: '1px solid var(--bg-5)', padding: 'var(--s-4) var(--s-5)', display: 'flex', flexDirection: 'column', gap: 10 }}>
          {[['Spend', o.spend], ['Leads', o.leads], ['CPL', o.cpl], ['Alerts', o.alerts]].map(([k, v]) => (
            <div key={k} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span style={{ fontSize: 12, color: 'var(--bg-9)' }}>{k}</span>
              <span className="mono tnum" style={{ fontSize: 14, color: k === 'Alerts' && v > 0 ? 'var(--warning)' : 'var(--bg-11)' }}>{v}</span>
            </div>
          ))}
        </div>
        <div style={{ borderTop: '1px solid var(--bg-5)', padding: 'var(--s-3) var(--s-4)', display: 'flex', gap: 'var(--s-2)' }}>
          <button className="btn btn-secondary btn-sm" style={{ flex: 1 }}>Правила</button>
          <button className="btn btn-ghost btn-sm" style={{ flex: 1 }}>Изменить</button>
        </div>
      </div>
    );
  }
  function OffersTemplate() {
    return (
      <Shell active="offers" crumb="Офферы">
        <PageHead num="02" eyebrow="CATALOG · ОФФЕРЫ" title="Офферы" sub="18 active · 4 inactive"
          action={<button className="btn btn-primary"><Icon name="plus" size={16} stroke={2} />Новый оффер</button>} />
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 'var(--s-5)' }}>
          <div style={{ display: 'flex', gap: 8 }}>
            {['Все', 'Активные', 'Неактивные'].map((t, i) => (
              <button key={t} className="btn btn-sm" style={{ borderRadius: 'var(--r-full)', border: `1px solid ${i === 0 ? 'var(--accent)' : 'var(--bg-6)'}`, background: i === 0 ? 'var(--accent-bg)' : 'transparent', color: i === 0 ? 'var(--accent)' : 'var(--bg-10)' }}>{t}</button>
            ))}
          </div>
          <button className="btn btn-secondary btn-sm" style={{ gap: 6 }}>Сортировка: spend<Icon name="chevD" size={12} /></button>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 'var(--s-4)' }}>
          {OFFERS.map((o) => <OfferCard key={o.code} o={o} />)}
        </div>
      </Shell>
    );
  }

  // ═══ HISTORY ═══════════════════════════════════════════════════════════════
  const HIST_DAYS = [
    { day: 'СЕГОДНЯ · 28 МАЯ', rows: [
      { t: '14:32', stage: 'warning', ad: 'CR2 | DRC | MV', rule: 'CPL_HIGH' },
      { t: '14:28', stage: 'stop', ad: 'UA17 | SP | MV', rule: 'CPL_HIGH' },
      { t: '13:51', stage: 'claimed', ad: 'PL4 | NUT | MV', rule: 'ROAS_LOW' },
    ] },
    { day: 'ВЧЕРА · 27 МАЯ', rows: [
      { t: '22:14', stage: 'warning', ad: 'DE2 | SP | TK', rule: 'CTR_LOW' },
      { t: '19:03', stage: 'stop', ad: 'IT8 | DRC | AK', rule: 'BUDGET_OVER' },
      { t: '11:40', stage: 'warning', ad: 'BR9 | DRC | AK', rule: 'SPEND_NO_EVENT' },
    ] },
  ];
  function HistoryTemplate() {
    return (
      <Shell active="history" crumb="История">
        <PageHead num="03" eyebrow="HISTORY · АРХИВ" title="История" sub="Последние 30 дней · 1,234 события"
          action={<button className="btn btn-secondary" style={{ gap: 6 }}><Icon name="external" size={15} />Export CSV</button>} />
        <div style={{ display: 'flex', gap: 8, marginBottom: 'var(--s-6)' }}>
          <button className="btn btn-secondary btn-sm" style={{ gap: 6 }}><Icon name="clock" size={13} />1 — 28 мая<Icon name="chevD" size={12} /></button>
          {['Кампания: любая', 'Offer: любой', 'Stage: любой'].map((f) => (
            <button key={f} className="btn btn-secondary btn-sm" style={{ gap: 6, color: 'var(--bg-9)' }}>{f}<Icon name="chevD" size={12} /></button>
          ))}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '40% 60%', gap: 'var(--s-6)' }}>
          {/* summary */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--s-4)' }}>
            <div className="card" style={{ padding: 'var(--s-5)' }}>
              <Eyebrow style={{ marginBottom: 14 }}>ВСЕГО СОБЫТИЙ</Eyebrow>
              <div className="mono tnum" style={{ fontSize: 44, fontWeight: 500, color: 'var(--bg-11)', lineHeight: 0.9 }}>1,234</div>
            </div>
            <div className="card" style={{ padding: 'var(--s-5)' }}>
              <Eyebrow style={{ marginBottom: 14 }}>ПО STAGE</Eyebrow>
              {[['warning', 834, 'var(--warning)'], ['stop', 267, 'var(--danger)'], ['claimed', 133, 'var(--info)']].map(([k, v, c]) => (
                <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
                  <span style={{ width: 7, height: 7, borderRadius: 999, background: c }} />
                  <span style={{ flex: 1, fontSize: 13, color: 'var(--bg-10)' }}>{k}</span>
                  <span className="mono tnum" style={{ fontSize: 14, color: 'var(--bg-11)' }}>{v}</span>
                </div>
              ))}
            </div>
            <div className="card" style={{ padding: 'var(--s-5)' }}>
              <Eyebrow style={{ marginBottom: 14 }}>ПО ПРАВИЛУ</Eyebrow>
              {[['CPL_HIGH', 412], ['SPEND_NO_EVENT', 234], ['FREQ_HIGH', 198], ['CTR_LOW', 176]].map(([k, v]) => (
                <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
                  <span className="rulepill" style={{ height: 18 }}>{k}</span>
                  <span style={{ flex: 1 }} />
                  <span className="mono tnum" style={{ fontSize: 14, color: 'var(--bg-11)' }}>{v}</span>
                </div>
              ))}
            </div>
          </div>
          {/* timeline */}
          <div className="card">
            {HIST_DAYS.map((d) => (
              <div key={d.day}>
                <div style={{ padding: '12px var(--s-5) 8px', borderBottom: '1px solid var(--bg-5)' }}><span className="eyebrow">{d.day}</span></div>
                {d.rows.map((r, i) => {
                  const c = { warning: 'var(--fsm-warning)', stop: 'var(--fsm-stop)', claimed: 'var(--fsm-claimed)' }[r.stage];
                  return (
                    <div key={i} style={{ display: 'grid', gridTemplateColumns: 'auto auto 1fr auto auto', gap: 'var(--s-3)', alignItems: 'center', height: 44, padding: '0 var(--s-5)', borderBottom: '1px solid var(--bg-5)' }}>
                      <span className="mono tnum" style={{ fontSize: 13, color: 'var(--bg-9)', minWidth: 44 }}>{r.t}</span>
                      <span style={{ width: 7, height: 7, borderRadius: 999, background: c }} />
                      <span className="mono" style={{ fontSize: 13, color: 'var(--bg-11)' }}>{r.ad}</span>
                      <span className="rulepill">{r.rule}</span>
                      <Icon name="chevR" size={14} />
                    </div>
                  );
                })}
              </div>
            ))}
            <div style={{ padding: 'var(--s-4)', textAlign: 'center' }}><button className="btn btn-ghost btn-sm">Загрузить ещё</button></div>
          </div>
        </div>
      </Shell>
    );
  }

  // ═══ SETTINGS ══════════════════════════════════════════════════════════════
  function Field({ label, value, hint, toggle, on }) {
    return (
      <div style={{ display: 'grid', gridTemplateColumns: '180px 1fr', gap: 16, alignItems: 'center', padding: '12px 0', borderBottom: '1px solid var(--bg-5)' }}>
        <div><div style={{ fontSize: 13, color: 'var(--bg-10)' }}>{label}</div>{hint && <div style={{ fontSize: 11, color: 'var(--bg-8)', marginTop: 2 }}>{hint}</div>}</div>
        {toggle
          ? <div style={{ width: 38, height: 22, borderRadius: 999, background: on ? 'var(--accent)' : 'var(--bg-5)', position: 'relative', cursor: 'pointer' }}><span style={{ position: 'absolute', top: 2, left: on ? 18 : 2, width: 18, height: 18, borderRadius: 999, background: on ? 'var(--bg-0)' : 'var(--bg-9)', transition: 'left var(--dur-fast)' }} /></div>
          : <div style={{ height: 32, background: 'var(--bg-2)', border: '1px solid var(--bg-6)', borderRadius: 'var(--r-1)', display: 'flex', alignItems: 'center', padding: '0 10px', fontFamily: 'var(--font-num)', fontSize: 13, color: 'var(--bg-11)', maxWidth: 280 }}>{value}</div>}
      </div>
    );
  }
  function SettingsTemplate() {
    const tabs = ['Observer', 'Telegram', 'Vision', 'Workers', 'AI', 'Health'];
    return (
      <Shell active="settings" crumb="Настройки">
        <PageHead num="05" eyebrow="SYSTEM · КОНФИГУРАЦИЯ" title="Настройки" />
        <div style={{ display: 'flex', gap: 4, borderBottom: '1px solid var(--bg-6)', marginBottom: 'var(--s-6)' }}>
          {tabs.map((t, i) => (
            <button key={t} style={{ padding: '10px 16px', background: 'transparent', border: 'none', borderBottom: `2px solid ${i === 0 ? 'var(--accent)' : 'transparent'}`, color: i === 0 ? 'var(--bg-11)' : 'var(--bg-9)', font: 'inherit', fontSize: 13, fontWeight: i === 0 ? 600 : 400, cursor: 'pointer', marginBottom: -1 }}>{t}</button>
          ))}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '60% 40%', gap: 'var(--s-8)' }}>
          <div>
            <Eyebrow style={{ marginBottom: 8, display: 'inline-block' }}>OBSERVER · ПАРАМЕТРЫ</Eyebrow>
            <Field label="Интервал скана" value="30 сек" />
            <Field label="Cabinet URL" value="https://adsmanager.facebook.com/…" />
            <Field label="Страна" value="PT" />
            <Field label="Auto-disable" toggle on={true} hint="отключать при stop-правиле" />
            <Field label="Auto-enable reco" toggle on={true} hint="включать по рекомендации AI" />
            <div style={{ marginTop: 'var(--s-5)' }}><button className="btn btn-primary">Сохранить изменения</button></div>
          </div>
          <div>
            <div className="card" style={{ padding: 'var(--s-5)', marginBottom: 'var(--s-4)' }}>
              <Eyebrow style={{ marginBottom: 14 }}>СТАТУС</Eyebrow>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                <span style={{ fontSize: 13, color: 'var(--bg-10)' }}>Observer</span>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 13, color: 'var(--success)' }}><span style={{ width: 7, height: 7, borderRadius: 999, background: 'var(--success)' }} />ONLINE</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <span style={{ fontSize: 13, color: 'var(--bg-10)' }}>Последний скан</span>
                <span className="mono tnum" style={{ fontSize: 13, color: 'var(--bg-11)' }}>14с назад</span>
              </div>
            </div>
            <div className="card" style={{ padding: 'var(--s-5)' }}>
              <Eyebrow style={{ marginBottom: 14 }}>ДЕЙСТВИЯ</Eyebrow>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--s-3)' }}>
                <button className="btn btn-secondary" style={{ justifyContent: 'flex-start' }}><Icon name="refresh" size={15} />Перезапустить observer</button>
                <button className="btn btn-secondary" style={{ justifyContent: 'flex-start' }}><Icon name="scan" size={15} />Сканировать сейчас</button>
                <button className="btn btn-secondary" style={{ justifyContent: 'flex-start' }}><Icon name="clock" size={15} />Начать новый день кабинета</button>
              </div>
            </div>
          </div>
        </div>
      </Shell>
    );
  }

  window.TEMPLATES = [
    { id: 'drafts', name: 'Черновики', tag: 'Drafts · одобрение AI-мутаций', el: DraftsTemplate, h: 880 },
    { id: 'offers', name: 'Офферы', tag: 'Offers · каталог + правила', el: OffersTemplate, h: 620 },
    { id: 'history', name: 'История', tag: 'History · сводка + таймлайн', el: HistoryTemplate, h: 720 },
    { id: 'settings', name: 'Настройки', tag: 'Settings · таб-навигация', el: SettingsTemplate, h: 640 },
  ];
})();
