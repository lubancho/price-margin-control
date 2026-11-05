"""Сборка статичной демо-версии дашборда для GitHub Pages.

Запуск (нужна база с загруженными данными и выполненным расчётом — `pmc init-demo`):
    uv run python scripts/build_static_demo.py --out site

Все цифры считаются теми же модулями, что и в приложении (repository, metrics, alerts, scenarios).
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

sys.path.insert(0, str(Path(__file__).parent))
from static_site_kit import PALETTE, Page, copy_screens, records

from pmc import metrics
from pmc import repository as repo
from pmc.alerts import TITLES
from pmc.db import connect, fetch_df
from pmc.pricing import margin

STATUS = {
    "PROPOSED": "Предложено",
    "NO_CHANGE": "Без изменений",
    "CONFLICT": "Конфликт",
    "BLOCKED": "Заблокировано",
    "ON_HOLD": "Отложено (промо)",
    "APPROVED": "Согласовано",
    "REJECTED": "Отклонено",
    "SUPERSEDED": "Заменено",
}
BADGE = {
    "CONFLICT": "b-warn",
    "BLOCKED": "b-crit",
    "PROPOSED": "",
    "ON_HOLD": "",
    "NO_CHANGE": "b-ok",
}
SCOPE = {"global": "Вся сеть", "category": "Категория", "store": "Точка", "product": "Товар"}
DEV = {
    "below_cost": "Цена ниже себестоимости",
    "below_min": "Ниже минимальной маржи",
    "below_target": "Ниже целевой маржи",
    "network": "Отклонение от медианы сети",
    "data_error": "Ошибка данных",
    "none": "Без отклонений",
}


def rub(x) -> str:
    v = float(x)
    return (
        (f"{v / 1e6:,.1f} млн ₽" if abs(v) >= 1e6 else f"{v:,.0f} ₽")
        .replace(",", " ")
        .replace(".", ",")
    )


def pct(x, signed=False) -> str:
    return (
        "н/д"
        if x is None
        else (f"{float(x) * 100:+.1f}%" if signed else f"{float(x) * 100:.1f}%").replace(".", ",")
    )


def build(out: Path) -> Path:
    conn = connect()
    _, d1 = repo.data_period(conn)
    d0 = d1 - timedelta(days=27)
    p0, p1 = d0 - timedelta(days=28), d0 - timedelta(days=1)
    run = repo.last_done_run(conn)
    recs = repo.recommendations(conn, run["run_id"])
    alerts = repo.alerts(conn)

    page = Page(
        "Контроль цен и маржинальности",
        f"Период продаж {d0:%d.%m.%Y} — {d1:%d.%m.%Y}; рекомендации — расчёт на {run['as_of_date']:%d.%m.%Y}, "
        f"правила v{run['rule_version']}.",
    )

    # --- Обзор ---------------------------------------------------------------------------------
    def tot(a, b):
        s = repo.sales_aggregates(conn, a, b).iloc[0]
        return metrics.from_sums(s.revenue, s.revenue_costed, s.cogs, s.net_qty, s.lines_no_cost)

    m, mp = tot(d0, d1), tot(p0, p1)
    share = metrics.below_min_share(recs.assign(is_active=True, cost_invalid=False))
    opened = alerts[alerts["status"].isin(["NEW", "IN_PROGRESS"])]
    ov = page.tab("overview", "Обзор")
    ov.append(
        page.kpis(
            [
                (
                    "Выручка",
                    rub(m.revenue),
                    pct((m.revenue - mp.revenue) / mp.revenue, True) + " к прошлым 28 дням",
                ),
                (
                    "Валовая прибыль",
                    rub(m.gross_profit),
                    pct((m.gross_profit - mp.gross_profit) / mp.gross_profit, True)
                    + " к прошлым 28 дням",
                ),
                (
                    "Маржинальность",
                    pct(m.gross_margin),
                    f"{(m.gross_margin - mp.gross_margin) * 100:+.1f}".replace(".", ",")
                    + " п. п. к прошлым 28 дням",
                ),
                (
                    "Ниже мин. маржи",
                    f"{share.below}",
                    f"{pct(share.share)} из {share.valid} предложений",
                ),
                (
                    "Предупреждения",
                    f"{len(opened)}",
                    f"{(opened['severity'] == 'CRIT').sum()} критичных",
                ),
                (
                    "Изменить цену",
                    f"{(recs['status'] == 'PROPOSED').sum()}",
                    f"расчёт на {run['as_of_date']:%d.%m.%Y}",
                ),
            ]
        )
    )
    daily = metrics.enrich(repo.sales_aggregates(conn, d0, d1, "day"))
    rev = daily["revenue"].map(float)
    f1 = go.Figure()
    f1.add_bar(x=daily["sale_date"], y=rev, name="Выручка за день", marker_color=PALETTE["accent2"])
    f1.add_scatter(
        x=daily["sale_date"],
        y=rev.rolling(7, min_periods=1).mean(),
        name="Среднее за 7 дней",
        line={"color": PALETTE["accent"], "width": 2.5},
    )
    f1.update_layout(title="Динамика выручки, ₽", yaxis_tickformat=",.0f")
    f1.update_xaxes(tickformat="%d.%m")
    f2 = go.Figure()
    f2.add_scatter(
        x=daily["sale_date"],
        y=daily["gross_margin"].map(lambda v: float(v) * 100),
        name="Маржинальность",
        line={"color": PALETTE["accent"], "width": 2},
    )
    gmin = recs.loc[recs["rule_scope"] == "global", "min_margin"]
    if len(gmin):
        f2.add_hline(
            y=float(gmin.iloc[0]) * 100,
            line_dash="dash",
            line_color=PALETTE["crit"],
            annotation_text="мин. маржа сети",
            annotation_position="bottom right",
        )
    f2.update_layout(title="Динамика валовой маржинальности, %", yaxis_ticksuffix="%")
    f2.update_xaxes(tickformat="%d.%m")
    ov.append(page.row(page.figure(f1, 320), page.figure(f2, 320)))
    st = metrics.enrich(repo.sales_aggregates(conn, d0, d1, "store")).sort_values("gross_margin")
    f3 = px.bar(
        x=st["gross_margin"].map(lambda v: float(v) * 100),
        y=st["store_name"],
        orientation="h",
        color_discrete_sequence=[PALETTE["accent"]],
    )
    f3.update_layout(
        title="Сравнение точек: валовая маржинальность, %",
        xaxis_title=None,
        yaxis_title=None,
        xaxis={"ticksuffix": "%", "range": [float(st["gross_margin"].min()) * 100 - 3, None]},
    )
    dist = opened.groupby(["alert_code", "severity"]).size().reset_index(name="n")
    dist["Тип"] = dist["alert_code"].map(TITLES)
    dist["Критичность"] = dist["severity"].map({"CRIT": "Критично", "WARN": "Предупреждение"})
    f4 = px.bar(
        dist.sort_values("n"),
        x="n",
        y="Тип",
        color="Критичность",
        orientation="h",
        color_discrete_map={"Критично": PALETTE["crit"], "Предупреждение": PALETTE["warn"]},
    )
    f4.update_layout(title="Открытые предупреждения по типам", xaxis_title=None, yaxis_title=None)
    ov.append(page.row(page.figure(f3, 430), page.figure(f4, 430), widths=[7, 5]))

    # --- Анализ цен ------------------------------------------------------------------------------
    fact = metrics.enrich(repo.sales_aggregates(conn, d0, d1, "offer"))[
        ["store_id", "product_id", "gross_margin"]
    ]
    df = recs.merge(fact, on=["store_id", "product_id"], how="left")
    df["plan_margin"] = [
        margin(p, c) if c is not None and not pd.isna(c) and c > 0 else None
        for p, c in zip(df["current_price"], df["unit_cost"], strict=True)
    ]
    dev_offers = set(
        alerts.query("alert_code == 'PRICE_DEVIATION'")[["store_id", "product_id"]].itertuples(
            index=False, name=None
        )
    )

    def deviation(r):
        if r.status == "BLOCKED" or r.plan_margin is None:
            return DEV["data_error"]
        if r.current_price < r.unit_cost:
            return DEV["below_cost"]
        if r.min_margin is not None and r.plan_margin < r.min_margin:
            return DEV["below_min"]
        if r.target_margin is not None and r.plan_margin < r.target_margin:
            return DEV["below_target"]
        return DEV["network"] if (r.store_id, r.product_id) in dev_offers else DEV["none"]

    df["deviation"] = [deviation(r) for r in df.itertuples(index=False)]
    shown = df["status"].isin(["PROPOSED", "APPROVED", "REJECTED"])
    df["rec_show"] = df["recommended_price"].where(shown)
    df["chg_abs"] = df["change_abs"].where(shown)
    df["chg_pct"] = df["change_pct"].where(shown)
    df["status_l"] = df["status"].map(STATUS)
    df["status_h"] = df["status"].map(
        lambda s: f"<span class='badge {BADGE.get(s, '')}'>{STATUS[s]}</span>"
    )
    df["scope_l"] = df["rule_scope"].map(SCOPE)
    order = {"BLOCKED": 0, "CONFLICT": 1, "PROPOSED": 2, "ON_HOLD": 3, "NO_CHANGE": 6}
    df = df.assign(
        _o=df["status"].map(order).fillna(5),
        _c=df["change_pct"].map(
            lambda v: -abs(float(v)) if v is not None and not pd.isna(v) else 0
        ),
    )
    df = df.sort_values(["_o", "_c", "store_id", "product_id"])
    counts = df["status"].value_counts()
    pr = page.tab("prices", "Анализ цен")
    pr.append(
        page.note(
            " · ".join(
                f"{STATUS[s]}: <b>{counts.get(s, 0)}</b>"
                for s in ("PROPOSED", "NO_CHANGE", "CONFLICT", "BLOCKED", "ON_HOLD")
            )
            + "<br>Нажмите на строку, чтобы открыть карточку товарного предложения."
        )
    )
    pr.append(
        page.table(
            df,
            [
                ("store_name", "Точка", "text"),
                ("product_name", "Товар", "text"),
                ("category_name", "Категория", "text"),
                ("unit_cost", "Себест., ₽", "num2"),
                ("current_price", "Цена, ₽", "int"),
                ("plan_margin", "Маржа", "pct"),
                ("gross_margin", "Факт", "pct"),
                ("rec_show", "Рекоменд., ₽", "int"),
                ("chg_abs", "Изм., ₽", "int"),
                ("chg_pct", "Изм., %", "spct"),
                ("reason_text", "Причина", "text"),
                ("status_h", "Статус", "html"),
            ],
            filters=[
                ("store_name", "Точка"),
                ("category_name", "Категория"),
                ("status_l", "Статус"),
                ("deviation", "Отклонение"),
            ],
            search=["product_name", "sku"],
            page_size=30,
            row_click="offerCard",
            csv_name="recommendations",
            note="«Маржа» — плановая при текущей цене; «Факт» — фактическая валовая маржинальность за период.",
        )
    )
    # Данные для карточки: истории цен и себестоимости.
    ph = fetch_df(
        conn,
        "SELECT store_id, product_id, valid_from, price FROM core.store_price ORDER BY 1, 2, 3",
    )
    ch = fetch_df(
        conn, "SELECT product_id, valid_from, unit_cost FROM core.product_cost ORDER BY 1, 2"
    )
    page.data["price_hist"] = {
        f"{s}|{p}": records(g, ["valid_from", "price"])
        for (s, p), g in ph.groupby(["store_id", "product_id"])
    }
    page.data["cost_hist"] = {
        str(p): records(g, ["valid_from", "unit_cost"]) for p, g in ch.groupby("product_id")
    }
    page.data["as_of"] = run["as_of_date"].strftime("%d.%m.%Y")
    med = df.groupby("product_id")["current_price"].apply(
        lambda s: [float(s.median()), float(s.min()), float(s.max())] if s.notna().any() else None
    )
    page.data["median"] = {str(k): v for k, v in med.items()}
    open_al = opened.groupby(["product_id"]).apply(
        lambda g: [
            [None if pd.isna(s) else int(s), TITLES[c]]
            for s, c in zip(g["store_id"], g["alert_code"], strict=True)
        ],
        include_groups=False,
    )
    page.data["alerts_by_product"] = {str(int(k)): v for k, v in open_al.items()}
    for col in (
        "store_id",
        "product_id",
        "sku",
        "scope_l",
        "min_margin",
        "target_margin",
        "deviation",
    ):
        page.tables[list(page.tables)[-1]]["keys"].append(col)
    t = page.tables[list(page.tables)[-1]]
    extra = ["store_id", "product_id", "sku", "scope_l", "min_margin", "target_margin", "deviation"]
    t["rows"] = [
        r
        + [
            None
            if v is None or (isinstance(v, float) and pd.isna(v))
            else (float(v) if isinstance(v, Decimal) else (int(v) if hasattr(v, "item") else v))
            for v in ex
        ]
        for r, ex in zip(t["rows"], df[extra].itertuples(index=False, name=None), strict=True)
    ]

    # --- Предупреждения ------------------------------------------------------------------------
    al = page.tab("alerts", "Предупреждения")
    al.append(
        page.kpis(
            [
                ("Критичные (открытые)", f"{(opened['severity'] == 'CRIT').sum()}", None),
                ("Предупреждения (открытые)", f"{(opened['severity'] == 'WARN').sum()}", None),
                ("Всего обнаружено", f"{len(alerts)}", "повторное обнаружение не создаёт дубль"),
            ]
        )
    )
    a = alerts.copy()
    a["sev"] = a["severity"].map({"CRIT": "🔴 Критично", "WARN": "🟠 Предупреждение"})
    a["type"] = a["alert_code"].map(TITLES)
    a["store"] = a["store_name"].fillna("Вся сеть")
    a["product"] = a["product_name"].fillna("—")
    a["status_l"] = a["status"].map(
        {"NEW": "Новое", "IN_PROGRESS": "В работе", "RESOLVED": "Решено", "DISMISSED": "Отклонено"}
    )
    al.append(
        page.table(
            a,
            [
                ("sev", "Критичность", "text"),
                ("type", "Тип", "text"),
                ("store", "Точка", "text"),
                ("product", "Товар", "text"),
                ("description", "Описание", "text"),
                ("first_seen_at", "Обнаружено", "text"),
                ("occurrences", "Повторов", "int"),
                ("status_l", "Статус", "text"),
            ],
            filters=[("sev", "Критичность"), ("type", "Тип"), ("store", "Точка")],
            search=["product", "description"],
            csv_name="alerts",
        )
    )
    dq = repo.dq_summary(conn)
    al.append(page.h("Проверки качества исходных данных при загрузке"))
    al.append(
        page.table(
            dq,
            [
                ("check_code", "Проверка", "text"),
                ("severity", "Критичность", "text"),
                ("issues", "Строк", "int"),
            ],
            page_size=20,
        )
    )

    # --- Сценарии (пересчёт спроса в браузере) ----------------------------------------------------
    offers = repo.offers_as_of(conn, run["as_of_date"])[
        ["store_id", "product_id", "category_name", "q0"]
    ]
    sc = recs[
        ["store_id", "product_id", "unit_cost", "current_price", "recommended_price", "status"]
    ].merge(offers, on=["store_id", "product_id"])
    page.data["scenario"] = records(
        sc, ["unit_cost", "current_price", "recommended_price", "status", "q0", "category_name"]
    )
    sn = page.tab("scenarios", "Сценарии")
    sn.append(
        page.note(
            "Сравнение «сейчас» и «после применения рекомендаций» по продажам за 28 дней до даты "
            "расчёта. Правила — как в расчёте; меняется только допущение о спросе. Пересчёт выполняется "
            "прямо в браузере по той же формуле, что в приложении."
        )
    )
    sn.append("""<div class='box'><div class='slider'><label><input type='radio' name='dm' value='fixed' checked>
      спрос фиксирован</label><label><input type='radio' name='dm' value='coef'> коэффициент реакции спроса</label></div>
      <div class='slider'>e = <input type='range' id='coef' min='-3' max='0' step='0.1' value='-0.5' disabled>
      <b id='coefv'>−0,5</b><span style='color:#5B6B7A'>при росте цены на 1% продажи меняются на e%</span></div></div>
      <div id='sc-table'></div>""")
    sn.append("<div class='fig dyn' id='sc-fig' style='height:320px'></div>")
    sn.append(
        page.note(
            "<b>Допущения</b><br>• Базовое количество — чистые продажи предложения за 28 дней до даты "
            "расчёта.<br>• Себестоимость неизменна.<br>• Промоакции и скидки не моделируются.<br>"
            "• «Конфликт», «Заблокировано», «Отложено» остаются по текущей цене.<br>"
            "• Коэффициент спроса задаётся вручную и не оценён по данным.<br>"
            "<b>Это сценарий, а не прогноз фактического результата.</b>",
            "warn",
        )
    )

    # --- История ---------------------------------------------------------------------------------
    hi = page.tab("history", "История")
    runs = repo.runs(conn)
    runs["status"] = runs["status"].map(
        {"done": "Завершён", "failed": "Ошибка", "running": "Выполняется"}
    )
    runs["run_id"] = runs["run_id"].str[:8]
    hi.append(page.h("Запуски расчёта"))
    hi.append(
        page.table(
            runs,
            [
                ("run_id", "Запуск", "text"),
                ("started_at", "Начало", "text"),
                ("as_of_date", "Дата расчёта", "text"),
                ("rule_version", "Правила", "int"),
                ("status", "Статус", "text"),
                ("triggered_by", "Кто запустил", "text"),
                ("offers_total", "Предложений", "int"),
            ],
            page_size=10,
        )
    )
    hi.append(page.h("Правила ценообразования (действующая версия)"))
    rules = fetch_df(
        conn,
        """
        SELECT CASE r.scope_type WHEN 'global' THEN 'Вся сеть' WHEN 'category' THEN 'Категория'
               WHEN 'store' THEN 'Точка' ELSE 'Товар' END AS level,
               COALESCE(c.name, 'вся сеть') AS obj, r.target_margin, r.min_margin, r.max_change, r.rounding_step,
               CASE r.allow_decrease WHEN true THEN 'да' ELSE 'нет' END AS dec
        FROM calc.pricing_rule r JOIN calc.rule_set rs USING (rule_set_id)
        LEFT JOIN core.category c ON r.scope_type = 'category' AND c.category_id = r.scope_id
        WHERE rs.version = %s ORDER BY r.rule_id""",
        (run["rule_version"],),
    )
    hi.append(
        page.table(
            rules,
            [
                ("level", "Уровень", "text"),
                ("obj", "Объект", "text"),
                ("target_margin", "Целевая маржа", "pct"),
                ("min_margin", "Мин. маржа", "pct"),
                ("max_change", "Лимит изменения", "pct"),
                ("rounding_step", "Шаг, ₽", "int"),
                ("dec", "Снижение", "text"),
            ],
            page_size=10,
        )
    )
    hi.append(page.h("Журнал событий"))
    hi.append(
        page.table(
            repo.events(conn),
            [
                ("event_at", "Время", "text"),
                ("event_type", "Событие", "text"),
                ("actor_role", "Роль", "text"),
                ("message", "Описание", "text"),
            ],
            page_size=15,
        )
    )

    page.js = SCRIPT
    path = page.write(out)
    copy_screens(Path(__file__).parents[1] / "docs" / "screenshots", out)
    print(f"Собрано: {path} ({path.stat().st_size / 1e6:.1f} МБ)")
    return path


SCRIPT = r"""
function offerCard(T, r){
  const g = k => r[T.keys.indexOf(k)];
  const key = g('store_id')+'|'+g('product_id');
  const ph = (P.data.price_hist[key]||[]), ch = (P.data.cost_hist[String(g('product_id'))]||[]);
  const asOf = P.data.as_of;
  const toISO = s => s.split('.').reverse().join('-');
  const line = (h,name,color) => ({x:[...h.map(v=>toISO(v[0])), toISO(asOf)], y:[...h.map(v=>v[1]), h.length?h[h.length-1][1]:null],
     type:'scatter', mode:'lines', line:{shape:'hv', color, width:2.5}, name});
  const med = P.data.median[String(g('product_id'))];
  const al = (P.data.alerts_by_product[String(g('product_id'))]||[]).filter(a=>a[0]===null||a[0]===g('store_id')).map(a=>a[1]);
  const dev = g('deviation');
  const crit = ['Цена ниже себестоимости','Ниже минимальной маржи','Ошибка данных'].includes(dev);
  let h = `<h3 style="margin-top:0">${fmt(g('product_name'),'text')} · ${g('sku')} · ${fmt(g('category_name'),'text')} · ${fmt(g('store_name'),'text')}</h3>`;
  if(crit) h += `<div class="box crit">Критично: ${dev}</div>`;
  h += `<div class="kpis"><div class="kpi"><div class="kpi-l">Себестоимость</div><div class="kpi-v">${fmt(g('unit_cost'),'rub2')}</div></div>
    <div class="kpi"><div class="kpi-l">Текущая цена</div><div class="kpi-v">${fmt(g('current_price'),'rub')}</div><div class="kpi-d">маржа ${fmt(g('plan_margin'),'pct')}</div></div>
    <div class="kpi"><div class="kpi-l">Рекомендация</div><div class="kpi-v">${g('rec_show')!==null?fmt(g('rec_show'),'rub'):g('status_l')}</div></div>
    <div class="kpi"><div class="kpi-l">Правило</div><div class="kpi-v" style="font-size:16px">${g('scope_l')||'—'}</div>
    <div class="kpi-d">мин. ${fmt(g('min_margin'),'pct')} · цель ${fmt(g('target_margin'),'pct')}</div></div></div>
    <div id="card-fig" style="height:240px"></div>
    <p><b>Почему:</b> ${fmt(g('reason_text'),'text')}</p>`;
  if(med) h += `<p style="color:#5B6B7A">Цена позиции в сети: медиана ${fmt(med[0],'rub')}, диапазон ${fmt(med[1],'rub')} — ${fmt(med[2],'rub')}</p>`;
  if(al.length) h += `<p><b>Открытые предупреждения:</b> ${[...new Set(al)].join(', ')}</p>`;
  h += `<p style="color:#8A97A3;font-size:12px">В полной версии руководитель согласует или отклоняет рекомендацию здесь же.</p>`;
  openModal(h, [['card-fig', [line(ph,'Цена в точке','#3E5C76'), line(ch,'Себестоимость','#B26A00')],
    {title:{text:'История цены и себестоимости'}, margin:{l:40,r:10,t:40,b:30}, yaxis:{rangemode:'tozero',title:{text:'₽'}},
     xaxis:{tickformat:'%d.%m'}, legend:{orientation:'h',y:-0.25}, separators:', ', font:{family:'Inter, Segoe UI, sans-serif'}}]]);
}
function scenario(){
  const mode = document.querySelector('input[name=dm]:checked').value;
  const e = parseFloat(document.getElementById('coef').value);
  document.getElementById('coef').disabled = mode==='fixed';
  document.getElementById('coefv').textContent = fmt1.format(e).replace('-','−');
  const cat = {}; let R0=0,R1=0,G0=0,G1=0,changed=0,n=0;
  P.data.scenario.forEach(([c,p0,rec,st,q0,cn])=>{
    if(c===null||c<=0||p0===null||p0<=0) return;
    const p1 = (['PROPOSED','APPROVED'].includes(st) && rec!==null) ? rec : p0;
    const dp = (p1-p0)/p0, q1 = mode==='fixed' ? q0 : Math.max(0, q0*(1+e*dp));
    const r0=p0*q0, r1=p1*q1, g0=(p0-c)*q0, g1=(p1-c)*q1;
    R0+=r0;R1+=r1;G0+=g0;G1+=g1;n++; if(p1!==p0) changed++;
    cat[cn]=cat[cn]||[0,0]; cat[cn][0]+=g0; cat[cn][1]+=g1;
  });
  const row=(l,a,b,t)=>`<tr><td>${l}</td><td class="num">${fmt(a,t)}</td><td class="num">${fmt(b,t)}</td><td class="num">${t==='pct'?fmt(b-a,'pp'):(b-a>0?'+':'')+fmt(b-a,t)}</td></tr>`;
  document.getElementById('sc-table').innerHTML = `<div class="tbl"><table><thead><tr><th>Показатель (28 дней)</th>
    <th class="num">Сейчас</th><th class="num">После</th><th class="num">Разница</th></tr></thead><tbody>
    ${row('Выручка',R0,R1,'rub')}${row('Валовая прибыль',G0,G1,'rub')}${row('Валовая маржинальность',G0/R0,G1/R1,'pct')}
    </tbody></table><div class="tbl-foot">Предложений в сценарии: ${fmtInt.format(n)}; изменится цен: ${fmtInt.format(changed)}</div></div>`;
  const names = Object.keys(cat).sort();
  Plotly.react('sc-fig', [
    {type:'bar', name:'Сейчас', x:names, y:names.map(k=>cat[k][0]), marker:{color:'#7F9BB3'}},
    {type:'bar', name:'После', x:names, y:names.map(k=>cat[k][1]), marker:{color:'#3E5C76'}}],
    {title:{text:'Валовая прибыль по категориям за 28 дней, ₽'}, barmode:'group', separators:', ',
     yaxis:{tickformat:',.0f', gridcolor:'#E1E6EB'}, paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)',
     margin:{l:60,r:10,t:44,b:30}, legend:{orientation:'h',y:-0.2}, font:{family:'Inter, Segoe UI, sans-serif'}},
    {displaylogo:false, responsive:true});
}
document.querySelectorAll('input[name=dm]').forEach(r=>r.onchange=scenario);
document.getElementById('coef').oninput = scenario;
function onTab_scenarios(){ scenario(); }
"""

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("site"))
    build(ap.parse_args().out)
