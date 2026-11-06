"""Аналитика → «Маржа и продажи»: динамика выручки и маржи, сравнение точек (перенесено с главной)."""

from __future__ import annotations

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from components import ui

from pmc import metrics
from pmc import repository as repo

ui.page_header(
    "Маржа и продажи", "Подробности к обзору: как менялись выручка и маржа и какие точки ниже сети."
)

c1, c2 = st.columns([2, 3])
with c1:
    d0, d1 = ui.period_filter("mg_period")
with c2:
    stores = ui.cached_stores()
    sel = st.multiselect(
        "Точки",
        stores["store_id"],
        default=[ui.my_store()] if ui.my_store() else [],
        placeholder="Вся сеть",
        format_func=dict(zip(stores["store_id"], stores["name"], strict=True)).get,
    )
store_ids = list(sel) or None


@st.cache_data(show_spinner=False, ttl=600)
def load(d0, d1, store_ids, grain):
    return metrics.enrich(repo.sales_aggregates(ui.get_conn(), d0, d1, grain, store_ids=store_ids))


daily = ui.to_display(load(d0, d1, store_ids, "day"))
fig = go.Figure()
fig.add_bar(
    x=daily["sale_date"],
    y=daily["revenue"],
    name="Выручка, ₽",
    marker_color=ui.PALETTE["accent3"],
    yaxis="y2",
)
fig.add_scatter(
    x=daily["sale_date"],
    y=daily["gross_margin"] * 100,
    name="Маржа, %",
    line={"color": ui.PALETTE["accent"], "width": 2.5},
)
fig.update_layout(
    title="Выручка и маржа по дням",
    height=360,
    yaxis={"ticksuffix": "%", "title": None},
    yaxis2={
        "overlaying": "y",
        "side": "right",
        "tickformat": ",.0f",
        "showgrid": False,
        "title": None,
    },
)
fig.update_xaxes(tickformat="%d.%m", hoverformat="%d.%m.%Y")
st.plotly_chart(fig, width="stretch")

bs = ui.to_display(load(d0, d1, None, "store")).sort_values("gross_margin")
net_m = float(load(d0, d1, None, "network").iloc[0]["gross_margin"]) * 100
fig = px.bar(
    bs, x=bs["gross_margin"] * 100, y="store_name", orientation="h", custom_data=["revenue"]
)
fig.update_traces(
    marker_color=[
        ui.PALETTE["warn"] if v * 100 < net_m else ui.PALETTE["accent"] for v in bs["gross_margin"]
    ],
    hovertemplate="%{y}<br>Маржа %{x:.1f}%<br>Выручка %{customdata[0]:,.0f} ₽<extra></extra>",
)
fig.add_vline(x=net_m, line_dash="dash", line_color=ui.PALETTE["muted"], annotation_text="сеть")
lo = max(0, float((bs["gross_margin"] * 100).min()) - 3) if len(bs) else 0
fig.update_layout(
    title="В каких точках маржа ниже средней, %",
    height=420,
    xaxis={"title": None, "ticksuffix": "%", "range": [lo, None]},
    yaxis_title=None,
)
st.plotly_chart(fig, width="stretch")
if store_ids:
    st.caption("Сравнение точек — по всей сети: фильтр точек к этому графику не применяется.")
ui.footer()
