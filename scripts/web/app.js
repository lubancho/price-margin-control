/* Дашборд «Контроль цен и маржинальности»: все показатели считаются в браузере из window.DATA. */
'use strict';

/* ---------- Данные ---------- */
const STORES = D.stores.map(([id, name], i) => ({i, id, name}));
const CATS = D.cats.map(([id, name], i) => ({i, id, name}));
const DAYS = D.days;
const OFFERS = D.offers.map(r => Object.fromEntries(D.offer_cols.map((k, j) => [k, r[j]])));
const ALERTS = D.alerts.map(r => Object.fromEntries(D.alert_cols.map((k, j) => [k, r[j]])));
const STATUS = {
  PROPOSED: {l: 'Предложено', p: 'p-vio', c: '--c1'}, CONFLICT: {l: 'Конфликт', p: 'p-warn', c: '--c4'},
  BLOCKED: {l: 'Заблокировано', p: 'p-bad', c: '--c3'}, ON_HOLD: {l: 'Отложено (промо)', p: '', c: '--c6'},
  NO_CHANGE: {l: 'Без изменений', p: 'p-ok', c: '--c5'},
};
const STATUS_ORDER = ['PROPOSED', 'CONFLICT', 'BLOCKED', 'ON_HOLD', 'NO_CHANGE'];
const DEV = {
  below_cost: 'Цена ниже себестоимости', below_min: 'Ниже минимальной маржи', below_target: 'Ниже целевой маржи',
  network: 'Отклонение от сети > 15%', data_error: 'Ошибка данных', none: 'Без отклонений',
};
const DQ = {DUPLICATE: 'Дубли строк', FK_VIOLATION: 'Ссылка на несуществующую точку или товар', DISCOUNT_OUT_OF_RANGE: 'Скидка вне допустимого диапазона',
  NON_POSITIVE_COST: 'Себестоимость ≤ 0', NON_POSITIVE_PRICE: 'Цена ≤ 0', MISSING_PRICE: 'Нет цены', MISSING_VALUE: 'Пустое обязательное поле',
  OUT_OF_RANGE: 'Значение вне диапазона'};
const SCOPE = {global: 'Вся сеть', category: 'Категория', store: 'Точка', product: 'Товар'};
const pill = s => `<span class="pill ${STATUS[s]?.p || ''}">${STATUS[s]?.l || s}</span>`;

OFFERS.forEach(o => {
  o.store = STORES[o.si].name; o.cat = CATS[o.ci].name;
  o.dev = devOf(o);
  o.status_h = pill(o.status);
  o.rec_show = ['PROPOSED'].includes(o.status) ? o.recommended_price : null;
  o.chg_show = o.rec_show != null ? o.change_pct : null;
});
function devOf(o) {
  if (o.status === 'BLOCKED' || o.plan_margin == null) return 'data_error';
  if (o.current_price < o.unit_cost) return 'below_cost';
  if (o.min_margin != null && o.plan_margin < o.min_margin) return 'below_min';
  if (o.target_margin != null && o.plan_margin < o.target_margin) return 'below_target';
  if (o.network_median && Math.abs(o.current_price - o.network_median) / o.network_median > 0.15) return 'network';
  return 'none';
}

/* ---------- Агрегация куба продаж ---------- */
function agg(from, to, key, st = State.s) {
  const m = new Map();
  for (const [d, s, c, rev, revc, cogs, qty] of D.cube) {
    if (d < from || d > to) continue;
    if (st.store != null && s !== st.store) continue;
    if (st.cat != null && c !== st.cat) continue;
    const k = key ? key(d, s, c) : 0;
    const a = m.get(k) || {rev: 0, revc: 0, cogs: 0, qty: 0};
    a.rev += rev; a.revc += revc; a.cogs += cogs; a.qty += qty; m.set(k, a);
  }
  return m;
}
const gp = a => a ? a.revc - a.cogs : null;
const mg = a => a ? ratio(a.revc - a.cogs, a.revc) : null;
const inScope = o => (State.s.store == null || o.si === State.s.store) && (State.s.cat == null || o.ci === State.s.cat);

/* ---------- Фильтры ---------- */
const LAST = DAYS.length - 1;
State.s = {days: 28, store: null, cat: null};
function range() { return [Math.max(0, LAST - State.s.days + 1), LAST]; }
function prevRange() {
  const [f, t] = range(), n = t - f + 1;
  return f - n < 0 ? null : [f - n, f - 1];
}
function buildFilters() {
  const per = $('#f-period');
  per.innerHTML = [[7, '7 дней'], [28, '28 дней'], [DAYS.length, 'Весь период']].map(([v, l]) =>
    `<button data-v="${v}" class="${State.s.days === v ? 'on' : ''}">${l}</button>`).join('');
  $$('button', per).forEach(b => b.onclick = () => State.set({days: +b.dataset.v}));
  const sel = $('#f-store');
  sel.innerHTML = '<option value="">Все точки</option>' + STORES.map(s => `<option value="${s.i}">${esc(s.name)}</option>`).join('');
  sel.onchange = () => State.set({store: sel.value === '' ? null : +sel.value});
  catChips = chipGroup('f-cats', CATS.map(c => ({v: c.i, label: c.name})), {allLabel: 'Все категории', value: null,
    onChange: v => State.set({cat: v})});
}
let catChips;
function syncFilters() {
  $$('#f-period button').forEach(b => b.classList.toggle('on', +b.dataset.v === State.s.days));
  $('#f-store').value = State.s.store ?? '';
  catChips.set(State.s.cat);
  const act = [];
  if (State.s.store != null) act.push(`<button class="chip af" data-x="store">${esc(STORES[State.s.store].name)} <span class="x">×</span></button>`);
  if (State.s.cat != null) act.push(`<button class="chip af" data-x="cat">${esc(CATS[State.s.cat].name)} <span class="x">×</span></button>`);
  if (act.length) act.push('<button class="link" data-x="all">Сбросить</button>');
  $('#f-active').innerHTML = act.join('');
  $$('#f-active [data-x]').forEach(b => b.onclick = () => {
    const x = b.dataset.x; State.set(x === 'all' ? {store: null, cat: null} : {[x]: null});
  });
  const view = (location.hash || '#overview').slice(1);
  $('#f-period').style.display = view === 'overview' || view === '' ? '' : 'none';
  $('#filterbar').style.display = view === 'history' ? 'none' : '';
}

