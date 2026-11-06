/* Дашборд «Контроль цен и маржинальности». Все показатели считаются в браузере из window.DATA,
   выгруженного из базы проекта. Формулы и правила — те же, что в Python (pmc.pricing, pmc.metrics). */
'use strict';

/* ---------- Данные и справочники ---------- */
const STORES = D.stores.map(([id, name], i) => ({i, id, name, short: name.replace('Омск — ', '')}));
const CATS = D.cats.map(([id, name], i) => ({i, id, name}));
const DAYS = D.days, LAST = DAYS.length - 1;
const OFFERS = D.offers.map(r => Object.fromEntries(D.offer_cols.map((k, j) => [k, r[j]])));
const ALERTS = D.alerts.map(r => Object.fromEntries(D.alert_cols.map((k, j) => [k, r[j]])));
const OPEN = a => ['NEW', 'IN_PROGRESS'].includes(a.status);

const STATUS = {
  PROPOSED: {l: 'Изменить цену', p: 'p-vio', c: '--c1', help: 'Система рассчитала новую цену. Решение принимает руководитель.'},
  CONFLICT: {l: 'Нужно решение вручную', p: 'p-warn', c: '--c4', help: 'Правила противоречат друг другу: допустимой цены нет. Цену задаёт руководитель.'},
  BLOCKED: {l: 'Не рассчитано: ошибка данных', p: 'p-bad', c: '--c3', help: 'В исходных данных ошибка (себестоимость, цена или товар выведен) — цену рассчитать нельзя.'},
  ON_HOLD: {l: 'Отложено: промоакция', p: '', c: '--c6', help: 'Товар участвует в промо — изменение перенесено до окончания акции.'},
  NO_CHANGE: {l: 'Цена в норме', p: 'p-ok', c: '--c5', help: 'Текущая цена соответствует правилам — менять не нужно.'},
};
const STATUS_ORDER = ['BLOCKED', 'CONFLICT', 'PROPOSED', 'ON_HOLD', 'NO_CHANGE'];
const DEV = {
  data_error: {l: 'Ошибка данных', cls: 'bad', help: 'Цена или себестоимость отсутствует или некорректна'},
  below_cost: {l: 'Цена ниже себестоимости', cls: 'bad', help: 'Каждая продажа убыточна'},
  below_min: {l: 'Маржа ниже минимальной', cls: 'bad', help: 'Плановая маржа ниже минимума, заданного правилом'},
  below_target: {l: 'Маржа ниже целевой', cls: 'warn', help: 'Выше минимума, но ниже цели правила'},
  network: {l: 'Цена отличается от сети > 15%', cls: 'warn', help: 'Цена в точке отличается от медианы сети более чем на 15%'},
  none: {l: 'Без отклонений', cls: 'ok', help: 'Маржа не ниже целевой, цена близка к сети'},
};
const DEV_ORDER = ['data_error', 'below_cost', 'below_min', 'below_target', 'network', 'none'];
const SCOPE = {global: 'правило сети', category: 'правило категории', store: 'правило точки', product: 'правило товара'};
const ALERT_INFO = {  // уровень и смысл типа предупреждения; reliability — влияет на достоверность расчёта
  INVALID_FIN_DATA: {lvl: 'crit', reliability: true, what: 'Себестоимость или цена некорректны (≤ 0, нет данных). Расчёт по позиции невозможен.', next: 'Исправить данные в учётной системе и пересчитать.'},
  PRICE_UNDETERMINED: {lvl: 'crit', reliability: true, what: 'Нет действующей цены на дату расчёта — нельзя проверить маржу.', next: 'Проверить прайс точки.'},
  PRICE_BELOW_COST: {lvl: 'crit', what: 'Продажа по текущей цене убыточна.', next: 'Проверить себестоимость и цену; при подтверждении — поднять цену.'},
  MARGIN_BELOW_MIN: {lvl: 'crit', what: 'Маржа ниже минимального порога правила.', next: 'Открыть рекомендацию по цене.'},
  RULE_CONFLICT: {lvl: 'warn', what: 'Для минимальной маржи нужна цена выше, чем разрешает лимит изменения.', next: 'Принять решение вручную: цена или пересмотр правила.'},
  COST_JUMP: {lvl: 'warn', what: 'Себестоимость заметно изменилась после последнего пересмотра цены.', next: 'Проверить закупочную цену и техкарту.'},
  MARGIN_DROP: {lvl: 'warn', what: 'Маржинальность позиции снизилась относительно предыдущего периода.', next: 'Проверить скидки и себестоимость.'},
  PRICE_DEVIATION: {lvl: 'warn', what: 'Цена в точке сильно отличается от медианы сети.', next: 'Проверить, не ошибка ли это в прайсе.'},
};
const LEVEL = {
  crit: {l: 'Критично', p: 'p-bad', dot: 'bad', ac: '--bad', d: 'Расчёт недостоверен или продажи убыточны'},
  warn: {l: 'Требует внимания', p: 'p-warn', dot: 'warn', ac: '--warn', d: 'Маржа под угрозой, нужна проверка'},
  info: {l: 'Информация', p: 'p-vio', dot: '', ac: '--vio', d: 'Учтено системой, действий не требует'},
};
const DQ = {DUPLICATE: 'Дубли строк — исключены автоматически', FK_VIOLATION: 'Ссылка на несуществующую точку или товар', DISCOUNT_OUT_OF_RANGE: 'Скидка вне допустимого диапазона',
  NON_POSITIVE_COST: 'Себестоимость ≤ 0', NON_POSITIVE_PRICE: 'Цена ≤ 0', MISSING_PRICE: 'Нет цены', MISSING_VALUE: 'Пустое обязательное поле', OUT_OF_RANGE: 'Значение вне диапазона'};
const EVENT = {RUN_COMPLETED: 'Расчёт выполнен', RUN_STARTED: 'Расчёт запущен', RUN_FAILED: 'Ошибка расчёта', DATA_LOADED: 'Загрузка данных',
  RULES_CREATED: 'Новая версия правил', DECISION: 'Решение по цене', ALERT_STATUS: 'Статус предупреждения', MIGRATION: 'Обновление базы'};
const HELP = {
  margin: 'Валовая маржинальность = (выручка − себестоимость проданного) / выручка. Считается по продажам, для которых известна себестоимость на дату продажи.',
  revenue: 'Выручка = сумма продаж за вычетом возвратов и скидок, без отменённых чеков.',
  gp: 'Валовая прибыль = выручка − себестоимость проданного (без аренды, зарплат и прочих расходов).',
  proposed: 'Позиции, для которых система рассчитала новую цену по правилам маржи. Цены в кассе система не меняет.',
  conflict: 'Позиции, где правила противоречат друг другу (минимальная маржа требует цену выше лимита изменения). Нужна цена вручную.',
  dq: 'Позиции, по которым цену рассчитать нельзя из-за ошибок данных, и строки, исключённые при загрузке.',
  planMargin: 'Плановая маржа = (цена − себестоимость) / цена при текущей цене и себестоимости на дату расчёта.',
  afterMargin: 'Маржа при рекомендуемой цене и той же себестоимости.',
};
const pill = s => `<span class="pill ${STATUS[s]?.p || ''}">${STATUS[s]?.l || s}</span>`;
const pctAx = v => NF0.format(v) + '%';
const sum = (a, f) => a.reduce((s, x) => s + f(x), 0);

