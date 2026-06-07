// ads-data.js — generates ~1000 ad rows + helpers. window.ADS, window.adFilter
(function () {
  const OFFERS = ['DRC', 'UA17', 'SP', 'NUT', 'CR2', 'FX', 'KETO', 'VPN'];
  const BUYERS = ['MV', 'AK', 'TK', 'DN'];
  const GEOS = [
    ['PT', 'Lisboa'], ['BR', 'Lima'], ['UA', 'Kyiv'], ['DE', 'Berlin'], ['IT', 'Roma'],
    ['ES', 'Madrid'], ['FR', 'Paris'], ['NL', 'Amsterdam'], ['PL', 'Warsaw'], ['GB', 'London'],
  ];
  const RULES = ['CPL_HIGH', 'FREQ_HIGH', 'SPEND_NO_EVENT', 'CTR_LOW', 'ROAS_LOW', 'BUDGET_OVER'];
  // deterministic PRNG
  let seed = 1337;
  const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
  const pick = (a) => a[Math.floor(rnd() * a.length)];

  function makeState() {
    const r = rnd();
    if (r < 0.74) return 'normal';
    if (r < 0.85) return 'warning';
    if (r < 0.885) return 'stop';
    if (r < 0.925) return 'claimed';
    return 'disabled';
  }

  const ADS = [];
  for (let i = 0; i < 1000; i++) {
    const offer = pick(OFFERS);
    const buyer = pick(BUYERS);
    const [geo, city] = pick(GEOS);
    const code = geo + (1 + Math.floor(rnd() * 40));
    const state = makeState();
    const hot = state === 'stop' || state === 'warning';
    const spend = +(20 + rnd() * (hot ? 1400 : 700)).toFixed(1);
    const cpl = state === 'normal' ? +(8 + rnd() * 9).toFixed(1) : +(14 + rnd() * 40).toFixed(1);
    const cpm = +(5 + rnd() * 13).toFixed(1);
    const ctr = +(0.6 + rnd() * 2.6).toFixed(1);
    const freq = +(1.2 + rnd() * (hot ? 4 : 2.2)).toFixed(1);
    const roas = +(0.4 + rnd() * 2.6).toFixed(1);
    const leads = Math.floor(rnd() * 60);
    const base = cpl;
    const spark = Array.from({ length: 8 }, (_, k) => +(base * (0.7 + rnd() * 0.6) * (1 + k * (hot ? 0.04 : 0))).toFixed(1));
    const rules = hot ? [pick(RULES), ...(rnd() > 0.6 ? [pick(RULES)] : [])].filter((v, j, a) => a.indexOf(v) === j) : [];
    ADS.push({
      id: 'ad' + i, code, offer, buyer, geo, city,
      name: `${code} | ${offer} | ${buyer}`,
      fullName: `${code} | ${offer} | ${buyer} | ${city} | ${(1 + Math.floor(rnd() * 28))}.0${1 + Math.floor(rnd() * 4)}`,
      ad_id: '12021' + (1000000000 + Math.floor(rnd() * 8999999999)),
      state, spend, cpl, cpm, ctr, freq, roas, leads, rules,
      ageMin: Math.floor(rnd() * 180),
      spark,
    });
  }

  function adFilter(list, { search = '', states = [], offers = [], sort = 'spend', dir = 'desc' } = {}) {
    let out = list.filter((a) => {
      if (search) {
        const q = search.toLowerCase();
        if (!(a.name.toLowerCase().includes(q) || a.ad_id.includes(q) || a.offer.toLowerCase().includes(q))) return false;
      }
      if (states.length && !states.includes(a.state)) return false;
      if (offers.length && !offers.includes(a.offer)) return false;
      return true;
    });
    const m = dir === 'desc' ? -1 : 1;
    out = out.slice().sort((a, b) => (a[sort] > b[sort] ? 1 : a[sort] < b[sort] ? -1 : 0) * m);
    return out;
  }

  const counts = ADS.reduce((acc, a) => { acc[a.state] = (acc[a.state] || 0) + 1; return acc; }, {});

  window.ADS = ADS;
  window.AD_OFFERS = OFFERS;
  window.AD_COUNTS = counts;
  window.adFilter = adFilter;
})();