/* ---------- 1. Обзор ---------- */
function renderOverview() {
  const [f, t] = range(), pr = prevRange();
  const cur = agg(f, t).get(0), prev = pr ? agg(pr[0], pr[1]).get(0) : null;
  const daily = agg(f, t, d => d);
  const dIdx = []; for (let d = f; d <= t; d++) dIdx.push(d);
  const revD = dIdx.map(d => daily.get(d)?.rev ?? 0), gpD = dIdx.map(d => gp(daily.get(d)) ?? 0),
    mgD = dIdx.map(d => mg(daily.get(d)));
  const offers = OFFERS.filter(inScope);
  const valid = offers.filter(o => o.plan_margin != null && o.min_margin != null && o.is_active);
  const below = valid.filter(o => o.plan_margin < o.min_margin);
  const openA = ALERTS.filter(a => ['NEW', 'IN_PROGRESS'].includes(a.status) && (State.s.store == null || a.si == null || a.si === State.s.store));
  const crit = openA.filter(a => a.severity === 'CRIT').length;
  const proposed = offers.filter(o => o.status === 'PROPOSED').length;
  const cmp = prev ? `к прошлым ${t - f + 1} дн.` : 'нет данных для сравнения';
  const kpis = [
    {k: 'rev', label: 'Выручка', dot: '', v: F.rubc(cur?.rev), d: deltaPill(cur?.rev, prev?.rev), note: cmp, spark: revD},
    {k: 'gp', label: 'Валовая прибыль', dot: '', v: F.rubc(gp(cur)), d: deltaPill(gp(cur), gp(prev)), note: cmp, spark: gpD},
    {k: 'mg', label: 'Маржинальность', dot: 'ok', v: F.pct(mg(cur)), d: prev ? deltaPill(mg(cur), mg(prev), {kind: 'pp'}) : '', note: cmp,
      spark: mgD.map(v => v == null ? null : v * 100)},
    {k: 'below', label: 'Ниже мин. маржи', dot: 'bad', v: F.int(below.length), note: `${F.pct(ratio(below.length, valid.length))} из ${F.int(valid.length)} предложений`,
      go: 'Открыть список →', act: () => goPrices({dev: ['below_min', 'below_cost']})},
    {k: 'alerts', label: 'Предупреждения', dot: 'warn', v: F.int(openA.length), note: `${crit} критичных`, go: 'Разобрать →', act: () => Nav.go('alerts')},
    {k: 'prop', label: 'Изменить цену', dot: '', v: F.int(proposed), note: `расчёт на ${F.date(D.meta.as_of)}`, go: 'Посмотреть →',
      act: () => goPrices({status: ['PROPOSED']})},
  ];
  $('#kpis').innerHTML = kpis.map(k => `<div class="card kpi ${k.act ? 'click' : ''}" data-k="${k.k}">
    <div class="eyebrow"><span class="dot ${k.dot}"></span>${k.label}</div>
    <div class="kpi-value">${k.v}</div>
    <div class="kpi-foot">${k.d || ''}<span>${k.note}</span></div>
    ${k.spark ? `<div class="spark" id="sp-${k.k}"></div>` : `<span class="go">${k.go}</span>`}</div>`).join('');
  kpis.forEach(k => {
    if (k.act) $(`[data-k="${k.k}"]`, $('#kpis')).onclick = k.act;
    if (k.spark) spark('sp-' + k.k, k.spark, k.k === 'mg' ? css('--c5') : css('--c1'));
  });
  $('#trend-hint').textContent = `${F.date(DAYS[f])} — ${F.date(DAYS[t])}`;

  const ma = revD.map((_, i) => { const s = revD.slice(Math.max(0, i - 6), i + 1); return s.reduce((a, b) => a + b, 0) / s.length; });
  const minM = D.rules.find(r => r.scope === 'global')?.mm;
  Charts.set('c-trend', () => ({
    legend: {top: 0, left: 0},
    grid: {left: 8, right: 8, top: 40, bottom: 4, containLabel: true},
    tooltip: {trigger: 'axis', valueFormatter: v => v},
    xAxis: Charts.axis('category', {data: dIdx.map(d => F.dshort(DAYS[d])), splitLine: {show: false}}),
    yAxis: [Charts.axis('value', {axisLabel: {color: css('--dim'), formatter: v => NF0.format(v / 1000) + ' тыс'}}),
      Charts.axis('value', {min: v => Math.max(0, Math.floor(v.min - 3)), max: v => Math.ceil(v.max + 2), splitLine: {show: false},
        axisLabel: {color: css('--dim'), formatter: v => v + '%'}})],
    series: [
      {name: 'Выручка', type: 'bar', data: revD, barMaxWidth: 18, itemStyle: {borderRadius: [6, 6, 0, 0],
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{offset: 0, color: css('--c2')}, {offset: 1, color: css('--c7')}])},
        tooltip: {valueFormatter: F.rub}},
      {name: 'Среднее за 7 дней', type: 'line', data: ma, smooth: true, symbol: 'none', lineStyle: {width: 2.5, color: css('--c1')},
        tooltip: {valueFormatter: F.rub}},
      {name: 'Маржинальность', type: 'line', yAxisIndex: 1, data: mgD.map(v => v == null ? null : +(v * 100).toFixed(2)), smooth: true,
        symbol: 'circle', symbolSize: 5, lineStyle: {width: 2, color: css('--c3')}, itemStyle: {color: css('--c3')},
        tooltip: {valueFormatter: v => NF1.format(v) + '%'},
        markLine: minM ? {symbol: 'none', silent: true, lineStyle: {color: css('--bad'), type: 'dashed'},
          label: {formatter: 'мин. маржа сети', color: css('--bad'), position: 'insideEndTop'}, data: [{yAxis: minM * 100}]} : undefined},
    ],
  }));

  const byCat = agg(f, t, (d, s, c) => c, {...State.s, cat: null});
  Charts.set('c-cats', () => ({
    tooltip: {trigger: 'item', formatter: p => `${p.name}<br><b>${F.rub(p.value)}</b> · ${NF1.format(p.percent)}%<br>маржа ${F.pct(mg(byCat.get(p.data.ci)))}`},
    legend: {bottom: 0, top: 'auto', left: 'center'},
    series: [{type: 'pie', radius: ['42%', '68%'], center: ['50%', '44%'], padAngle: 2, itemStyle: {borderRadius: 8},
      label: {show: true, formatter: p => `${NF0.format(p.percent)}%`, color: css('--text'), fontWeight: 700},
      emphasis: {scaleSize: 8},
      data: CATS.map(c => ({name: c.name, ci: c.i, value: +(byCat.get(c.i)?.rev ?? 0).toFixed(0),
        itemStyle: {opacity: State.s.cat == null || State.s.cat === c.i ? 1 : 0.3}}))}],
  }), p => State.set({cat: State.s.cat === p.data.ci ? null : p.data.ci}));

  const bySt = agg(f, t, (d, s) => s, {...State.s, store: null});
  const net = agg(f, t, null, {...State.s, store: null}).get(0);
  const sts = STORES.map(s => ({...s, m: mg(bySt.get(s.i)), rev: bySt.get(s.i)?.rev})).filter(s => s.m != null).sort((a, b) => a.m - b.m);
  Charts.set('c-stores', () => ({
    grid: {left: 8, right: 40, top: 8, bottom: 4, containLabel: true},
    tooltip: {trigger: 'axis', axisPointer: {type: 'shadow'}, formatter: ps => `${ps[0].name}<br>маржа <b>${NF1.format(ps[0].value)}%</b><br>выручка ${F.rub(sts[ps[0].dataIndex].rev)}`},
    xAxis: Charts.axis('value', {min: v => Math.max(0, Math.floor(v.min - 2)), axisLabel: {color: css('--dim'), formatter: v => v + '%'}}),
    yAxis: Charts.axis('category', {data: sts.map(s => s.name), axisLabel: {color: css('--text'), fontSize: 11.5}}),
    series: [{type: 'bar', barMaxWidth: 16, data: sts.map(s => ({value: +(s.m * 100).toFixed(2), si: s.i,
      itemStyle: {borderRadius: [0, 8, 8, 0], color: State.s.store === s.i ? css('--c3') : s.m < mg(net) ? css('--c4') : css('--c1'),
        opacity: State.s.store == null || State.s.store === s.i ? 1 : 0.35}})),
      label: {show: true, position: 'right', formatter: p => NF1.format(p.value) + '%', color: css('--muted'), fontSize: 11},
      markLine: {symbol: 'none', silent: true, lineStyle: {color: css('--dim'), type: 'dashed'}, label: {formatter: 'сеть', color: css('--dim')},
        data: [{xAxis: +(mg(net) * 100).toFixed(2)}]}}],
  }), p => State.set({store: State.s.store === p.data.si ? null : p.data.si}));

  const stCat = {};
  OFFERS.filter(o => State.s.store == null || o.si === State.s.store).forEach(o => {
    stCat[o.status] = stCat[o.status] || CATS.map(() => 0); stCat[o.status][o.ci]++;
  });
  Charts.set('c-status', () => ({
    tooltip: {trigger: 'axis', axisPointer: {type: 'shadow'}},
    legend: {top: 0, left: 0},
    grid: {left: 8, right: 12, top: 62, bottom: 4, containLabel: true},
    xAxis: Charts.axis('category', {data: CATS.map(c => c.name), splitLine: {show: false}, axisLabel: {color: css('--text'), interval: 0, fontSize: 11, formatter: v => v.replace(' и ', ' и\n')}}),
    yAxis: Charts.axis('value'),
    series: STATUS_ORDER.filter(s => s !== 'NO_CHANGE').map(s => ({name: STATUS[s].l, type: 'bar', stack: 'a', barMaxWidth: 46,
      itemStyle: {color: css(STATUS[s].c), borderRadius: 4}, emphasis: {focus: 'series'}, st: s,
      data: CATS.map(c => ({value: stCat[s]?.[c.i] ?? 0, ci: c.i,
        itemStyle: {opacity: State.s.cat == null || State.s.cat === c.i ? 1 : 0.3}}))})),
  }), p => { State.set({cat: p.data.ci}); goPrices({status: [STATUS_ORDER.filter(s => s !== 'NO_CHANGE')[p.seriesIndex]]}); });
}