OFFERS.forEach(o => {
  o.reason_short = (o.reason_text || '').split(';')[0];
  o.item_h = `${esc(o.product_name)}<div class="hint">${esc(STORES[o.si].short)}</div>`;  // первая часть объяснения; целиком — в карточке
  o.store = STORES[o.si].name; o.cat = CATS[o.ci].name;
  o.dev = devOf(o);
  o.status_h = pill(o.status);
  const proposed = o.status === 'PROPOSED';
  o.rec_show = proposed ? o.recommended_price : null;
  o.chg_abs = proposed ? o.change_abs : null;
  o.chg_pct = proposed ? o.change_pct : null;
  o.m_after = proposed && o.unit_cost != null ? (o.recommended_price - o.unit_cost) / o.recommended_price : null;
  o.chg_h = proposed ? `${o.change_abs >= 0 ? '+' : '−'}${F.rub(Math.abs(o.change_abs))}<br><span class="hint">${F.spct(o.change_pct)}</span>` : '—';
  o.mg_h = o.plan_margin == null ? NA : proposed ? `${F.pct(o.plan_margin)} → <b>${F.pct(o.m_after)}</b>` : F.pct(o.plan_margin);
  o.prio = DEV_ORDER.indexOf(o.dev) * 10 + STATUS_ORDER.indexOf(o.status);
});
function devOf(o) {
  if (o.status === 'BLOCKED' || o.plan_margin == null) return 'data_error';
  if (o.current_price < o.unit_cost) return 'below_cost';
  if (o.min_margin != null && o.plan_margin < o.min_margin) return 'below_min';
  if (o.target_margin != null && o.plan_margin < o.target_margin) return 'below_target';
  if (o.network_median && Math.abs(o.current_price - o.network_median) / o.network_median > 0.15) return 'network';
  return 'none';
}
const poz = n => `${F.int(n)} ${n % 10 === 1 && n % 100 !== 11 ? 'позиция' : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? 'позиции' : 'позиций'}`;
const naText = o => ({BLOCKED: 'не рассчитано', CONFLICT: 'нужно решение', ON_HOLD: 'отложено', NO_CHANGE: 'без изменений'}[o.status] || 'нет данных');

/* ---------- Агрегация продаж ---------- */
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
const alertInScope = a => State.s.store == null || a.si == null || a.si === State.s.store;

/* ---------- Фильтры ---------- */
State.s = {days: 28, store: null, cat: null};
const range = () => [Math.max(0, LAST - State.s.days + 1), LAST];
function prevRange() { const [f, t] = range(), n = t - f + 1; return f - n < 0 ? null : [f - n, f - 1]; }
const periodText = ([f, t] = range()) => `${F.date(DAYS[f])} — ${F.date(DAYS[t])}`;
function buildFilters() {
  const per = $('#f-period');
  per.innerHTML = [[7, '7 дней'], [28, '28 дней'], [DAYS.length, `${DAYS.length} дней`]].map(([v, l]) =>
    `<button data-v="${v}" aria-pressed="false">${l}</button>`).join('');
  $$('button', per).forEach(b => b.onclick = () => State.set({days: +b.dataset.v}));
  const sel = $('#f-store');
  sel.innerHTML = '<option value="">Все точки</option>' + STORES.map(s => `<option value="${s.i}">${esc(s.name)}</option>`).join('');
  sel.onchange = () => State.set({store: sel.value === '' ? null : +sel.value});
  const cat = $('#f-cat');
  cat.innerHTML = '<option value="">Все категории</option>' + CATS.map(c => `<option value="${c.i}">${esc(c.name)}</option>`).join('');
  cat.onchange = () => State.set({cat: cat.value === '' ? null : +cat.value});
}
const view = () => (location.hash || '#overview').slice(1) || 'overview';
function syncFilters() {
  $$('#f-period button').forEach(b => { const on = +b.dataset.v === State.s.days; b.classList.toggle('on', on); b.setAttribute('aria-pressed', on); });
  $('#f-store').value = State.s.store ?? '';
  $('#f-cat').value = State.s.cat ?? '';
  const act = [];
  if (State.s.store != null) act.push(`<button class="chip af" data-x="store" aria-label="Снять фильтр: ${esc(STORES[State.s.store].name)}">${esc(STORES[State.s.store].name)} <span aria-hidden="true">×</span></button>`);
  if (State.s.cat != null) act.push(`<button class="chip af" data-x="cat" aria-label="Снять фильтр: ${esc(CATS[State.s.cat].name)}">${esc(CATS[State.s.cat].name)} <span aria-hidden="true">×</span></button>`);
  if (act.length) act.push('<button class="link" data-x="all">Сбросить</button>');
  $('#f-active').innerHTML = act.join('');
  $$('#f-active [data-x]').forEach(b => b.onclick = () => { const x = b.dataset.x; State.set(x === 'all' ? {store: null, cat: null} : {[x]: null}); });
  // На каждом экране видны только те фильтры, которые на нём действуют (на обзоре — не больше двух).
  const v = view(), show = (id, on) => { $(id).style.display = on ? '' : 'none'; };
  show('#f-period', v === 'overview' || v === 'margin');
  show('#f-cat', v !== 'overview');
  show('#filterbar', v !== 'method');
}

