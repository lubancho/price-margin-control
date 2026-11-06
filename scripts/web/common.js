/* Общий код демо-дашбордов: форматирование, состояние фильтров, навигация, графики ECharts, таблицы,
   боковая панель деталей. Файл одинаковый в обоих проектах. Данные — в window.DATA (data.js). */
'use strict';
const D = window.DATA;
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = v => String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

/* ---------- Форматирование (русская локаль) ---------- */
const NF0 = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 0});
const NF1 = new Intl.NumberFormat('ru-RU', {minimumFractionDigits: 1, maximumFractionDigits: 1});
const NF2 = new Intl.NumberFormat('ru-RU', {minimumFractionDigits: 2, maximumFractionDigits: 2});
const F = {
  int: v => v == null || isNaN(v) ? '—' : NF0.format(v),
  num1: v => v == null || isNaN(v) ? '—' : NF1.format(v),
  rub: v => v == null || isNaN(v) ? '—' : NF0.format(v) + ' ₽',
  rub2: v => v == null || isNaN(v) ? '—' : NF2.format(v) + ' ₽',
  rubc: v => v == null || isNaN(v) ? '—' : Math.abs(v) >= 1e6 ? NF1.format(v / 1e6) + ' млн ₽'
    : Math.abs(v) >= 1e4 ? NF0.format(v / 1e3) + ' тыс ₽' : NF0.format(v) + ' ₽',
  pct: v => v == null || isNaN(v) ? '—' : NF1.format(v * 100) + '%',
  spct: v => v == null || isNaN(v) ? '—' : (v > 0 ? '+' : v < 0 ? '−' : '') + NF1.format(Math.abs(v) * 100) + '%',
  pp: v => v == null || isNaN(v) ? '—' : (v > 0 ? '+' : v < 0 ? '−' : '') + NF1.format(Math.abs(v) * 100) + ' п. п.',
  date: iso => iso ? iso.slice(8, 10) + '.' + iso.slice(5, 7) + '.' + iso.slice(0, 4) : '—',
  dshort: iso => iso ? iso.slice(8, 10) + '.' + iso.slice(5, 7) : '',
};
const ratio = (a, b) => (b == null || b <= 0 || a == null) ? null : a / b;
/* Изменение к базе сравнения. Цвет — по смыслу для бизнеса: invert — рост плох (списания), neutral — без оценки. */
function deltaPill(cur, prev, {kind = 'rel', invert = false, neutral = false} = {}) {
  if (cur == null || prev == null) return '';
  const v = kind === 'rel' ? ratio(cur - prev, Math.abs(prev)) : cur - prev;
  if (v == null) return '';
  const good = invert ? v < 0 : v > 0;
  const cls = neutral || Math.abs(v) < 0.0005 ? 'flat' : good ? 'up' : 'down';
  const word = cls === 'flat' ? '' : good ? 'лучше: ' : 'хуже: ';
  return `<span class="delta ${cls}"><span class="sr">${word}</span>${kind === 'rel' ? F.spct(v) : F.pp(v)}</span>`;
}
/* Подсказка ⓘ: текст по наведению и по фокусу с клавиатуры */
const tip = (text, right = false) => `<button type="button" class="tip ${right ? 'r' : ''}" data-tip="${esc(text)}" aria-label="${esc(text)}">i</button>`;
const NA = '<span class="na">нет данных</span>';
/* Сделать элемент кнопкой для мыши и клавиатуры */
function clickable(el, fn) {
  if (!el) return;
  if (el.tagName !== 'BUTTON') { el.setAttribute('role', 'button'); el.tabIndex = 0; }
  el.onclick = fn;
  el.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fn(e); } };
}
/* Вкладки: <div class="tabs" id=…><button data-tab="a">…</button></div> + .tab#tab-a */
function tabs(id, onChange) {
  const box = document.getElementById(id);
  const set = key => {
    $$('button', box).forEach(b => { const on = b.dataset.tab === key; b.classList.toggle('on', on); b.setAttribute('aria-selected', on); });
    $$('button', box).forEach(b => document.getElementById('tab-' + b.dataset.tab).classList.toggle('on', b.dataset.tab === key));
    requestAnimationFrame(() => Charts.redrawAll());
    if (onChange) onChange(key);
  };
  box.setAttribute('role', 'tablist');
  $$('button', box).forEach(b => { b.setAttribute('role', 'tab'); b.onclick = () => set(b.dataset.tab); });
  set($$('button', box)[0].dataset.tab);
  return {set};
}

