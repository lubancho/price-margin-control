"""Набор компонентов для статичной демо-версии дашборда (GitHub Pages).

Простыми словами: берём уже посчитанные кодом проекта таблицы и графики и собираем из них
одну HTML-страницу с вкладками. Страница работает без сервера и базы: данные встроены в неё,
таблицы фильтруются и сортируются в браузере, графики Plotly интерактивные.

Файл одинаковый в обоих проектах.
"""

from __future__ import annotations

import html
import json
import shutil
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.offline import get_plotlyjs

DEMO_NOTE = "Демонстрационные синтетические данные. Не являются показателями реальной компании."


PALETTE = {
    "accent": "#3E5C76",
    "accent2": "#7F9BB3",
    "accent3": "#A3B8C9",
    "crit": "#B3261E",
    "warn": "#B26A00",
    "ok": "#2E7D32",
    "muted": "#8A97A3",
    "grid": "#E1E6EB",
}


def style(fig: go.Figure) -> go.Figure:
    """Единое спокойное оформление графиков (как в приложении)."""
    fig.update_layout(
        template="plotly_white",
        font={"family": "Inter, Segoe UI, sans-serif", "size": 13, "color": "#1F2933"},
        colorway=[
            PALETTE["accent"],
            PALETTE["accent2"],
            PALETTE["accent3"],
            PALETTE["warn"],
            PALETTE["ok"],
        ],
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin={"l": 10, "r": 10, "t": 44, "b": 10},
        legend={"orientation": "h", "y": -0.2},
        separators=", ",
        hoverlabel={"bgcolor": "white"},
    )
    fig.update_xaxes(gridcolor=PALETTE["grid"], zeroline=False)
    fig.update_yaxes(gridcolor=PALETTE["grid"], zeroline=False)
    return fig


def _plain(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime | pd.Timestamp):
        return v.strftime("%d.%m.%Y %H:%M")
    if isinstance(v, date):
        return v.strftime("%d.%m.%Y")
    if isinstance(v, float) and pd.isna(v):
        return None
    if hasattr(v, "item") and not isinstance(v, str):
        return v.item()
    if v is pd.NA:
        return None
    return v


def records(df: pd.DataFrame, cols: list[str]) -> list[list]:
    return [[_plain(v) for v in row] for row in df[cols].itertuples(index=False, name=None)]