/* ---------- 1. Обзор: главный показатель → вывод → проблемы → подробности по запросу ---------- */
function goProblems(level = null, type = null) {
  aLevel = level; aType = type;
  Nav.go('problems');
  if (type) $('#a-details').open = true;
  renderAlerts();
}
function problemItems(rg, offers, m, mPrev, dm) {
  // только факты из расчёта; порядок — по влиянию на достоверность и деньги
  const n = rg[1] - rg[0] + 1, cnt = s => offers.filter(o => o.status === s).length, devCnt = k => offers.filter(o => o.dev === k).length;
  const blocked = cnt('BLOCKED'), conflicts = cnt('CONFLICT');
  const items = [];
  if (devCnt('below_cost')) items.push({lvl: 'crit', t: `Цена ниже себестоимости: ${poz(devCnt('below_cost'))}`, why: 'Каждая продажа этих позиций убыточна.',
    go: () => goPrices({dev: ['below_cost']})});
  if (devCnt('below_min')) items.push({lvl: 'crit', t: `Маржа ниже минимальной: ${poz(devCnt('below_min'))}`, why: 'По большинству система уже предложила новую цену.',
    go: () => goPrices({dev: ['below_min']})});
  if (blocked) items.push({lvl: 'crit', t: `Цена не рассчитана из-за ошибок в данных: ${poz(blocked)}`, why: 'Сначала нужно исправить себестоимость или цену в учётной системе.',
    go: () => goPrices({status: ['BLOCKED']})});
  if (conflicts) items.push({lvl: 'warn', t: `Нужно решение руководителя: ${poz(conflicts)}`, why: 'Правила противоречат друг другу: система не может выбрать цену сама.',
    go: () => goPrices({status: ['CONFLICT']})});
  if (dm != null && dm < 0) {
    const jumps = ALERTS.filter(a => OPEN(a) && a.alert_code === 'COST_JUMP' && alertInScope(a)).length;
    items.push({lvl: 'warn', t: `Маржа снизилась на ${F.num1(-dm * 100)} п. п. за ${n} дн.`, why: jumps ? `У ${jumps} позиций заметно выросла себестоимость — стоит проверить.` : 'Сравните точки на странице «Где падает маржа».',
      go: () => jumps ? goProblems('warn', 'COST_JUMP') : Nav.go('margin')});
  }
  return items;
}
function renderOverview() {
  const rg = range(), pr = prevRange(), n = rg[1] - rg[0] + 1;
  const cur = agg(...rg).get(0), prev = pr ? agg(...pr).get(0) : null;
  const daily = agg(...rg, d => d);
  const dIdx = []; for (let d = rg[0]; d <= rg[1]; d++) dIdx.push(d);
  const offers = OFFERS.filter(inScope);
  const proposed = offers.filter(o => o.status === 'PROPOSED').length;
  const needDecision = offers.filter(o => o.status === 'CONFLICT' || o.status === 'BLOCKED').length;
  const m = mg(cur), mPrev = mg(prev), dm = m != null && mPrev != null ? m - mPrev : null;
  const items = problemItems(rg, offers, m, mPrev, dm);
  $('#ov-period').textContent = `${periodText(rg)} · ${State.s.store != null ? STORES[State.s.store].name : 'вся сеть'}`;
  $('#ov-verdict').innerHTML = `Маржа ${State.s.store != null ? 'точки' : 'сети'} — <b>${F.pct(m)}</b>${dm != null ? `, это на ${F.num1(Math.abs(dm) * 100)} п. п. ${dm >= 0 ? 'выше' : 'ниже'}, чем в прошлые ${n} дн.` : '.'}
    ${items.length ? `Главное сейчас: ${items[0].t.charAt(0).toLowerCase() + items[0].t.slice(1)}.` : 'Серьёзных проблем не найдено.'}`;
  const kpis = [
    {k: 'mg', l: 'Маржа', tip: HELP.margin, v: F.pct(m), d: pr ? deltaPill(m, mPrev, {kind: 'pp'}) : '', note: pr ? 'к прошлому периоду' : ''},
    {k: 'gp', l: 'Валовая прибыль', tip: HELP.gp, v: F.rubc(gp(cur)), d: deltaPill(gp(cur), gp(prev)), note: pr ? 'к прошлому периоду' : ''},
    {k: 'prop', l: 'Цен к пересмотру', tip: HELP.proposed, v: F.int(proposed), note: 'предлагает система', act: () => goPrices({status: ['PROPOSED']})},
    {k: 'dec', l: 'Ждут решения', dot: 'warn', tip: HELP.conflict + ' Сюда же входят позиции, где расчёт невозможен из-за ошибок данных.', v: F.int(needDecision),
      note: 'конфликт правил или ошибка данных', act: () => goPrices({status: ['CONFLICT', 'BLOCKED']})},
  ];
  $('#kpis').innerHTML = kpis.map(k => `<div class="card kpi plain ${k.act ? 'click' : ''}" data-k="${k.k}">
    <div class="eyebrow">${k.dot ? `<span class="dot ${k.dot}"></span>` : ''}${k.l}${tip(k.tip)}</div>
    <div class="kpi-value">${k.v}</div><div class="kpi-foot">${k.d || ''}<span>${k.note}</span></div></div>`).join('');
  kpis.forEach(k => { if (k.act) clickable($(`[data-k="${k.k}"]`, $('#kpis')), e => { if (!e.target.closest('.tip')) k.act(); }); });
  const top = items.slice(0, 3);
  $('#attn-hint').textContent = items.length > 3 ? `показаны 3 из ${items.length}` : '';
  $('#attn').innerHTML = top.length ? top.map((x, i) => `<button class="attn-item" data-i="${i}" style="--ac:var(${LEVEL[x.lvl].ac})">
      <span class="sevc"><span class="pill ${LEVEL[x.lvl].p}">${LEVEL[x.lvl].l}</span></span>
      <span><span class="t">${x.t}</span><span class="why" style="display:block">${x.why}</span></span>
      <span></span><span class="arr" aria-hidden="true">→</span></button>`).join('')
    : '<div class="attn-ok">Проблем, требующих внимания, не найдено.</div>';
  $$('#attn .attn-item').forEach(b => b.onclick = () => top[+b.dataset.i].go());
  $('#cta-recs').textContent = `Пересмотреть цены: ${F.int(proposed)} предложений →`;
  $('#cta-recs').onclick = () => goPrices({status: ['PROPOSED']});
  const nProb = ALL_ROWS.filter(alertInScope).length;
  $('#cta-problems').textContent = `Все проблемы и ошибки (${F.int(nProb)})`;
  $('#cta-problems').onclick = () => goProblems();

  const minM = D.rules.find(r => r.scope === 'global')?.mm;
  $('#trend-hint').textContent = `пунктир — минимум по правилу${minM ? ' ' + F.pct(minM) : ''}`;
  Charts.set('c-trend', () => ({
    legend: {show: false}, grid: {left: 8, right: 12, top: 12, bottom: 4, containLabel: true},
    tooltip: {trigger: 'axis', valueFormatter: v => NF1.format(v) + '%'},
    xAxis: Charts.axis('category', {data: dIdx.map(d => F.dshort(DAYS[d])), splitLine: {show: false}}),
    yAxis: Charts.axis('value', {min: v => Math.max(0, Math.floor(Math.min(v.min, (minM ?? 1) * 100) - 3)), max: v => Math.ceil(v.max + 2), axisLabel: {formatter: pctAx}}),
    series: [{name: 'Маржа', type: 'line', data: dIdx.map(d => { const v = mg(daily.get(d)); return v == null ? null : +(v * 100).toFixed(2); }),
      smooth: true, symbol: 'none', lineStyle: {width: 3, color: css('--c1')}, areaStyle: {color: css('--c1') + '14'},
      markLine: minM ? {symbol: 'none', silent: true, lineStyle: {color: css('--bad'), type: 'dashed'}, label: {show: false}, data: [{yAxis: minM * 100}]} : undefined}],
  }));
}