/* ---------- Тема и цвета ---------- */
const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const PAL = () => ['--c1', '--c2', '--c3', '--c4', '--c5', '--c6', '--c7'].map(css);
function setTheme(t) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem('theme', t); } catch (e) { /* браузер без хранилища */ }
  $$('.theme-label').forEach(el => el.textContent = t === 'dark' ? 'Светлая тема' : 'Тёмная тема');
  Charts.redrawAll();
}
function initTheme() {
  let t = null;
  try { t = localStorage.getItem('theme'); } catch (e) { /* нет хранилища */ }
  if (!t) t = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  document.documentElement.dataset.theme = t;
  $$('.theme-label').forEach(el => el.textContent = t === 'dark' ? 'Светлая тема' : 'Тёмная тема');
}

/* ---------- Графики ECharts ---------- */
const Charts = {
  reg: {},
  base() {
    return {
      color: PAL(), textStyle: {fontFamily: 'Manrope, Segoe UI, sans-serif', color: css('--text')},
      grid: {left: 12, right: 18, top: 34, bottom: 8, containLabel: true},
      tooltip: {trigger: 'axis', backgroundColor: css('--glass'), borderColor: css('--bd'),
        textStyle: {color: css('--fg'), fontSize: 12}, extraCssText: 'border-radius:12px;box-shadow:0 10px 30px rgba(70,50,170,.15)'},
      legend: {top: 0, right: 0, textStyle: {color: css('--muted'), fontSize: 12}, icon: 'roundRect', itemWidth: 12, itemHeight: 8},
      animationDuration: 500,
    };
  },
  axis(type = 'value', extra = {}) {
    const lbl = {color: css('--dim'), fontSize: 11};
    if (type === 'value') lbl.formatter = v => NF0.format(v);
    if (extra.axisLabel) { Object.assign(lbl, extra.axisLabel); extra = {...extra}; delete extra.axisLabel; }
    return Object.assign({type, axisLabel: lbl, axisLine: {lineStyle: {color: css('--bd')}}, axisTick: {show: false},
      splitLine: {lineStyle: {color: css('--bd'), type: 'dashed'}}}, extra);
  },
  set(id, build, onClick) {
    const el = document.getElementById(id);
    if (!el) return;
    let c = this.reg[id]?.chart;
    if (c && !c.isDisposed() && c.getDom() !== el) c.dispose();  // элемент пересоздан (карточка в боковой панели)
    if (!c || c.isDisposed()) {
      c = echarts.init(el, null, {renderer: 'canvas'});
      new ResizeObserver(() => c.resize()).observe(el);
    }
    this.reg[id] = {chart: c, build, onClick};
    c.off('click');
    if (onClick) { c.on('click', onClick); el.style.cursor = 'pointer'; }
    this.draw(id);
  },
  draw(id) {
    const r = this.reg[id];
    if (!r || !r.chart.getDom().offsetParent) return;  // скрытая вкладка — нарисуем при показе
    const b = this.base(), o = r.build();
    ['legend', 'tooltip', 'grid'].forEach(k => {  // настройки темы + настройки конкретного графика
      if (o[k] && !Array.isArray(o[k])) o[k] = Object.assign({}, b[k], o[k]);
    });
    r.chart.setOption(Object.assign(b, o), true);
    r.chart.resize();
  },
  redrawAll() { Object.keys(this.reg).forEach(id => this.draw(id)); },
};
const spark = (id, values, color) => Charts.set(id, () => ({
  grid: {left: 0, right: 0, top: 2, bottom: 0}, legend: {show: false}, tooltip: {show: false},
  xAxis: {type: 'category', show: false, data: values.map((_, i) => i)}, yAxis: {type: 'value', show: false, scale: true},
  series: [{type: 'line', data: values, smooth: true, symbol: 'none', lineStyle: {width: 2, color: color || css('--c1')},
    areaStyle: {color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
      {offset: 0, color: (color || css('--c1')) + '55'}, {offset: 1, color: (color || css('--c1')) + '00'}])}}],
}));

/* ---------- Состояние фильтров ---------- */
const State = {
  s: {},
  subs: [],
  set(patch) { Object.assign(this.s, patch); this.subs.forEach(f => f(this.s)); },
  on(f) { this.subs.push(f); },
};