/* ---------- 2. Анализ цен ---------- */
let pStatus = [], pDev = [], tPrices, stChips, devChips;
function goPrices({status = [], dev = []} = {}) { pStatus = status; pDev = dev; stChips.set(status); devChips.set(dev); Nav.go('prices'); renderPrices(); }
function initPrices() {
  stChips = chipGroup('p-status', [], {multi: true, allLabel: 'Все', onChange: v => { pStatus = v; renderPrices(); }});
  devChips = chipGroup('p-dev', [], {multi: true, allLabel: 'Любое', onChange: v => { pDev = v; renderPrices(); }});
  tPrices = new Table('t-prices', {
    cols: [{k: 'store', label: 'Точка', t: 'text', w: 150}, {k: 'product_name', label: 'Товар', t: 'text', w: 170},
      {k: 'cat', label: 'Категория', t: 'text'}, {k: 'unit_cost', label: 'Себест.', t: 'rub2'}, {k: 'current_price', label: 'Цена', t: 'rub'},
      {k: 'plan_margin', label: 'Маржа', t: 'pct'}, {k: 'rec_show', label: 'Рекомендация', t: 'rub'}, {k: 'chg_show', label: 'Изм.', t: 'spct'},
      {k: 'reason_text', label: 'Причина', t: 'text', w: 280}, {k: 'status_h', label: 'Статус', t: 'html'}],
    rows: OFFERS, page: 20, search: ['product_name', 'sku', 'store'], onRow: offerCard, csv: 'recommendations', sort: 'chg_show',
  });
}
function renderPrices() {
  const scope = OFFERS.filter(inScope);
  const where = [State.s.store != null ? STORES[State.s.store].name : 'все точки', State.s.cat != null ? CATS[State.s.cat].name : 'все категории'];
  $('#prices-eyebrow').textContent = `Расчёт на ${F.date(D.meta.as_of)} · правила v${D.meta.rule_version} · ${where.join(' · ')}`;
  const cnt = k => scope.filter(o => o.status === k).length;
  stChips.update(STATUS_ORDER.map(s => ({v: s, label: STATUS[s].l, n: cnt(s),
    cls: s === 'BLOCKED' ? 'bad' : s === 'CONFLICT' ? 'warn' : s === 'NO_CHANGE' ? 'ok' : ''})));
  devChips.update(Object.keys(DEV).map(k => ({v: k, label: DEV[k], n: scope.filter(o => o.dev === k).length,
    cls: ['below_cost', 'below_min', 'data_error'].includes(k) ? 'bad' : k === 'none' ? 'ok' : 'warn'})));
  const pred = o => inScope(o) && (!pStatus.length || pStatus.includes(o.status)) && (!pDev.length || pDev.includes(o.dev));
  tPrices.filter(pred);
  const ch = OFFERS.filter(o => pred(o) && o.status === 'PROPOSED').map(o => o.change_pct * 100);
  const bins = Array.from({length: 11}, (_, i) => i);
  Charts.set('c-hist', () => ({
    grid: {left: 8, right: 8, top: 16, bottom: 4, containLabel: true},
    tooltip: {trigger: 'axis', axisPointer: {type: 'shadow'}, formatter: ps => `+${ps[0].name}: <b>${ps[0].value}</b> предложений`},
    xAxis: Charts.axis('category', {data: bins.map(b => b === 10 ? '10%' : `${b}–${b + 1}%`), splitLine: {show: false}}),
    yAxis: Charts.axis('value'),
    series: [{type: 'bar', barMaxWidth: 34, data: bins.map(b => ch.filter(v => b === 10 ? v >= 10 : v >= b && v < b + 1).length),
      itemStyle: {borderRadius: [8, 8, 0, 0], color: new echarts.graphic.LinearGradient(0, 0, 0, 1,
        [{offset: 0, color: css('--c1')}, {offset: 1, color: css('--c2')}])}}],
  }));
}