/* ---------- 3. Аналитика: маржа и продажи ---------- */
function renderMargin() {
  const rg = range(), daily = agg(...rg, d => d);
  const dIdx = []; for (let d = rg[0]; d <= rg[1]; d++) dIdx.push(d);
  const minM = D.rules.find(r => r.scope === 'global')?.mm;
  $('#trend2-hint').textContent = periodText(rg);
  Charts.set('c-trend2', () => ({
    legend: {top: 0, left: 0}, grid: {left: 8, right: 8, top: 40, bottom: 4, containLabel: true},
    tooltip: {trigger: 'axis'},
    xAxis: Charts.axis('category', {data: dIdx.map(d => F.dshort(DAYS[d])), splitLine: {show: false}}),
    yAxis: [Charts.axis('value', {min: v => Math.max(0, Math.floor(Math.min(v.min, (minM ?? 1) * 100) - 3)), max: v => Math.ceil(v.max + 2), axisLabel: {formatter: pctAx}}),
      Charts.axis('value', {splitLine: {show: false}, axisLabel: {formatter: v => NF0.format(v / 1000) + ' тыс ₽'}})],
    series: [
      {name: 'Выручка, ₽ (правая ось)', type: 'bar', yAxisIndex: 1, data: dIdx.map(d => daily.get(d)?.rev ?? 0), barMaxWidth: 14,
        itemStyle: {color: css('--c7'), borderRadius: [4, 4, 0, 0]}, tooltip: {valueFormatter: F.rub}, z: 1},
      {name: 'Маржинальность, %', type: 'line', data: dIdx.map(d => { const v = mg(daily.get(d)); return v == null ? null : +(v * 100).toFixed(2); }),
        smooth: true, symbolSize: 5, lineStyle: {width: 3, color: css('--c1')}, itemStyle: {color: css('--c1')}, z: 3,
        tooltip: {valueFormatter: v => NF1.format(v) + '%'},
        markLine: minM ? {symbol: 'none', silent: true, lineStyle: {color: css('--bad'), type: 'dashed'},
          label: {formatter: `минимум правила ${NF0.format(minM * 100)}%`, color: css('--bad'), position: 'insideEndTop'}, data: [{yAxis: minM * 100}]} : undefined},
    ],
  }));
  const bySt = agg(...rg, (d, s) => s, {...State.s, store: null}), net = mg(agg(...rg, null, {...State.s, store: null}).get(0));
  const sts = STORES.map(s => ({...s, m: mg(bySt.get(s.i)), rev: bySt.get(s.i)?.rev})).filter(s => s.m != null).sort((a, b) => a.m - b.m);
  const low = sts.filter(s => s.m < net);
  $('#mg-verdict').innerHTML = sts.length ? `Ниже средней по сети (${F.pct(net)}) — <b>${low.length} из ${sts.length}</b> точек. Ниже всех — ${esc(sts[0].name)}: <b>${F.pct(sts[0].m)}</b>.` : 'Нет продаж за выбранный период.';
  Charts.set('c-stores', () => ({
    grid: {left: 8, right: 46, top: 8, bottom: 4, containLabel: true},
    tooltip: {trigger: 'axis', axisPointer: {type: 'shadow'}, formatter: ps => `${ps[0].name}<br>маржа <b>${NF1.format(ps[0].value)}%</b> (сеть ${F.pct(net)})<br>выручка ${F.rub(sts[ps[0].dataIndex].rev)}`},
    xAxis: Charts.axis('value', {min: v => Math.max(0, Math.floor(v.min - 2)), axisLabel: {formatter: pctAx}}),
    yAxis: Charts.axis('category', {data: sts.map(s => s.short), axisLabel: {color: css('--text'), fontSize: 11.5}}),
    series: [{type: 'bar', barMaxWidth: 16, data: sts.map(s => ({value: +(s.m * 100).toFixed(2), si: s.i,
      itemStyle: {borderRadius: [0, 8, 8, 0], color: State.s.store === s.i ? css('--c3') : s.m < net ? css('--c4') : css('--c1'),
        opacity: State.s.store == null || State.s.store === s.i ? 1 : 0.35}})),
      label: {show: true, position: 'right', formatter: p => NF1.format(p.value) + '%', color: css('--muted'), fontSize: 11},
      markLine: {symbol: 'none', silent: true, lineStyle: {color: css('--dim'), type: 'dashed'}, label: {formatter: 'сеть', color: css('--muted')},
        data: [{xAxis: +(net * 100).toFixed(2)}]}}],
  }), p => State.set({store: State.s.store === p.data.si ? null : p.data.si}));
  const ch = OFFERS.filter(o => inScope(o) && o.status === 'PROPOSED').map(o => o.change_pct * 100);
  const bins = Array.from({length: 11}, (_, i) => i);
  Charts.set('c-hist', () => ({
    grid: {left: 8, right: 8, top: 16, bottom: 4, containLabel: true},
    tooltip: {trigger: 'axis', axisPointer: {type: 'shadow'}, formatter: ps => `рост цены на ${ps[0].name}: <b>${ps[0].value}</b> позиций`},
    xAxis: Charts.axis('category', {data: bins.map(b => b === 10 ? '10% и более' : `${b}–${b + 1}%`), splitLine: {show: false}}),
    yAxis: Charts.axis('value'),
    series: [{type: 'bar', barMaxWidth: 34, data: bins.map(b => ch.filter(v => b === 10 ? v >= 10 : v >= b && v < b + 1).length),
      itemStyle: {borderRadius: [8, 8, 0, 0], color: css('--c1')}}],
  }));
}

/* ---------- 2. Цены и рекомендации ---------- */
// по умолчанию — то, что нужно сделать: цены к пересмотру
let pStatus = ['PROPOSED'], pDev = [], tPrices, stChips, devChips;
function goPrices({status = [], dev = []} = {}) {
  pStatus = status; pDev = dev; stChips.set(status); devChips.set(dev);
  if (dev.length) $('#p-dev').closest('details').open = true;
  Nav.go('prices'); renderPrices();
}
function initPrices() {
  stChips = chipGroup('p-status', [], {multi: true, allLabel: 'Все', value: pStatus, onChange: v => { pStatus = v; renderPrices(); }});
  devChips = chipGroup('p-dev', [], {multi: true, allLabel: 'Любое', onChange: v => { pDev = v; renderPrices(); }});
  tPrices = new Table('t-prices', {
    cols: [{k: 'item_h', label: 'Товар и точка', t: 'html', w: 180},
      {k: 'current_price', label: 'Цена', t: 'rub'}, {k: 'rec_show', label: 'Новая', t: 'rub', na: '—', help: 'Предлагаемая цена — расчёт системы'},
      {k: 'chg_h', label: 'Изм.', t: 'html', help: 'В рублях и в процентах к текущей цене'},
      {k: 'mg_h', label: 'Маржа: сейчас → после', t: 'html', help: HELP.planMargin + ' ' + HELP.afterMargin},
      {k: 'reason_short', label: 'Причина', t: 'text', w: 180, help: 'Кратко; полное объяснение — в карточке позиции'}, {k: 'status_h', label: 'Статус', t: 'html', help: 'Нажмите на строку — в карточке объяснено, что означает статус'}],
    rows: OFFERS, page: 20, search: ['product_name', 'sku', 'store', 'cat'], onRow: offerCard, csv: 'recommendations', sort: 'prio', dir: 1,
  });
}
function renderPrices() {
  const scope = OFFERS.filter(inScope);
  $$('.recs-eyebrow').forEach(el => el.textContent = `Расчёт на ${F.date(D.meta.as_of)} · ${State.s.store != null ? STORES[State.s.store].name : 'все точки'} · ${State.s.cat != null ? CATS[State.s.cat].name : 'все категории'}`);
  $('#asof').textContent = F.date(D.meta.as_of);
  stChips.update(STATUS_ORDER.map(s => ({v: s, label: STATUS[s].l, n: scope.filter(o => o.status === s).length,
    cls: s === 'BLOCKED' ? 'bad' : s === 'CONFLICT' ? 'warn' : s === 'NO_CHANGE' ? 'ok' : ''})));
  devChips.update(DEV_ORDER.map(k => ({v: k, label: DEV[k].l, n: scope.filter(o => o.dev === k).length, cls: DEV[k].cls})));
  const pred = o => inScope(o) && (!pStatus.length || pStatus.includes(o.status)) && (!pDev.length || pDev.includes(o.dev));
  tPrices.filter(pred);
}

