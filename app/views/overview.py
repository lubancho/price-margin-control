"""Экран «Обзор» (макет 1): карточки показателей и четыре графика."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from components import ui

from pmc import metrics
from pmc import repository as repo

ui.page_header(
    "Обзор сети",
    "Выручка и валовая прибыль — за выбранный период; цены и предупреждения — "
    "по последнему расчёту.",
)

c1, c2 = st.columns([2, 3])
with c1:
    d0, d1 = ui.period_filter("ov_period")
with c2:
    stores = ui.cached_stores()
    default = [ui.my_store()] if ui.my_store() else []
    sel = st.multiselect(
        "Точки",
        stores["store_id"],
        default=default,
        placeholder="Вся сеть",
        format_func=dict(zip(stores["store_id"], stores["name"], strict=True)).get,
    )
store_ids = list(sel) or None


@st.cache_data(show_spinner=False, ttl=600)
def load(d0, d1, store_ids, grain):
    return metrics.enrich(repo.sales_aggregates(ui.get_conn(), d0, d1, grain, store_ids=store_ids))


@st.cache_data(show_spinner=False, ttl=600)
def last_run_data():
    conn = ui.get_conn()
    run = repo.last_done_run(conn)
    if run is None:
        return None, pd.DataFrame(), pd.DataFrame()
    return run, repo.recommendations(conn, run["run_id"]), repo.alerts(conn, ["NEW", "IN_PROGRESS"])


days = (d1 - d0).days + 1
cur = load(d0, d1, store_ids, "network")
prev = load(d0 - timedelta(days=days), d0 - timedelta(days=1), store_ids, "network")
run, recs, alerts = last_run_data()
if store_ids and not recs.empty:
    recs = recs[recs["store_id"].isin(store_ids)]
    alerts = alerts[alerts["store_id"].isin(store_ids) | alerts["store_id"].isna()]

m = metrics.from_sums(
    *cur.iloc[0][["revenue", "revenue_costed", "cogs", "net_qty", "lines_no_cost"]]
)
mp = metrics.from_sums(
    *prev.iloc[0][["revenue", "revenue_costed", "cogs", "net_qty", "lines_no_cost"]]
)


def delta(a, b):
    r = metrics.safe_ratio(a - b, abs(b)) if b else None
    return None if r is None else ui.fmt_pct(r, signed=True)


DELTA_NOTE = "к предыдущему периоду той же длины"
k = st.columns(6)
k[0].metric(
    "Выручка", ui.fmt_rub(m.revenue), delta(m.revenue, mp.revenue), delta_description=DELTA_NOTE
)
k[1].metric(
    "Валовая прибыль",
    ui.fmt_rub(m.gross_profit),
    delta(m.gross_profit, mp.gross_profit),
    delta_description=DELTA_NOTE,
)
margin_delta = None
if m.gross_margin is not None and mp.gross_margin is not None:
    margin_delta = ui.fmt_pp(m.gross_margin - mp.gross_margin)
k[2].metric(
    "Маржинальность",
    ui.fmt_pct(m.gross_margin),
    margin_delta,
    delta_description=DELTA_NOTE,
    help=f"Валовая маржинальность. Рассчитана по {ui.fmt_pct(m.cost_coverage)} выручки: у строк без "
    "себестоимости на дату продажи маржа не считается (документ 04, М-02).",
)
if run is not None and not recs.empty:
    share = metrics.below_min_share(recs.assign(is_active=True, cost_invalid=False))
    k[3].metric(
        "Ниже мин. маржи",
        ui.fmt_int(share.below),
        f"{ui.fmt_pct(share.share)} из {ui.fmt_int(share.valid)}",
        delta_color="off",
        delta_arrow="off",
        help="Проблемные товарные предложения: активные, с маржой ниже минимальной (М-06). "
        f"Исключено из-за ошибок данных: {share.excluded}.",
    )
    n_crit = int((alerts["severity"] == "CRIT").sum())
    k[4].metric(
        "Предупреждения",
        ui.fmt_int(len(alerts)),
        f"{n_crit} критичных",
        delta_color="off",
        delta_arrow="off",
        help="Открытые предупреждения (новые и в работе)",
    )
    k[5].metric(
        "Изменить цену",
        ui.fmt_int((recs["status"] == "PROPOSED").sum()),
        f"на {run['as_of_date']:%d.%m.%Y}",
        delta_color="off",
        delta_arrow="off",
        help="Рекомендации по изменению цены в последнем расчёте",
    )
else:
    k[3].metric("Ниже мин. маржи", "н/д")
    k[4].metric("Предупреждения", "н/д")
    k[5].metric(
        "Изменить цену", "н/д", "расчёт не выполнялся", delta_color="off", delta_arrow="off"
    )

daily = ui.to_display(load(d0, d1, store_ids, "day"))
g1, g2 = st.columns(2)
with g1:
    fig = go.Figure()
    fig.add_bar(
        x=daily["sale_date"],
        y=daily["revenue"],
        name="Выручка за день",
        marker_color=ui.PALETTE["accent2"],
    )
    fig.add_scatter(
        x=daily["sale_date"],
        y=daily["revenue"].rolling(7, min_periods=1).mean(),
        name="Среднее за 7 дней",
        line={"color": ui.PALETTE["accent"], "width": 2.5},
    )
    fig.update_layout(title="Динамика выручки, ₽", height=320, yaxis_tickformat=",.0f")
    fig.update_xaxes(tickformat="%d.%m", hoverformat="%d.%m.%Y")
    st.plotly_chart(fig, width="stretch")
with g2:
    fig = go.Figure()
    fig.add_scatter(
        x=daily["sale_date"],
        y=daily["gross_margin"] * 100,
        name="Маржинальность за день",
        line={"color": ui.PALETTE["accent"], "width": 2},
    )
    if run is not None and not recs.empty:
        min_m = recs.loc[recs["rule_scope"] == "global", "min_margin"]
        if len(min_m):
            fig.add_hline(
                y=float(min_m.iloc[0]) * 100,
                line_dash="dash",
                line_color=ui.PALETTE["crit"],
                annotation_text="мин. маржа сети",
                annotation_position="bottom right",
            )
    fig.update_layout(title="Динамика валовой маржинальности, %", height=320, yaxis_ticksuffix="%")
    fig.update_xaxes(tickformat="%d.%m", hoverformat="%d.%m.%Y")
    st.plotly_chart(fig, width="stretch")

g3, g4 = st.columns([7, 5])
with g3:
    by_store = ui.to_display(load(d0, d1, store_ids, "store")).sort_values("gross_margin")
    fig = px.bar(
        by_store,
        x=by_store["gross_margin"] * 100,
        y="store_name",
        orientation="h",
        color_discrete_sequence=[ui.PALETTE["accent"]],
        custom_data=["revenue", "gross_profit"],
    )
    fig.update_traces(
        hovertemplate="%{y}<br>Маржинальность %{x:.1f}%<br>Выручка %{customdata[0]:,.0f} ₽"
        "<br>Валовая прибыль %{customdata[1]:,.0f} ₽<extra></extra>"
    )
    lo = max(0, float((by_store["gross_margin"] * 100).min()) - 3) if len(by_store) else 0
    fig.update_layout(
        title="Сравнение точек: валовая маржинальность, %",
        height=430,
        xaxis={"title": None, "ticksuffix": "%", "range": [lo, None]},
        yaxis_title=None,
    )
    st.plotly_chart(fig, width="stretch")
with g4:
    if alerts.empty:
        st.info("Открытых предупреждений нет")
    else:
        from pmc.alerts import TITLES

        dist = alerts.groupby(["alert_code", "severity"]).size().reset_index(name="n")
        dist["Тип"] = dist["alert_code"].map(TITLES)
        dist["Критичность"] = dist["severity"].map(ui.SEVERITY_LABEL)
        dist = dist.sort_values("n")
        fig = px.bar(
            dist,
            x="n",
            y="Тип",
            color="Критичность",
            orientation="h",
            color_discrete_map={
                "Критично": ui.PALETTE["crit"],
                "Предупреждение": ui.PALETTE["warn"],
            },
        )
        fig.update_layout(
            title="Открытые предупреждения по типам", height=430, xaxis_title=None, yaxis_title=None
        )
        st.plotly_chart(fig, width="stretch")

if m.lines_no_cost:
    st.caption(
        f"Строк продаж без себестоимости на дату продажи: {m.lines_no_cost}. Они учтены в выручке, "
        "но не в валовой прибыли."
    )
ui.footer()