/* ---------- Карточка предложения ---------- */
function offerCard(o) {
  const hist = D.price_hist[`${STORES[o.si].id}|${o.product_id}`] || [], cost = D.cost_hist[String(o.product_id)] || [];
  const al = ALERTS.filter(a => a.product_id === o.product_id && (a.si == null || a.si === o.si) && ['NEW', 'IN_PROGRESS'].includes(a.status));
  const peers = OFFERS.filter(x => x.product_id === o.product_id && x.current_price != null).sort((a, b) => a.current_price - b.current_price);
  const crit = ['below_cost', 'below_min', 'data_error'].includes(o.dev);
  Drawer.open(`<div class="eyebrow"><span class="dot ${crit ? 'bad' : ''}"></span>${esc(o.cat)} · ${esc(o.sku)}</div>
    <h2>${esc(o.product_name)}</h2><div class="meta">${esc(o.store)} · ${pill(o.status)} · правило: ${SCOPE[o.rule_scope] || '—'}
      (цель ${F.pct(o.target_margin)}, минимум ${F.pct(o.min_margin)})</div>
    <div class="mini"><div><span>Себестоимость</span><b>${F.rub2(o.unit_cost)}</b></div><div><span>Текущая цена</span><b>${F.rub(o.current_price)}</b></div>
      <div><span>Плановая маржа</span><b>${F.pct(o.plan_margin)}</b></div>
      <div><span>Рекомендация</span><b>${o.rec_show != null ? F.rub(o.rec_show) + ' <span class="pos" style="font-size:13px">' + F.spct(o.change_pct) + '</span>' : STATUS[o.status].l}</b></div></div>
    <div class="callout ${o.status === 'CONFLICT' ? 'warn' : o.status === 'BLOCKED' ? 'bad' : o.status === 'NO_CHANGE' ? 'ok' : ''}"><b>Почему.</b> ${esc(o.reason_text)}</div>
    <div class="card-head" style="margin-top:14px"><h3>История цены и себестоимости</h3></div><div class="chart short" id="c-card"></div>
    <div class="card-head" style="margin-top:10px"><h3>Цена этой позиции в точках сети</h3></div><div class="chart short" id="c-peers"></div>
    ${al.length ? `<div class="callout bad"><b>Открытые предупреждения:</b> ${[...new Set(al.map(a => a.title))].join(', ')}</div>` : ''}
    <p class="hint">В полной версии руководитель согласует или отклоняет рекомендацию прямо здесь; конфликт — с ценой, введённой вручную.</p>`, () => {
    const end = D.meta.as_of;
    const steps = h => [...h.map(([d, v]) => [d, v]), h.length ? [end, h[h.length - 1][1]] : null].filter(Boolean);
    Charts.set('c-card', () => ({
      grid: {left: 8, right: 12, top: 34, bottom: 4, containLabel: true},
      legend: {top: 0, left: 0},
      tooltip: {trigger: 'axis', valueFormatter: F.rub2},
      xAxis: Charts.axis('time', {splitLine: {show: false}, axisLabel: {color: css('--dim'), formatter: v => F.dshort(new Date(v).toISOString().slice(0, 10))}}),
      yAxis: Charts.axis('value', {min: 0}),
      series: [{name: 'Цена в точке', type: 'line', step: 'end', data: steps(hist), symbol: 'none', lineStyle: {width: 3, color: css('--c1')},
        areaStyle: {color: css('--c1') + '18'}},
        {name: 'Себестоимость', type: 'line', step: 'end', data: steps(cost), symbol: 'none', lineStyle: {width: 3, color: css('--c4')}}],
    }));
    Charts.set('c-peers', () => ({
      grid: {left: 8, right: 12, top: 10, bottom: 4, containLabel: true},
      tooltip: {trigger: 'axis', axisPointer: {type: 'shadow'}, valueFormatter: F.rub},
      xAxis: Charts.axis('category', {data: peers.map(x => x.store.replace('Омск — ', '').replace(' — Центр', '')), axisLabel: {color: css('--dim'), rotate: 40, fontSize: 10}}),
      yAxis: Charts.axis('value', {min: v => Math.floor(v.min * 0.9)}),
      series: [{type: 'bar', barMaxWidth: 18, data: peers.map(x => ({value: x.current_price,
        itemStyle: {color: x.si === o.si ? css('--c3') : css('--c2'), borderRadius: [6, 6, 0, 0]}}))}],
    }));
  });
}