/* ---------- Карточка изменения цены ---------- */
function constraints(o) {  // какие ограничения правила определяют цену (тот же расчёт, что в pmc.pricing)
  const r = ruleFor(o, GLOBAL_DEFAULT);
  if (o.unit_cost == null || o.unit_cost <= 0 || o.current_price == null) return null;
  const pTarget = roundStep(o.unit_cost / (1 - r.tm), r.step, r.mode), pMin = roundStep(o.unit_cost / (1 - r.mm), r.step, 'up');
  const hi = roundStep(o.current_price * (1 + r.mc), r.step, 'down');
  return {r, pTarget, pMin, hi};
}
function offerCard(o) {
  const hist = D.price_hist[`${STORES[o.si].id}|${o.product_id}`] || [], cost = D.cost_hist[String(o.product_id)] || [];
  const al = ALERTS.filter(a => a.product_id === o.product_id && (a.si == null || a.si === o.si) && OPEN(a));
  const c = constraints(o), proposed = o.status === 'PROPOSED';
  const step = (n, label, value) => `<div class="step"><span class="sn">${n}</span><div><div class="sl">${label}</div><div class="sv">${value}</div></div></div>`;
  let why = `<b>Почему.</b> ${esc(o.reason_text)}`;
  if (o.status === 'CONFLICT' && c) {
    why = `<b>Почему нет допустимой цены.</b> Два ограничения правила несовместимы:<br>
      • минимальная маржа ${F.pct(c.r.mm)} требует цену <b>не ниже ${F.rub(c.pMin)}</b>;<br>
      • лимит изменения ±${F.pct(c.r.mc)} от текущих ${F.rub(o.current_price)} разрешает <b>не выше ${F.rub(c.hi)}</b>.<br>
      ${F.rub(c.pMin)} > ${F.rub(c.hi)} — система не выбирает цену сама. Нужно решение: задать цену вручную или пересмотреть правило.`;
  }
  const risks = [];
  if (proposed) risks.push('Рост цены может снизить продажи — реакция спроса не моделируется.');
  if (proposed && o.network_median) {
    const d = (o.recommended_price - o.network_median) / o.network_median;
    risks.push(`Новая цена ${d >= 0 ? 'выше' : 'ниже'} медианы сети (${F.rub(o.network_median)}) на ${F.num1(Math.abs(d) * 100)}%.`);
  }
  if (proposed && o.m_after != null && o.target_margin != null && o.m_after < o.target_margin) risks.push('Целевая маржа не достигается за один шаг из-за лимита изменения цены — потребуется следующий пересмотр.');
  if (o.dev === 'below_cost') risks.push(`Сейчас цена ниже себестоимости на ${F.rub2(o.unit_cost - o.current_price)} — каждая продажа убыточна.`);
  if (o.dev === 'below_min' && !proposed) risks.push(`Маржа ${F.pct(o.plan_margin)} ниже минимальной ${F.pct(o.min_margin)} — пока цена не изменена, позиция теряет маржу.`);
  if (o.cost_newer) risks.push('Себестоимость менялась после последнего изменения цены.');
  if (o.status === 'BLOCKED') risks.push('Расчёт по позиции недостоверен до исправления исходных данных.');
  if (o.status === 'ON_HOLD') risks.push('Изменение цены во время промоакции исказит её результат — поэтому отложено.');
  if (!risks.length) risks.push('Существенных рисков по данным расчёта не выявлено.');
  Drawer.open(`<div class="eyebrow"><span class="dot ${['below_cost', 'below_min', 'data_error'].includes(o.dev) ? 'bad' : ''}"></span>${esc(o.cat)} · ${esc(o.sku)}</div>
    <h2>${esc(o.product_name)}</h2><div class="meta">${esc(o.store)} · ${pill(o.status)} · ${SCOPE[o.rule_scope] || 'правило не найдено'}:
      цель ${F.pct(o.target_margin)}, минимум ${F.pct(o.min_margin)}</div>
    <div class="steps">
      ${step(1, 'Товар и точка', `${esc(o.product_name)} · ${esc(o.store)}`)}
      ${step(2, 'Текущая цена', `${F.rub(o.current_price)} <span class="hint">себестоимость ${F.rub2(o.unit_cost)}</span>`)}
      ${step(3, 'Рекомендуемая цена', proposed ? F.rub(o.recommended_price) : `<span class="na">${STATUS[o.status].l.toLowerCase()}</span>`)}
      ${step(4, 'Изменение', proposed ? `${o.change_abs >= 0 ? '+' : '−'}${F.rub(Math.abs(o.change_abs))} · ${F.spct(o.change_pct)}` : '<span class="na">—</span>')}
      ${step(5, 'Маржа сейчас', F.pct(o.plan_margin))}
      ${step(6, 'Маржа после изменения', proposed ? F.pct(o.m_after) : '<span class="na">—</span>')}
    </div>
    <div class="callout ${o.status === 'CONFLICT' ? 'warn' : o.status === 'BLOCKED' ? 'bad' : o.status === 'NO_CHANGE' ? 'ok' : ''}"><span class="sn" style="font-weight:800">7 · </span>${why}</div>
    <div class="callout warn"><b>8 · Ограничения и риски.</b><br>${risks.map(r => '• ' + r).join('<br>')}</div>
    <div class="callout"><b>9 · Статус решения:</b> не принято.
      <br><span class="hint">Рекомендованная цена — расчёт системы. Согласованная цена появляется после решения руководителя (в полной версии).
      Применённая цена в кассе сейчас — ${F.rub(o.current_price)}: система цены в кассе не меняет.</span></div>
    <div class="card-head" style="margin-top:14px"><h3>История цены и себестоимости, ₽</h3></div><div class="chart short" id="c-card" role="img" aria-label="История цены и себестоимости"></div>
    <div class="card-head" style="margin-top:10px"><h3>Цена этой позиции в точках сети, ₽</h3><span class="hint">розовым — эта точка</span></div><div class="chart short" id="c-peers" role="img" aria-label="Цены в точках сети"></div>
    ${al.length ? `<div class="callout bad"><b>Открытые предупреждения:</b> ${[...new Set(al.map(a => a.title))].join(', ')}</div>` : ''}`, () => {
    const end = D.meta.as_of;
    const steps = h => [...h, h.length ? [end, h[h.length - 1][1]] : null].filter(Boolean);
    Charts.set('c-card', () => ({
      grid: {left: 8, right: 12, top: 34, bottom: 4, containLabel: true}, legend: {top: 0, left: 0},
      tooltip: {trigger: 'axis', valueFormatter: F.rub2},
      xAxis: Charts.axis('time', {splitLine: {show: false}, axisLabel: {formatter: v => F.dshort(new Date(v).toISOString().slice(0, 10))}}),
      yAxis: Charts.axis('value', {min: 0}),
      series: [{name: 'Цена в точке', type: 'line', step: 'end', data: steps(hist), symbol: 'none', lineStyle: {width: 3, color: css('--c1')}},
        {name: 'Себестоимость', type: 'line', step: 'end', data: steps(cost), symbol: 'none', lineStyle: {width: 3, color: css('--c4')}},
        ...(proposed ? [{name: 'Рекомендуемая цена', type: 'line', data: [[end, o.recommended_price]], symbolSize: 10, itemStyle: {color: css('--c3')}}] : [])],
    }));
    const peers = OFFERS.filter(x => x.product_id === o.product_id && x.current_price != null).sort((a, b) => a.current_price - b.current_price);
    Charts.set('c-peers', () => ({
      grid: {left: 8, right: 12, top: 10, bottom: 4, containLabel: true},
      tooltip: {trigger: 'axis', axisPointer: {type: 'shadow'}, valueFormatter: F.rub},
      xAxis: Charts.axis('category', {data: peers.map(x => STORES[x.si].short), axisLabel: {rotate: 40, fontSize: 10}}),
      yAxis: Charts.axis('value', {min: v => Math.floor(v.min * 0.9)}),
      series: [{type: 'bar', barMaxWidth: 18, data: peers.map(x => ({value: x.current_price,
        itemStyle: {color: x.si === o.si ? css('--c3') : css('--c2'), borderRadius: [6, 6, 0, 0]}}))}],
    }));
  });
}