/* ---------- Навигация ---------- */
const Nav = {
  views: [],
  init() {
    $$('.nav[data-view]').forEach(b => b.onclick = () => this.go(b.dataset.view));
    window.addEventListener('hashchange', () => this.fromHash());
    this.fromHash();
  },
  fromHash() {
    const k = (location.hash || '').slice(1);
    this.go(document.getElementById('v-' + k) ? k : $$('.nav[data-view]')[0].dataset.view, false);
  },
  go(key, push = true) {
    $$('.view').forEach(v => v.classList.toggle('on', v.id === 'v-' + key));
    $$('.nav[data-view]').forEach(b => { b.classList.toggle('on', b.dataset.view === key); b.toggleAttribute('aria-current', b.dataset.view === key); });
    if (push) history.replaceState(null, '', '#' + key);
    window.scrollTo({top: 0});
    requestAnimationFrame(() => Charts.redrawAll());
  },
};

/* ---------- Таблица ---------- */
class Table {
  /* cols: [{k, label, t: 'text'|'int'|'num1'|'rub'|'rub2'|'pct'|'spct'|'pp'|'html', w}] */
  constructor(el, {cols, rows, page = 25, search = [], onRow = null, csv = null, sort = null, dir = -1, empty = 'Нет строк'}) {
    Object.assign(this, {el: typeof el === 'string' ? document.getElementById(el) : el, cols, rows, page, search, onRow, csv,
      sortK: sort, dir, empty, q: '', p: 0, pred: () => true});
    this.el.innerHTML = `<div class="tbl-tools">${search.length ? '<input class="search" type="search" placeholder="Поиск по названию…">' : ''}
      <span class="sp"></span>${csv ? '<button class="btn" data-csv>Скачать CSV</button>' : ''}</div>
      <div class="tbl-wrap"><table><thead></thead><tbody></tbody></table></div><div class="tbl-foot"></div>`;
    const s = $('.search', this.el);
    if (s) s.oninput = () => { this.q = s.value.toLowerCase(); this.p = 0; this.render(); };
    const c = $('[data-csv]', this.el);
    if (c) c.onclick = () => this.download();
  }
  setRows(rows) { this.rows = rows; this.p = 0; this.render(); }
  filter(pred) { this.pred = pred; this.p = 0; this.render(); }
  current() {
    let r = this.rows.filter(this.pred);
    if (this.q) r = r.filter(x => this.search.some(k => String(x[k] ?? '').toLowerCase().includes(this.q)));
    if (this.sortK) {
      const k = this.sortK, d = this.dir;
      r = r.slice().sort((a, b) => {
        const x = a[k], y = b[k];
        if (x == null) return 1; if (y == null) return -1;
        return (typeof x === 'number' ? x - y : String(x).localeCompare(String(y), 'ru')) * d;
      });
    }
    return r;
  }
  cell(v, t, c = {}) {
    if (v == null || v === '' || (typeof v === 'number' && isNaN(v))) return t === 'html' && v === '' ? '' : c.na ? `<span class="na">${c.na}</span>` : NA;
    return t === 'html' ? v : t === 'text' ? esc(v) : F[t](v);
  }
  render() {
    const rows = this.current(), num = t => !['text', 'html'].includes(t);
    $('thead', this.el).innerHTML = '<tr>' + this.cols.map(c =>
      `<th class="${num(c.t) ? 'num' : ''}" data-k="${c.k}" tabindex="0" aria-sort="${this.sortK === c.k ? (this.dir > 0 ? 'ascending' : 'descending') : 'none'}" style="${c.w ? 'min-width:' + c.w + 'px' : ''}">${c.label}${c.help ? tip(c.help, num(c.t)) : ''}${this.sortK === c.k ? (this.dir > 0 ? ' ▲' : ' ▼') : ''}</th>`).join('') + '</tr>';
    const start = this.p * this.page, slice = rows.slice(start, start + this.page);
    $('tbody', this.el).innerHTML = slice.map((r, i) => `<tr class="${this.onRow ? 'click' : ''}" data-i="${start + i}" ${this.onRow ? 'tabindex="0"' : ''}>` +
      this.cols.map(c => `<td class="${num(c.t) ? 'num' : ''}">${this.cell(typeof c.k === 'function' ? c.k(r) : r[c.k], c.t, c)}</td>`).join('') + '</tr>').join('')
      || `<tr><td colspan="${this.cols.length}" class="empty">${this.empty}</td></tr>`;
    const sortBy = th => {
      const k = th.dataset.k; if (this.sortK === k) this.dir = -this.dir; else { this.sortK = k; this.dir = -1; } this.render();
    };
    $$('th', this.el).forEach(th => {
      th.onclick = e => { if (!e.target.closest('.tip')) sortBy(th); };
      th.onkeydown = e => { if ((e.key === 'Enter' || e.key === ' ') && !e.target.closest('.tip')) { e.preventDefault(); sortBy(th); } };
    });
    if (this.onRow) $$('tbody tr[data-i]', this.el).forEach(tr => clickable(tr, () => this.onRow(rows[+tr.dataset.i])));
    const pages = Math.max(1, Math.ceil(rows.length / this.page));
    $('.tbl-foot', this.el).innerHTML = `<span>Строк: ${F.int(rows.length)} из ${F.int(this.rows.length)}</span>
      <span class="pager"><button ${this.p === 0 ? 'disabled' : ''} data-p="-1" aria-label="Предыдущая страница">←</button> ${this.p + 1} / ${pages}
      <button ${this.p >= pages - 1 ? 'disabled' : ''} data-p="1" aria-label="Следующая страница">→</button></span>`;
    $$('[data-p]', this.el).forEach(b => b.onclick = () => { this.p += +b.dataset.p; this.render(); });
  }
  download() {
    const lines = ['# ' + D.meta.note, this.cols.map(c => c.label).join(';')];
    this.current().forEach(r => lines.push(this.cols.map(c => {
      const v = r[c.k];
      if (v == null) return '';
      if (typeof v === 'number') return String(v).replace('.', ',');
      return '"' + String(v).replace(/<[^>]+>/g, '').replace(/"/g, '""') + '"';
    }).join(';')));
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob(['﻿' + lines.join('\n')], {type: 'text/csv;charset=utf-8'}));
    a.download = this.csv + '.csv'; a.click();
  }
}