/* ---------- 3. Предупреждения ---------- */
let aType = null, aSev = null, tAlerts, typeChips;
function initAlerts() {
  typeChips = chipGroup('a-type', [], {allLabel: 'Все типы', onChange: v => { aType = v; renderAlerts(); }});
  tAlerts = new Table('t-alerts', {
    cols: [{k: 'sev_h', label: 'Критичность', t: 'html'}, {k: 'title', label: 'Тип', t: 'text', w: 160}, {k: 'store_l', label: 'Точка', t: 'text'},
      {k: 'product_l', label: 'Товар', t: 'text'}, {k: 'description', label: 'Описание', t: 'text', w: 300},
      {k: 'occurrences', label: 'Повторов', t: 'int'}],
    rows: ALERTS.map(a => ({...a, sev_h: a.severity === 'CRIT' ? '<span class="pill p-bad">Критично</span>' : '<span class="pill p-warn">Внимание</span>',
      store_l: a.store_name || 'Вся сеть', product_l: a.product_name || '—'})),
    page: 12, search: ['product_l', 'description', 'title'], csv: 'alerts',
    onRow: a => { const o = OFFERS.find(x => x.product_id === a.product_id && (a.si == null || x.si === a.si)); if (o) offerCard(o); },
  });
  new Table('t-dq', {cols: [{k: 0, label: 'Проверка', t: 'html'}, {k: 1, label: 'Критичность', t: 'html'}, {k: 2, label: 'Строк', t: 'int'}],
    rows: D.dq.map(([c, sev, n]) => [DQ[c] ? `${DQ[c]} <span class="hint">${c}</span>` : c,
      sev === 'CRIT' ? '<span class="pill p-bad">Критично</span>' : '<span class="pill p-warn">Внимание</span>', n]), page: 10}).render();
}
function renderAlerts() {
  const scope = ALERTS.filter(a => ['NEW', 'IN_PROGRESS'].includes(a.status) && (State.s.store == null || a.si == null || a.si === State.s.store));
  const crit = scope.filter(a => a.severity === 'CRIT'), warn = scope.filter(a => a.severity === 'WARN');
  const cards = [['CRIT', 'Критичные', 'bad', crit.length, 'цена ниже себестоимости, ошибки данных…'],
    ['WARN', 'Требуют внимания', 'warn', warn.length, 'конфликты правил, скачки себестоимости…'],
    [null, 'Всего открытых', '', scope.length, 'нажмите, чтобы сбросить фильтр']];
  $('#a-kpis').innerHTML = cards.map(([k, l, dot, n, note]) => `<div class="card kpi click ${aSev === k && k ? 'on' : ''}" data-sev="${k ?? ''}"
    style="${aSev === k && k ? 'border-color:var(--bd2)' : ''}"><div class="eyebrow"><span class="dot ${dot}"></span>${l}</div>
    <div class="kpi-value">${F.int(n)}</div><div class="kpi-foot">${note}</div></div>`).join('');
  $$('#a-kpis [data-sev]').forEach(c => c.onclick = () => { aSev = c.dataset.sev || null; if (!aSev) aType = null; renderAlerts(); });
  const types = {};
  scope.filter(a => !aSev || a.severity === aSev).forEach(a => types[a.title] = (types[a.title] || 0) + 1);
  const tl = Object.entries(types).sort((a, b) => b[1] - a[1]);
  typeChips.update(tl.map(([t, n]) => ({v: t, label: t, n})));
  typeChips.set(aType);
  const tr = tl.slice().reverse();
  Charts.set('c-atypes', () => ({
    grid: {left: 8, right: 36, top: 6, bottom: 4, containLabel: true},
    tooltip: {trigger: 'axis', axisPointer: {type: 'shadow'}},
    xAxis: Charts.axis('value', {splitNumber: 3}),
    yAxis: Charts.axis('category', {data: tr.map(x => x[0]), axisLabel: {color: css('--text'), width: 170, overflow: 'break', fontSize: 11.5, lineHeight: 14}}),
    series: [{type: 'bar', barMaxWidth: 18, label: {show: true, position: 'right', color: css('--muted'), fontWeight: 700},
      data: tr.map(([t, n], i) => ({value: n, name: t, itemStyle: {color: PAL()[(tr.length - 1 - i) % 6], borderRadius: [0, 8, 8, 0],
        opacity: !aType || aType === t ? 1 : 0.3}}))}],
  }), p => { aType = aType === p.name ? null : p.name; renderAlerts(); });
  tAlerts.filter(a => ['NEW', 'IN_PROGRESS'].includes(a.status) && (State.s.store == null || a.si == null || a.si === State.s.store)
    && (!aSev || a.severity === aSev) && (!aType || a.title === aType));
}