/* ---------- 3. Предупреждения ---------- */
let aLevel = null, aType = null, tAlerts, tTypes;
function alertRows() {  // предупреждения + информационные записи (не являются проблемами)
  const rows = ALERTS.filter(OPEN).map(a => {
    const info = ALERT_INFO[a.alert_code] || {lvl: a.severity === 'CRIT' ? 'crit' : 'warn'};
    return {...a, lvl: info.lvl, reliability: !!info.reliability, type: a.alert_code, store_l: a.store_name || 'Вся сеть', product_l: a.product_name || '—'};
  });
  OFFERS.filter(o => o.status === 'ON_HOLD').forEach(o => rows.push({lvl: 'info', type: 'ON_HOLD', title: 'Изменение цены отложено (промо)', si: o.si,
    store_l: o.store, product_l: o.product_name, product_id: o.product_id, occurrences: 1, description: o.reason_text}));
  const dup = D.dq.find(r => r[0] === 'DUPLICATE');
  if (dup) rows.push({lvl: 'info', type: 'DQ_DUPLICATE', title: 'Дубли строк при загрузке', si: null, store_l: 'Вся сеть', product_l: '—', occurrences: 1,
    description: `${dup[2]} повторяющихся строк исключены автоматически, на расчёт не влияют.`});
  return rows;
}
const ALL_ROWS = alertRows();
const TYPE_TITLE = {ON_HOLD: 'Изменение цены отложено (промо)', DQ_DUPLICATE: 'Дубли строк при загрузке'};
const TYPE_INFO = {ON_HOLD: {what: 'Товар в промоакции — изменение цены перенесено.', next: 'Действий не требуется.'},
  DQ_DUPLICATE: {what: 'Повторяющиеся строки исходных данных удалены при загрузке.', next: 'Действий не требуется.'}};
function initAlerts() {
  const lvlCol = r => `<span class="pill ${LEVEL[r.lvl].p}">${LEVEL[r.lvl].l}</span>`;
  tTypes = new Table('t-types', {
    cols: [{k: 'lvl_h', label: 'Уровень', t: 'html'}, {k: 'title', label: 'Что найдено', t: 'text', w: 200}, {k: 'n', label: 'Сколько', t: 'int'},
      {k: 'what', label: 'Что это значит', t: 'text', w: 260}, {k: 'next', label: 'Что сделать', t: 'text', w: 220}],
    rows: [], page: 12, sort: 'order', dir: 1, onRow: r => { aType = aType === r.type ? null : r.type; renderAlerts(); },
  });
  tAlerts = new Table('t-alerts', {
    cols: [{k: 'lvl_h', label: 'Уровень', t: 'html'}, {k: 'title', label: 'Тип', t: 'text', w: 170}, {k: 'store_l', label: 'Точка', t: 'text'},
      {k: 'product_l', label: 'Товар', t: 'text'}, {k: 'description', label: 'Описание', t: 'text', w: 300},
      {k: 'occurrences', label: 'Повторов', t: 'int', help: 'Сколько расчётов подряд проблема обнаруживалась'}],
    rows: ALL_ROWS.map(r => ({...r, lvl_h: lvlCol(r), order: ({crit: 0, warn: 10, info: 20}[r.lvl]) + (r.reliability ? 0 : 1)})), page: 12,
    search: ['product_l', 'description', 'title', 'store_l'], csv: 'alerts', sort: 'order', dir: 1,
    onRow: a => { const o = OFFERS.find(x => x.product_id === a.product_id && (a.si == null || x.si === a.si)); if (o) offerCard(o); },
  });
  $('#a-reset').onclick = () => { aLevel = null; aType = null; renderAlerts(); };
}
function renderAlerts() {
  const scope = ALL_ROWS.filter(alertInScope);
  $('#a-levels').innerHTML = ['crit', 'warn', 'info'].map(k => {
    const part = scope.filter(r => r.lvl === k), rel = part.filter(r => r.reliability).length;
    return `<div class="grp ${aLevel === k ? 'on' : ''}" data-l="${k}" style="--ac:var(${LEVEL[k].ac})" aria-pressed="${aLevel === k}">
      <div class="gt"><span class="pill ${LEVEL[k].p}">${LEVEL[k].l}</span></div><div class="gv">${F.int(part.length)}</div>
      <div class="gd">${LEVEL[k].d}${k === 'crit' && rel ? ` · из них <b>${rel}</b> — ошибки данных` : ''}</div></div>`;
  }).join('');
  $$('#a-levels .grp').forEach(c => clickable(c, () => { aLevel = aLevel === c.dataset.l ? null : c.dataset.l; aType = null; renderAlerts(); }));
  const byType = {};
  scope.filter(r => !aLevel || r.lvl === aLevel).forEach(r => {
    const t = byType[r.type] || (byType[r.type] = {type: r.type, lvl: r.lvl, title: r.title || TYPE_TITLE[r.type], n: 0, reliability: r.reliability});
    t.n++;
  });
  tTypes.setRows(Object.values(byType).map(t => {
    const info = ALERT_INFO[t.type] || TYPE_INFO[t.type] || {};
    return {...t, lvl_h: `<span class="pill ${LEVEL[t.lvl].p}">${LEVEL[t.lvl].l}</span>${t.reliability ? ' <span class="hint">достоверность</span>' : ''}`,
      what: info.what, next: info.next, order: ({crit: 0, warn: 100, info: 200}[t.lvl]) + (t.reliability ? 0 : 50) - t.n / 1000};
  }));
  const parts = [aLevel ? LEVEL[aLevel].l : null, aType ? (byType[aType]?.title || TYPE_TITLE[aType]) : null].filter(Boolean);
  $('#a-list-title').textContent = parts.length ? 'Список: ' + parts.join(' · ') : 'Список всех записей';
  $('#a-reset').hidden = !parts.length;
  tAlerts.filter(r => alertInScope(r) && (!aLevel || r.lvl === aLevel) && (!aType || r.type === aType));
}