class Page:
    """Страница с вкладками. Каждая вкладка — набор блоков HTML; графики и таблицы — через методы."""

    def __init__(self, title: str, subtitle: str, footer_extra: str = ""):
        self.title, self.subtitle, self.footer_extra = title, subtitle, footer_extra
        self.tabs: list[tuple[str, str, list[str]]] = []
        self.figures: dict[str, dict] = {}
        self.tables: dict[str, dict] = {}
        self.data: dict[str, object] = {}
        self.js = ""
        self._n = 0

    def _id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    def tab(self, key: str, title: str) -> list[str]:
        blocks: list[str] = []
        self.tabs.append((key, title, blocks))
        return blocks

    # --- блоки ---------------------------------------------------------------------------------
    @staticmethod
    def kpis(items: list[tuple[str, str, str | None]]) -> str:
        cards = "".join(
            f"<div class='kpi'><div class='kpi-l'>{html.escape(label)}</div><div class='kpi-v'>{html.escape(value)}</div>"
            + (f"<div class='kpi-d'>{html.escape(note)}</div>" if note else "")
            + "</div>"
            for label, value, note in items
        )
        return f"<div class='kpis'>{cards}</div>"

    @staticmethod
    def note(text: str, kind: str = "") -> str:
        return f"<div class='box {kind}'>{text}</div>"

    @staticmethod
    def h(text: str) -> str:
        return f"<h3>{html.escape(text)}</h3>"

    @staticmethod
    def row(*blocks: str, widths: list[int] | None = None) -> str:
        widths = widths or [1] * len(blocks)
        inner = "".join(
            f"<div style='flex:{w} 1 0;min-width:300px'>{b}</div>"
            for b, w in zip(blocks, widths, strict=True)
        )
        return f"<div class='row'>{inner}</div>"

    def figure(self, fig: go.Figure, height: int = 340) -> str:
        fid = self._id("fig")
        style(fig)
        fig.update_layout(height=height)
        self.figures[fid] = json.loads(fig.to_json())
        return f"<div class='fig' id='{fid}' style='height:{height}px'></div>"

    def table(
        self,
        df: pd.DataFrame,
        columns: list[tuple[str, str, str]],
        filters: list[tuple[str, str]] | None = None,
        search: list[str] | None = None,
        page_size: int = 25,
        row_click: str | None = None,
        csv_name: str | None = None,
        note: str = "",
    ) -> str:
        """columns: (колонка, заголовок, тип: text|int|num1|num2|rub|rub2|pct|pp); filters: (колонка, подпись)."""
        tid = self._id("tbl")
        cols = [c for c, _, _ in columns]
        extra = [c for c in ([f for f, _ in (filters or [])] + (search or [])) if c not in cols]
        self.tables[tid] = {
            "columns": [{"key": c, "label": label, "type": t} for c, label, t in columns],
            "keys": cols + extra,
            "rows": records(df, cols + extra),
            "filters": [{"key": f, "label": label} for f, label in (filters or [])],
            "search": search or [],
            "pageSize": page_size,
            "rowClick": row_click,
            "csv": csv_name,
            "note": note,
        }
        return f"<div class='tbl' id='{tid}'></div>"

    # --- сборка --------------------------------------------------------------------------------
    def write(self, out_dir: Path, filename: str = "index.html") -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        plotly_path = out_dir / "plotly.min.js"
        if not plotly_path.exists():
            plotly_path.write_text(get_plotlyjs(), encoding="utf-8")
        nav = "".join(
            f"<button class='tab-btn' data-tab='{k}'>{html.escape(t)}</button>"
            for k, t, _ in self.tabs
        )
        sections = "".join(
            f"<section class='tab' id='tab-{k}'><h2>{html.escape(t)}</h2>{''.join(b)}</section>"
            for k, t, b in self.tabs
        )
        payload = json.dumps(
            {"figures": self.figures, "tables": self.tables, "data": self.data},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        page = (
            TEMPLATE.replace("%%TITLE%%", html.escape(self.title))
            .replace("%%SUBTITLE%%", self.subtitle)
            .replace("%%NAV%%", nav)
            .replace("%%SECTIONS%%", sections)
            .replace(
                "%%NOTE%%",
                html.escape(DEMO_NOTE) + (" " + self.footer_extra if self.footer_extra else ""),
            )
            .replace("%%PAYLOAD%%", payload.replace("</", "<\\/"))
            .replace("%%BUILT%%", datetime.now().strftime("%d.%m.%Y %H:%M"))
            .replace("%%EXTRA_JS%%", f"<script>{self.js}</script>")
        )
        path = out_dir / filename
        path.write_text(page, encoding="utf-8")
        return path


def copy_screens(src: Path, out_dir: Path) -> None:
    if src.exists():
        shutil.copytree(src, out_dir / "screenshots", dirs_exist_ok=True)


TEMPLATE = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%%TITLE%%</title>
<script src="plotly.min.js"></script>
<style>
:root{--bg:#F7F8FA;--card:#FFFFFF;--line:#E1E6EB;--text:#1F2933;--muted:#5B6B7A;--soft:#8A97A3;--accent:#3E5C76;
--accent2:#7F9BB3;--crit:#B3261E;--warn:#B26A00;--ok:#2E7D32;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 Inter,"Segoe UI",Roboto,Arial,sans-serif}
.layout{display:flex;min-height:100vh}
aside{width:250px;flex:0 0 250px;background:#EAEEF2;padding:20px 14px;position:sticky;top:0;height:100vh;overflow:auto}
aside .brand{font-weight:700;margin-bottom:4px}
aside .sub{color:var(--muted);font-size:12px;margin-bottom:18px}
.tab-btn{display:block;width:100%;text-align:left;border:0;background:transparent;padding:9px 10px;border-radius:6px;
font:inherit;color:var(--text);cursor:pointer;margin-bottom:2px}
.tab-btn:hover{background:#DDE3E9}.tab-btn.active{background:var(--accent);color:#fff}
aside .demo{margin-top:18px;font-size:12px;color:var(--muted);border-top:1px solid #D3DAE1;padding-top:12px}
main{flex:1;min-width:0;padding:22px 28px 30px;max-width:1500px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:20px;margin:6px 0 12px}h3{font-size:15px;margin:18px 0 8px}
.lead{color:var(--muted);margin-bottom:14px}
.tab{display:none}.tab.active{display:block}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:8px 0 14px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 14px;min-height:96px}
.kpi-l{font-size:12px;color:var(--muted)}.kpi-v{font-size:22px;font-weight:600;margin-top:4px}
.kpi-d{font-size:12px;color:var(--soft);margin-top:2px}
.row{display:flex;gap:14px;flex-wrap:wrap}
.fig{background:var(--card);border:1px solid var(--line);border-radius:8px;margin-bottom:14px}
.box{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:6px;
padding:10px 14px;margin:6px 0 12px}.box.warn{border-left-color:var(--warn)}.box.crit{border-left-color:var(--crit)}
.tbl{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px;margin-bottom:14px}
.tbl-tools{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:8px}
.tbl-tools select,.tbl-tools input{font:inherit;padding:5px 8px;border:1px solid #C9D1D9;border-radius:6px;background:#fff}
.tbl-tools input{min-width:200px}.tbl-tools .sp{flex:1}
.btn{font:inherit;padding:5px 10px;border:1px solid #C9D1D9;border-radius:6px;background:#fff;cursor:pointer}
.btn:hover{background:#F0F3F6}
.tbl-wrap{overflow:auto;max-height:560px}
table{border-collapse:collapse;width:100%;font-size:13px}
th{position:sticky;top:0;background:#F2F5F8;text-align:left;font-weight:600;padding:7px 8px;border-bottom:1px solid var(--line);
cursor:pointer;white-space:nowrap}
th.num,td.num{text-align:right}
td{padding:6px 8px;border-bottom:1px solid #EEF1F4;vertical-align:top}
tr.click{cursor:pointer}tr.click:hover td{background:#F5F8FB}
.tbl-foot{display:flex;justify-content:space-between;align-items:center;color:var(--muted);font-size:12px;margin-top:6px}
.badge{display:inline-block;padding:1px 7px;border-radius:10px;font-size:12px;background:#EEF1F4}
.b-crit{background:#F9E3E1;color:var(--crit)}.b-warn{background:#FBEEDB;color:var(--warn)}.b-ok{background:#E3F1E4;color:var(--ok)}
.modal-bg{display:none;position:fixed;inset:0;background:rgba(20,30,40,.45);z-index:20;align-items:flex-start;
justify-content:center;padding:40px 16px;overflow:auto}
.modal{background:#fff;border-radius:10px;max-width:820px;width:100%;padding:18px 20px;position:relative}
.modal .x{position:absolute;right:12px;top:8px;border:0;background:none;font-size:22px;cursor:pointer;color:var(--muted)}
.rec{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin-bottom:10px}
.rec .t{font-weight:600}.rec .grid{display:grid;grid-template-columns:1fr 1fr;gap:6px 18px;margin-top:8px}
.rec .grid b{color:var(--muted);font-weight:600}
pre{background:#F2F5F8;border:1px solid var(--line);border-radius:6px;padding:10px;overflow:auto;font-size:12px}
footer{color:var(--soft);font-size:12px;border-top:1px solid var(--line);margin-top:22px;padding-top:8px}
.slider{display:flex;gap:10px;align-items:center;margin:6px 0}
@media (max-width:900px){.layout{display:block}aside{position:static;width:auto;height:auto}
main{padding:16px}.rec .grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="layout">
<aside>
  <div class="brand">%%TITLE%%</div>
  <div class="sub">Демо-версия на GitHub Pages</div>
  %%NAV%%
  <div class="demo">Статичный снимок результатов работающей системы: данные и графики посчитаны кодом проекта.
  Пересчёт и изменение правил доступны в полной версии (запуск через Docker — см. README).<br><br>Собрано %%BUILT%%</div>
</aside>
<main>
  <h1>%%TITLE%%</h1>
  <div class="lead">%%SUBTITLE%%</div>
  %%SECTIONS%%
  <footer>%%NOTE%%</footer>
</main>
</div>
<div class="modal-bg" id="modal-bg"><div class="modal"><button class="x" onclick="closeModal()">×</button>
<div id="modal-body"></div></div></div>
<script id="payload" type="application/json">%%PAYLOAD%%</script>
<script>
const P = JSON.parse(document.getElementById('payload').textContent);
const fmtInt = new Intl.NumberFormat('ru-RU',{maximumFractionDigits:0});
const fmt1 = new Intl.NumberFormat('ru-RU',{minimumFractionDigits:1,maximumFractionDigits:1});
const fmt2 = new Intl.NumberFormat('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2});
function fmt(v,t){
  if(v===null||v===undefined||v==='') return t==='text'?'':'—';
  switch(t){
    case 'int': return fmtInt.format(v);
    case 'num1': return fmt1.format(v);
    case 'num2': return fmt2.format(v);
    case 'rub': return fmtInt.format(v)+' ₽';
    case 'rub2': return fmt2.format(v)+' ₽';
    case 'pct': return fmt1.format(v*100)+'%';
    case 'pp': return (v>0?'+':'')+fmt1.format(v*100)+' п. п.';
    case 'spct': return (v>0?'+':'')+fmt1.format(v*100)+'%';
    case 'html': return v;
    default: return String(v).replace(/&/g,'&amp;').replace(/</g,'&lt;');
  }
}
const isNum = t => ['int','num1','num2','rub','rub2','pct','pp','spct'].includes(t);
const drawn = {};
function drawFigures(scope){
  scope.querySelectorAll('.fig').forEach(el=>{
    const f = P.figures[el.id];
    if(!f) return;  // графики, которые рисует код страницы (например, сценарий)
    if(!drawn[el.id]){ Plotly.newPlot(el, f.data, f.layout, {displaylogo:false,responsive:true,
      modeBarButtonsToRemove:['lasso2d','select2d']}); drawn[el.id]=true; }
    else Plotly.Plots.resize(el);
  });
}
const tableState = {};
function renderTable(id){
  const T = P.tables[id], el = document.getElementById(id);
  const S = tableState[id] || (tableState[id] = {sort:null,dir:1,page:0,filters:{},q:''});
  if(!el.dataset.ready){
    let tools = '<div class="tbl-tools">';
    T.filters.forEach(f=>{
      const i = T.keys.indexOf(f.key);
      const vals = [...new Set(T.rows.map(r=>r[i]).filter(v=>v!==null&&v!==''))].sort((a,b)=>String(a).localeCompare(String(b),'ru'));
      tools += `<select data-f="${f.key}"><option value="">${f.label}: все</option>`+
        vals.map(v=>`<option value="${String(v).replace(/"/g,'&quot;')}">${fmt(v,'text')}</option>`).join('')+'</select>';
    });
    if(T.search.length) tools += '<input type="search" placeholder="Поиск…" data-q="1">';
    tools += '<span class="sp"></span>';
    if(T.csv) tools += '<button class="btn" data-csv="1">Скачать CSV</button>';
    tools += '</div><div class="tbl-wrap"><table><thead></thead><tbody></tbody></table></div><div class="tbl-foot"></div>';
    if(T.note) tools += `<div class="tbl-foot">${T.note}</div>`;
    el.innerHTML = tools;
    el.querySelectorAll('select[data-f]').forEach(s=>s.onchange=()=>{S.filters[s.dataset.f]=s.value;S.page=0;renderTable(id)});
    const q = el.querySelector('input[data-q]'); if(q) q.oninput=()=>{S.q=q.value.toLowerCase();S.page=0;renderTable(id)};
    const c = el.querySelector('[data-csv]'); if(c) c.onclick=()=>downloadCsv(id);
    el.dataset.ready='1';
  }
  let rows = filtered(id);
  if(S.sort!==null){
    const i = T.keys.indexOf(S.sort);
    rows = rows.slice().sort((a,b)=>{const x=a[i],y=b[i];
      if(x===null) return 1; if(y===null) return -1;
      return (typeof x==='number'&&typeof y==='number'?x-y:String(x).localeCompare(String(y),'ru'))*S.dir;});
  }
  const head = '<tr>'+T.columns.map(c=>`<th class="${isNum(c.type)?'num':''}" data-k="${c.key}">${c.label}${S.sort===c.key?(S.dir>0?' ▲':' ▼'):''}</th>`).join('')+'</tr>';
  const start = S.page*T.pageSize, pageRows = rows.slice(start,start+T.pageSize);
  const body = pageRows.map(r=>`<tr class="${T.rowClick?'click':''}" data-i="${T.rows.indexOf(r)}">`+
    T.columns.map(c=>`<td class="${isNum(c.type)?'num':''}">${fmt(r[T.keys.indexOf(c.key)],c.type)}</td>`).join('')+'</tr>').join('');
  el.querySelector('thead').innerHTML = head; el.querySelector('tbody').innerHTML = body ||
    `<tr><td colspan="${T.columns.length}" style="color:#8A97A3">Нет строк под выбранные фильтры</td></tr>`;
  el.querySelectorAll('th').forEach(th=>th.onclick=()=>{const k=th.dataset.k; if(S.sort===k) S.dir=-S.dir; else {S.sort=k;S.dir=1;} renderTable(id)});
  if(T.rowClick) el.querySelectorAll('tbody tr[data-i]').forEach(tr=>tr.onclick=()=>window[T.rowClick](T,T.rows[+tr.dataset.i]));
  const pages = Math.max(1,Math.ceil(rows.length/T.pageSize));
  el.querySelector('.tbl-foot').innerHTML = `<span>Строк: ${fmtInt.format(rows.length)} из ${fmtInt.format(T.rows.length)}</span>`+
    `<span><button class="btn" ${S.page===0?'disabled':''} data-p="-1">←</button> ${S.page+1} / ${pages} `+
    `<button class="btn" ${S.page>=pages-1?'disabled':''} data-p="1">→</button></span>`;
  el.querySelectorAll('[data-p]').forEach(b=>b.onclick=()=>{S.page+=+b.dataset.p;renderTable(id)});
}
function filtered(id){
  const T = P.tables[id], S = tableState[id];
  return T.rows.filter(r=>{
    for(const [k,v] of Object.entries(S.filters)){ if(v!=='' && String(r[T.keys.indexOf(k)])!==v) return false; }
    if(S.q){ return T.search.some(k=>String(r[T.keys.indexOf(k)]??'').toLowerCase().includes(S.q)); }
    return true;});
}
function downloadCsv(id){
  const T = P.tables[id];
  const lines = ['# '+document.querySelector('footer').textContent.trim(), T.columns.map(c=>c.label).join(';')];
  filtered(id).forEach(r=>lines.push(T.columns.map(c=>{let v=r[T.keys.indexOf(c.key)];
    if(v===null) return ''; if(typeof v==='number') return String(v).replace('.',',');
    return '"'+String(v).replace(/<[^>]+>/g,'').replace(/"/g,'""')+'"';}).join(';')));
  const blob = new Blob(['﻿'+lines.join('\n')],{type:'text/csv;charset=utf-8'});
  const a = document.createElement('a'); a.href=URL.createObjectURL(blob); a.download=T.csv+'.csv'; a.click();
}
function openModal(html, figs){ document.getElementById('modal-body').innerHTML = html;
  document.getElementById('modal-bg').style.display='flex';
  (figs||[]).forEach(([el,data,layout])=>Plotly.newPlot(el,data,layout,{displaylogo:false,responsive:true})); }
function closeModal(){ document.getElementById('modal-bg').style.display='none'; }
document.getElementById('modal-bg').onclick = e=>{ if(e.target.id==='modal-bg') closeModal(); };
function showTab(key){
  document.querySelectorAll('.tab').forEach(s=>s.classList.toggle('active', s.id==='tab-'+key));
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.toggle('active', b.dataset.tab===key));
  const sec = document.getElementById('tab-'+key);
  sec.querySelectorAll('.tbl').forEach(t=>{ if(P.tables[t.id]) renderTable(t.id); }); drawFigures(sec);
  if(window['onTab_'+key]) window['onTab_'+key]();
  history.replaceState(null,'','#'+key); window.scrollTo(0,0);
}
document.querySelectorAll('.tab-btn').forEach(b=>b.onclick=()=>showTab(b.dataset.tab));
window.addEventListener('hashchange',()=>{const k=location.hash.slice(1); if(document.getElementById('tab-'+k)) showTab(k);});
</script>
%%EXTRA_JS%%
<script>
const first = document.querySelector('.tab-btn').dataset.tab;
showTab((location.hash||'').slice(1) && document.getElementById('tab-'+location.hash.slice(1)) ? location.hash.slice(1) : first);
</script>
</body>
</html>
"""