/* ---------- 4. Сценарии: алгоритм цены в браузере ---------- */
const GLOBAL = D.rules.find(r => r.scope === 'global');
const SC = {tm: GLOBAL.tm, mm: GLOBAL.mm, mc: GLOBAL.mc, step: GLOBAL.step, dec: GLOBAL.dec, all: false, mode: 'fixed', e: -0.5};
function roundStep(x, step, mode) {
  const q = +(x / step).toFixed(9);
  const r = mode === 'up' ? Math.ceil(q) : mode === 'down' ? Math.floor(q) : Math.floor(q + 0.5);
  return +(r * step).toFixed(2);
}
function ruleFor(o, sc) {
  const g = {...GLOBAL, tm: sc.tm, mm: sc.mm, mc: sc.mc, step: sc.step, dec: sc.dec};
  if (sc.all) return g;
  const sid = STORES[o.si].id, cid = CATS[o.ci].id;
  return D.rules.find(r => r.scope === 'product' && r.id === o.product_id) || D.rules.find(r => r.scope === 'category' && r.id === cid)
    || D.rules.find(r => r.scope === 'store' && r.id === sid) || g;
}
function recommendJS(o, r) {
  const c = o.unit_cost, p0 = o.current_price;
  if (!o.is_active) return {st: 'BLOCKED'};
  if (o.cost_invalid || c == null || c <= 0) return {st: 'BLOCKED'};
  if (p0 == null || p0 <= 0) return {st: 'BLOCKED'};
  if (!(r.tm >= 0 && r.tm < 1) || r.mm < 0 || r.mm > r.tm || !(r.mc > 0 && r.mc <= 1) || !(r.step > 0)) return {st: 'BLOCKED'};
  let pr = roundStep(c / (1 - r.tm), r.step, r.mode);
  const pmin = roundStep(c / (1 - r.mm), r.step, 'up');
  if (pr < pmin) pr = pmin;
  if (pr < p0 && !r.dec) return {st: 'NO_CHANGE', p: p0};
  const lh = p0 * (1 + r.mc), ll = p0 * (1 - r.mc);
  let pc = pr;
  if (pr > lh + 1e-9) { pc = roundStep(lh, r.step, 'down'); if (pc <= p0) return {st: 'CONFLICT'}; }
  else if (pr < ll - 1e-9) { pc = roundStep(ll, r.step, 'up'); if (pc >= p0) return {st: 'CONFLICT'}; }
  if ((pc - c) / pc < r.mm - 1e-12) return {st: 'CONFLICT'};
  if (o.in_promo && pc !== p0) return {st: 'ON_HOLD'};
  if (pc === p0) return {st: 'NO_CHANGE', p: p0};
  return {st: 'PROPOSED', p: pc};
}
window.checkPricing = function () {  // сверка браузерного алгоритма с расчётом Python при стандартных правилах
  const sc = {tm: GLOBAL.tm, mm: GLOBAL.mm, mc: GLOBAL.mc, step: GLOBAL.step, dec: GLOBAL.dec, all: false};
  let mism = 0;
  OFFERS.forEach(o => {
    const r = recommendJS(o, ruleFor(o, sc));
    const pyPrice = o.status === 'PROPOSED' ? o.recommended_price : null, jsPrice = r.st === 'PROPOSED' ? r.p : null;
    if (r.st !== o.status || pyPrice !== jsPrice) mism++;
  });
  return {offers: OFFERS.length, mismatches: mism};
};
let tScen;
function initScenarios() {
  const ctl = $('#s-ctl');
  const sl = (k, l, min, max, step, fmt) => `<div><label>${l}<b id="sv-${k}"></b></label><input type="range" id="s-${k}" min="${min}" max="${max}" step="${step}"></div>`;
  ctl.innerHTML = sl('tm', 'Целевая маржа', 20, 70, 1) + sl('mm', 'Минимальная маржа', 10, 60, 1) + sl('mc', 'Лимит изменения цены', 2, 30, 1)
    + `<div><label>Шаг цены на витрине</label><div class="seg" id="s-step">${[1, 5, 10, 50].map(v => `<button data-v="${v}">${v} ₽</button>`).join('')}</div></div>
    <label class="switch"><input type="checkbox" id="s-dec">Разрешить снижение цены</label>
    <label class="switch"><input type="checkbox" id="s-all">Одно правило для всех категорий</label>
    <div class="eyebrow mt" style="margin-bottom:0"><span class="dot warn"></span>Реакция спроса</div>
    <div class="seg" id="s-mode"><button data-v="fixed">Спрос фиксирован</button><button data-v="coef">Коэффициент</button></div>
    <div><label>Коэффициент e<b id="sv-e"></b></label><input type="range" id="s-e" min="-3" max="0" step="0.1"></div>
    <button class="btn" id="s-reset">Вернуть правила системы</button>`;
  const sync = () => {
    $('#s-tm').value = Math.round(SC.tm * 100); $('#s-mm').value = Math.round(SC.mm * 100); $('#s-mc').value = Math.round(SC.mc * 100);
    $('#s-e').value = SC.e; $('#s-dec').checked = SC.dec; $('#s-all').checked = SC.all; $('#s-e').disabled = SC.mode === 'fixed';
    $$('#s-step button').forEach(b => b.classList.toggle('on', +b.dataset.v === SC.step));
    $$('#s-mode button').forEach(b => b.classList.toggle('on', b.dataset.v === SC.mode));
  };
  ['tm', 'mm', 'mc'].forEach(k => $('#s-' + k).oninput = e => {
    SC[k] = +e.target.value / 100;
    if (k === 'tm' && SC.mm > SC.tm) SC.mm = SC.tm;
    if (k === 'mm' && SC.mm > SC.tm) SC.tm = SC.mm;
    sync(); renderScenarios();
  });
  $('#s-e').oninput = e => { SC.e = +e.target.value; renderScenarios(); };
  $('#s-dec').onchange = e => { SC.dec = e.target.checked; renderScenarios(); };
  $('#s-all').onchange = e => { SC.all = e.target.checked; renderScenarios(); };
  $$('#s-step button').forEach(b => b.onclick = () => { SC.step = +b.dataset.v; sync(); renderScenarios(); });
  $$('#s-mode button').forEach(b => b.onclick = () => { SC.mode = b.dataset.v; sync(); renderScenarios(); });
  $('#s-reset').onclick = () => { Object.assign(SC, {tm: GLOBAL.tm, mm: GLOBAL.mm, mc: GLOBAL.mc, step: GLOBAL.step, dec: GLOBAL.dec, all: false}); sync(); renderScenarios(); };
  sync();
  tScen = new Table('t-scen', {
    cols: [{k: 'store', label: 'Точка', t: 'text'}, {k: 'product_name', label: 'Товар', t: 'text'}, {k: 'current_price', label: 'Цена', t: 'rub'},
      {k: 's_price', label: 'В сценарии', t: 'rub'}, {k: 's_chg', label: 'Изм.', t: 'spct'}, {k: 's_status', label: 'Статус', t: 'html'},
      {k: 's_dgp', label: 'Δ прибыли, 28 дн.', t: 'rub'}],
    rows: [], page: 10, onRow: offerCard, sort: 's_dgp', csv: 'scenario',
  });
  $('#s-assume').innerHTML = `<b>Допущения сценария.</b> Базовое количество — чистые продажи предложения за 28 дней до ${F.date(D.meta.as_of)}.
    Себестоимость неизменна. Промоакции и скидки не моделируются. «Конфликт», «Заблокировано», «Отложено» остаются по текущей цене.
    Коэффициент спроса задаётся вручную и не оценён по данным. Меняется только правило сети; правила категорий действуют, пока не включено
    «Одно правило для всех категорий». <b>Это сценарий, а не прогноз фактического результата.</b>`;
}
function renderScenarios() {
  $('#sv-tm').textContent = F.pct(SC.tm); $('#sv-mm').textContent = F.pct(SC.mm); $('#sv-mc').textContent = '±' + F.pct(SC.mc);
  $('#sv-e').textContent = SC.mode === 'fixed' ? 'не используется' : NF1.format(SC.e);
  const counts = {}, cat0 = CATS.map(() => 0), cat1 = CATS.map(() => 0);
  let R0 = 0, R1 = 0, G0 = 0, G1 = 0, changed = 0;
  const rows = [];
  OFFERS.filter(inScope).forEach(o => {
    const r = recommendJS(o, ruleFor(o, SC));
    counts[r.st] = (counts[r.st] || 0) + 1;
    const c = o.unit_cost, p0 = o.current_price, q0 = o.q0 || 0;
    if (c == null || c <= 0 || p0 == null || p0 <= 0) return;
    const p1 = r.st === 'PROPOSED' ? r.p : p0, dp = (p1 - p0) / p0;
    const q1 = SC.mode === 'fixed' ? q0 : Math.max(0, q0 * (1 + SC.e * dp));
    R0 += p0 * q0; R1 += p1 * q1; G0 += (p0 - c) * q0; G1 += (p1 - c) * q1; cat0[o.ci] += (p0 - c) * q0; cat1[o.ci] += (p1 - c) * q1;
    if (p1 !== p0) { changed++; rows.push({...o, s_price: p1, s_chg: dp, s_status: pill(r.st), s_dgp: (p1 - c) * q1 - (p0 - c) * q0}); }
  });
  const isDefault = SC.tm === GLOBAL.tm && SC.mm === GLOBAL.mm && SC.mc === GLOBAL.mc && SC.step === GLOBAL.step && SC.dec === GLOBAL.dec && !SC.all;
  const chk = isDefault && State.s.store == null && State.s.cat == null ? window.checkPricing() : null;
  $('#s-hero').innerHTML = `<div class="eyebrow">Валовая прибыль за 28 дней · сценарий</div>
    <div class="big">${F.rub(G1)} <span style="font-size:17px;opacity:.85">${G1 >= G0 ? '+' : '−'}${F.rub(Math.abs(G1 - G0))}</span></div>
    <div class="row"><div>Сейчас<b>${F.rub(G0)}</b></div><div>Выручка<b>${F.rubc(R1)}</b></div>
      <div>Маржинальность<b>${F.pct(ratio(G0, R0))} → ${F.pct(ratio(G1, R1))}</b></div><div>Изменится цен<b>${F.int(changed)}</b></div></div>
    ${chk ? `<div class="ok-pill">✓ Совпадает с расчётом системы: ${F.int(chk.offers - chk.mismatches)} из ${F.int(chk.offers)} рекомендаций</div>` : ''}`;
  Charts.set('c-sstatus', () => ({
    grid: {left: 8, right: 40, top: 6, bottom: 4, containLabel: true},
    tooltip: {trigger: 'axis', axisPointer: {type: 'shadow'}},
    xAxis: Charts.axis('value', {splitNumber: 3}),
    yAxis: Charts.axis('category', {data: STATUS_ORDER.map(s => STATUS[s].l).reverse(), axisLabel: {color: css('--text')}}),
    series: [{type: 'bar', barMaxWidth: 18, label: {show: true, position: 'right', color: css('--muted')},
      data: STATUS_ORDER.slice().reverse().map(s => ({value: counts[s] || 0, itemStyle: {color: css(STATUS[s].c), borderRadius: [0, 8, 8, 0]}}))}],
  }));
  Charts.set('c-scats', () => ({
    legend: {top: 0, left: 0}, grid: {left: 8, right: 8, top: 34, bottom: 4, containLabel: true},
    tooltip: {trigger: 'axis', axisPointer: {type: 'shadow'}, valueFormatter: F.rub},
    xAxis: Charts.axis('category', {data: CATS.map(c => c.name), axisLabel: {color: css('--text'), interval: 0, fontSize: 10.5, formatter: v => v.replace(' и ', ' и\n')}}),
    yAxis: Charts.axis('value', {axisLabel: {color: css('--dim'), formatter: v => NF0.format(v / 1e6) + ' млн'}}),
    series: [{name: 'Сейчас', type: 'bar', data: cat0.map(v => +v.toFixed(0)), itemStyle: {color: css('--c7'), borderRadius: [6, 6, 0, 0]}},
      {name: 'В сценарии', type: 'bar', data: cat1.map(v => +v.toFixed(0)), itemStyle: {color: css('--c1'), borderRadius: [6, 6, 0, 0]}}],
  }));
  tScen.setRows(rows);
}