/* ---------- Чипы-переключатели ---------- */
function chipGroup(el, items, {multi = false, value = null, onChange, allLabel = null}) {
  el = typeof el === 'string' ? document.getElementById(el) : el;
  let cur = multi ? new Set(value || []) : value;
  const draw = () => {
    el.innerHTML = (allLabel ? `<button class="chip ${(multi ? cur.size === 0 : cur == null) ? 'on' : ''}" data-v="">${allLabel}</button>` : '') +
      items.map(it => `<button class="chip ${it.cls || ''} ${(multi ? cur.has(it.v) : cur === it.v) ? 'on' : ''}" data-v="${esc(it.v)}">${esc(it.label)}${it.n != null ? `<span class="n">${F.int(it.n)}</span>` : ''}</button>`).join('');
    $$('.chip', el).forEach(b => b.onclick = () => {
      const v = b.dataset.v === '' ? null : items.find(i => String(i.v) === b.dataset.v).v;
      if (multi) { if (v == null) cur.clear(); else cur.has(v) ? cur.delete(v) : cur.add(v); }
      else cur = cur === v ? null : v;
      draw(); onChange(multi ? [...cur] : cur);
    });
  };
  draw();
  return {set(v) { cur = multi ? new Set(v || []) : v; draw(); }, update(newItems) { items = newItems; draw(); }};
}

/* ---------- Боковая панель деталей ---------- */
/* Карточка открывается поверх списка; закрытие (×, фон, Esc) возвращает к тому же месту списка и фокусу. */
const Drawer = {
  back: null,
  open(html, after) {
    if (!$('#drawer').classList.contains('open')) this.back = document.activeElement;
    $('#drawer-body').innerHTML = html;
    $('#drawer').classList.add('open'); $('#drawer-bg').classList.add('open');
    $('#drawer').setAttribute('aria-hidden', 'false');
    $('#drawer').scrollTop = 0;
    setTimeout(() => $('#drawer .close').focus(), 50);
    if (after) requestAnimationFrame(after);
  },
  close() {
    $('#drawer').classList.remove('open'); $('#drawer-bg').classList.remove('open');
    $('#drawer').setAttribute('aria-hidden', 'true');
    if (this.back && this.back.focus) this.back.focus();
  },
};
document.addEventListener('keydown', e => { if (e.key === 'Escape') Drawer.close(); });

/* ---------- Каркас страницы ---------- */
function shell() {
  $('#drawer-bg').onclick = () => Drawer.close();
  $('#drawer .close').onclick = () => Drawer.close();
  $$('.theme-btn').forEach(b => b.onclick = () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'));
  $('#drawer').setAttribute('role', 'dialog'); $('#drawer').setAttribute('aria-modal', 'true'); $('#drawer').setAttribute('aria-hidden', 'true');
  initTheme();
  const ld = document.getElementById('loading'); if (ld) ld.remove();
  // графики внутри раскрываемых блоков рисуются при раскрытии
  document.addEventListener('toggle', e => { if (e.target.open) requestAnimationFrame(() => Charts.redrawAll()); }, true);
}