/* ---------- 4. Что будет, если изменить цены: алгоритм цены в браузере ---------- */
const GLOBAL = D.rules.find(r => r.scope === 'global');
const GLOBAL_DEFAULT = {tm: GLOBAL.tm, mm: GLOBAL.mm, mc: GLOBAL.mc, step: GLOBAL.step, dec: GLOBAL.dec, all: false};
const SC = {...GLOBAL_DEFAULT, mode: 'fixed', e: -0.5};
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
  let mism = 0;
  OFFERS.forEach(o => {
    const r = recommendJS(o, ruleFor(o, GLOBAL_DEFAULT));
    const pyPrice = o.status === 'PROPOSED' ? o.recommended_price : null, jsPrice = r.st === 'PROPOSED' ? r.p : null;
    if (r.st !== o.status || pyPrice !== jsPrice) mism++;
  });
  return {offers: OFFERS.length, mismatches: mism};
};
let tScen;
function initScenarios() {
  const ctl = $('#s-ctl');
  const sl = (k, l, min, max, help) => `<div><label for="s-${k}">${l} ${tip(help)}<b id="sv-${k}"></b></label><input type="range" id="s-${k}" min="${min}" max="${max}" step="1"></div>`;
  ctl.innerHTML = sl('tm', 'Целевая маржа', 20, 70, 'К какой марже стремится цена. Чем выше, тем выше рекомендуемые цены.')
    + sl('mm', 'Минимальная маржа', 10, 60, 'Ниже этой маржи цена не может быть. Если лимит изменения не позволяет её достичь — конфликт.')
    + sl('mc', 'Лимит изменения цены', 2, 30, 'Насколько цена может измениться за один пересмотр, % от текущей.')
    + `<div><span class="sl" style="font-weight:700;font-size:13px">Шаг цены на витрине ${tip('Цена округляется вверх до шага: 10 ₽ → 237 ₽ станет 240 ₽.')}</span>
      <div class="seg mt" id="s-step" role="group" aria-label="Шаг цены">${[1, 5, 10, 50].map(v => `<button data-v="${v}">${v} ₽</button>`).join('')}</div></div>
    <label class="switch"><input type="checkbox" id="s-dec">Разрешить снижение цены</label>
    <label class="switch"><input type="checkbox" id="s-all">Одно правило для всех категорий ${tip('Иначе у категорий остаются свои правила, а меняется только правило сети.')}</label>
    <div class="eyebrow mt" style="margin-bottom:0"><span class="dot warn"></span>Как покупатели реагируют на цену</div>
    <div class="seg" id="s-mode" role="group" aria-label="Реакция спроса"><button data-v="fixed">Не реагируют (продажи те же)</button><button data-v="coef">Задать коэффициент</button></div>
    <div><label for="s-e">Коэффициент реакции ${tip('При −0,5 рост цены на 10% снижает продажи на 5%. Задаётся вручную, по данным не оценён.')}<b id="sv-e"></b></label>
      <input type="range" id="s-e" min="-3" max="0" step="0.1"></div>
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
  $('#s-reset').onclick = () => { Object.assign(SC, GLOBAL_DEFAULT); sync(); renderScenarios(); };
  sync();
  tScen = new Table('t-scen', {
    cols: [{k: 'product_name', label: 'Товар', t: 'text'}, {k: 'store', label: 'Точка', t: 'text'}, {k: 'current_price', label: 'Цена сейчас', t: 'rub'},
      {k: 's_price', label: 'Цена в расчёте', t: 'rub'}, {k: 's_chg', label: 'Изм., %', t: 'spct'}, {k: 's_status', label: 'Статус', t: 'html'},
      {k: 's_dgp', label: 'Δ прибыли, ₽ за 28 дн.', t: 'rub'}],
    rows: [], page: 10, onRow: offerCard, sort: 's_dgp', csv: 'scenario',
  });
  $('#s-assume').innerHTML = `<b>Допущения расчёта.</b> Объём продаж — фактические продажи позиции за 28 дней до ${F.date(D.meta.as_of)}.
    Себестоимость не меняется. Скидки и промо не моделируются. Позиции «нужно решение», «ошибка данных» и «промо» остаются по текущей цене.
    Меняется только правило сети; правила категорий действуют, пока не включено «Одно правило для всех категорий».`;
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
  const isDefault = ['tm', 'mm', 'mc', 'step', 'dec', 'all'].every(k => SC[k] === GLOBAL_DEFAULT[k]);
  const chk = isDefault && State.s.store == null && State.s.cat == null ? window.checkPricing() : null;
  $('#s-hero').innerHTML = `<div class="eyebrow">Валовая прибыль за 28 дней при новых ценах · расчёт, не прогноз</div>
    <div class="big">${F.rub(G1)} <span style="font-size:17px;opacity:.9">${G1 >= G0 ? '+' : '−'}${F.rub(Math.abs(G1 - G0))} к текущим ценам</span></div>
    <div class="row"><div>При текущих ценах<b>${F.rub(G0)}</b></div><div>Выручка<b>${F.rubc(R0)} → ${F.rubc(R1)}</b></div>
      <div>Маржинальность<b>${F.pct(ratio(G0, R0))} → ${F.pct(ratio(G1, R1))}</b></div><div>Изменится цен<b>${F.int(changed)}</b></div></div>
    ${chk ? `<div class="ok-pill">✓ Совпадает с расчётом системы: ${F.int(chk.offers - chk.mismatches)} из ${F.int(chk.offers)} рекомендаций</div>` : ''}
    ${State.s.store != null || State.s.cat != null ? '<div class="ok-pill">Учтены только позиции выбранных точки и категории</div>' : ''}`;
  Charts.set('c-sstatus', () => ({
    grid: {left: 8, right: 44, top: 6, bottom: 4, containLabel: true},
    tooltip: {trigger: 'axis', axisPointer: {type: 'shadow'}, valueFormatter: v => F.int(v) + ' позиций'},
    xAxis: Charts.axis('value', {splitNumber: 2}),
    yAxis: Charts.axis('category', {data: STATUS_ORDER.map(s => STATUS[s].l).reverse(), axisLabel: {color: css('--text'), fontSize: 11}}),
    series: [{type: 'bar', barMaxWidth: 18, label: {show: true, position: 'right', color: css('--muted')},
      data: STATUS_ORDER.slice().reverse().map(s => ({value: counts[s] || 0, itemStyle: {color: css(STATUS[s].c), borderRadius: [0, 8, 8, 0]}}))}],
  }));
  Charts.set('c-scats', () => ({
    legend: {top: 0, left: 0}, grid: {left: 8, right: 8, top: 34, bottom: 4, containLabel: true},
    tooltip: {trigger: 'axis', axisPointer: {type: 'shadow'}, valueFormatter: F.rub},
    xAxis: Charts.axis('category', {data: CATS.map(c => c.name), axisLabel: {color: css('--text'), interval: 0, fontSize: 10.5, formatter: v => v.replace(' и ', ' и\n')}}),
    yAxis: Charts.axis('value', {axisLabel: {formatter: v => NF1.format(v / 1e6) + ' млн'}}),
    series: [{name: 'При текущих ценах', type: 'bar', data: cat0.map(v => +v.toFixed(0)), itemStyle: {color: css('--c7'), borderRadius: [6, 6, 0, 0]}},
      {name: 'При новых ценах', type: 'bar', data: cat1.map(v => +v.toFixed(0)), itemStyle: {color: css('--c1'), borderRadius: [6, 6, 0, 0]}}],
  }));
  tScen.setRows(rows);
}

/* ---------- 5. Методика и история ---------- */
function initHistory() {
  $('#rules-hint').textContent = `версия v${D.meta.rule_version} · заблокирована после расчёта`;
  new Table('t-rules', {cols: [{k: 'lvl', label: 'Для чего', t: 'text'}, {k: 'tm', label: 'Цель', t: 'pct', help: 'Целевая маржа'},
    {k: 'mm', label: 'Минимум', t: 'pct', help: 'Минимальная маржа'}, {k: 'mc', label: 'Лимит', t: 'pct', help: 'Максимальное изменение цены за пересмотр'},
    {k: 'step', label: 'Шаг, ₽', t: 'int'}, {k: 'dec', label: 'Снижение', t: 'text'}],
  rows: D.rules.map(r => ({...r, lvl: r.scope === 'category' ? 'Категория «' + (CATS.find(c => c.id === r.id)?.name ?? r.id) + '»' : r.scope === 'global' ? 'Вся сеть (по умолчанию)' : SCOPE[r.scope],
    dec: r.dec ? 'разрешено' : 'запрещено'})), page: 10}).render();
  new Table('t-runs', {cols: [{k: 0, label: 'Запуск', t: 'text'}, {k: 1, label: 'Начало', t: 'text'}, {k: 2, label: 'Дата расчёта', t: 'text'},
    {k: 3, label: 'Версия правил', t: 'int'}, {k: 'st', label: 'Статус', t: 'text'}, {k: 5, label: 'Кто', t: 'text'}, {k: 6, label: 'Позиций', t: 'int'}],
  rows: D.runs.map(r => Object.assign([...r], {st: {done: 'выполнен', failed: 'ошибка', running: 'выполняется'}[r[4]] || r[4]})), page: 8}).render();
  new Table('t-events', {cols: [{k: 0, label: 'Время', t: 'text', w: 130}, {k: 'ev', label: 'Событие', t: 'text'}, {k: 2, label: 'Кто', t: 'text'},
    {k: 3, label: 'Описание', t: 'text', w: 420}], rows: D.events.map(e => Object.assign([...e], {ev: EVENT[e[1]] || e[1]})), page: 12, search: ['ev', 3]}).render();
  new Table('t-dq', {cols: [{k: 'n', label: 'Проверка', t: 'html'}, {k: 's', label: 'Уровень', t: 'html'}, {k: 2, label: 'Строк', t: 'int'}],
    rows: D.dq.map(r => Object.assign([...r], {n: `${DQ[r[0]] || esc(r[0])} <span class="hint">${r[0]}</span>`,
      s: r[1] === 'CRIT' ? '<span class="pill p-bad">Исключено из расчёта</span>' : '<span class="pill p-vio">Информация</span>'})), page: 10}).render();
  $('#m-method').innerHTML = `<h3 style="margin-top:0">Как система рассчитывает цену</h3>
    <ol>
      <li><b>Цена для целевой маржи:</b> <span class="formula">цена = себестоимость / (1 − целевая маржа)</span>, округление вверх до шага.</li>
      <li><b>Не ниже минимальной маржи:</b> <span class="formula">мин. цена = ⌈себестоимость / (1 − минимальная маржа)⌉</span></li>
      <li><b>Снижение цены</b> — только если правило разрешает; иначе «Цена в норме».</li>
      <li><b>Лимит изменения:</b> цена меняется не больше чем на заданный % за пересмотр. Если лимит не даёт достичь минимальной маржи — «Нужно решение вручную».</li>
      <li><b>Промо:</b> если товар в акции, изменение откладывается.</li>
      <li><b>Ошибки данных</b> (нет себестоимости, цена ≤ 0, товар выведен) — расчёт не выполняется.</li>
    </ol>
    <p>Приоритет правил: товар → категория → точка → вся сеть. Расчёт в Python на Decimal; браузерная версия сверяется с ним.</p>
    <h3>Показатели</h3>
    <ul><li><b>Валовая маржинальность</b> — ${HELP.margin}</li><li><b>Валовая прибыль</b> — ${HELP.gp}</li>
      <li><b>Плановая маржа</b> — ${HELP.planMargin}</li><li><b>Отклонение от сети</b> — разница цены в точке и медианы цен этой позиции по сети; сигнал — больше 15%.</li></ul>`;
  $('#m-limits').innerHTML = `<h3 style="margin-top:0">Ограничения модели</h3>
    <ul><li>Данные синтетические, сгенерированы для учебного проекта.</li>
      <li>Маржа валовая: аренда, персонал и логистика не учитываются.</li>
      <li>Реакция спроса на цену не оценена по данным — сценарии не являются прогнозом.</li>
      <li>Система не меняет цены в кассе; согласование и журнал решений — в полной версии (Streamlit + PostgreSQL).</li>
      <li>Демо-версия статичная: показывает результат последнего расчёта на ${F.date(D.meta.as_of)} и не выполняет серверных операций.</li></ul>`;
}

/* ---------- Запуск ---------- */
shell();
buildFilters();
initPrices(); initAlerts(); initScenarios(); initHistory();
$('#cnt-recs').textContent = F.int(OFFERS.filter(o => o.status === 'PROPOSED').length);
$('#foot').innerHTML = `${esc(D.meta.note)} Статичная демо-версия: данные выгружены из базы проекта (расчёт на ${F.date(D.meta.as_of)}), собрано ${D.meta.built}.
  Согласование рекомендаций и пересчёт по новым данным — в полной версии (запуск через Docker, см. README).<br>Канаева Любовь · учебный проект бизнес-аналитика · <a href="https://github.com/lubancho/price-margin-control" target="_blank" rel="noopener">Код и документация на GitHub →</a>`;
function renderAll() { syncFilters(); renderOverview(); renderPrices(); renderAlerts(); renderMargin(); renderScenarios(); }
State.on(renderAll);
const navGo = Nav.go.bind(Nav);
Nav.go = (k, push) => { navGo(k, push); syncFilters(); };
// Старые ссылки (#recs, #alerts, #analytics, #history) ведут на нужную страницу новой структуры.
const ALIAS = {recs: 'prices', alerts: 'problems', analytics: 'margin', history: 'method'};
Nav.fromHash = function () {
  const k = (location.hash || '').slice(1);
  if (ALIAS[k]) {
    history.replaceState(null, '', '#' + ALIAS[k]);
    Nav.go(ALIAS[k], false);
    return;
  }
  Nav.go(document.getElementById('v-' + k) ? k : 'overview', false);
};
Nav.init();
renderAll();