/* ---------- 5. История ---------- */
function initHistory() {
  const g = D.rules.find(r => r.scope === 'global');
  $('#rules-hint').textContent = `версия v${D.meta.rule_version}, заблокирована после расчёта`;
  new Table('t-rules', {cols: [{k: 'lvl', label: 'Уровень', t: 'text'}, {k: 'obj', label: 'Объект', t: 'text'}, {k: 'tm', label: 'Цель', t: 'pct'},
    {k: 'mm', label: 'Минимум', t: 'pct'}, {k: 'mc', label: 'Лимит', t: 'pct'}, {k: 'step', label: 'Шаг, ₽', t: 'int'}],
  rows: D.rules.map(r => ({...r, lvl: SCOPE[r.scope], obj: r.scope === 'category' ? CATS.find(c => c.id === r.id)?.name : 'вся сеть'})), page: 10}).render();
  new Table('t-runs', {cols: [{k: 0, label: 'Запуск', t: 'text'}, {k: 1, label: 'Начало', t: 'text'}, {k: 2, label: 'Дата расчёта', t: 'text'},
    {k: 3, label: 'Правила', t: 'int'}, {k: 4, label: 'Статус', t: 'text'}, {k: 6, label: 'Предложений', t: 'int'}], rows: D.runs, page: 8}).render();
  new Table('t-events', {cols: [{k: 0, label: 'Время', t: 'text', w: 130}, {k: 1, label: 'Событие', t: 'text'}, {k: 2, label: 'Роль', t: 'text'},
    {k: 3, label: 'Описание', t: 'text', w: 420}], rows: D.events, page: 12, search: [1, 3]}).render();
  void g;
}

/* ---------- Запуск ---------- */
shell();
buildFilters();
initPrices(); initAlerts(); initScenarios(); initHistory();
$('#cnt-prices').textContent = F.int(OFFERS.filter(o => o.status === 'PROPOSED').length);
$('#cnt-alerts').textContent = F.int(ALERTS.filter(a => a.severity === 'CRIT' && ['NEW', 'IN_PROGRESS'].includes(a.status)).length);
$('#foot').innerHTML = `${esc(D.meta.note)} Статичная демо-версия: данные посчитаны кодом проекта ${F.date(D.meta.as_of)}, собрано ${D.meta.built}.
  Полная версия с согласованием рекомендаций и пересчётом — запуск через Docker (см. README).`;
function renderAll() { syncFilters(); renderOverview(); renderPrices(); renderAlerts(); renderScenarios(); }
State.on(renderAll);
window.addEventListener('hashchange', syncFilters);
Nav.init();
renderAll();
