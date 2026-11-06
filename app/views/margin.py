"""Разбор → «Где падает маржа»: сравнение точек; динамика выручки и маржи — в «Подробнее»."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from components import ui

from pmc import metrics
from pmc import repository as repo
from pmc.pricing import margin

ui.crumbs(("Сводка", "views/overview.py"), ("Маржа по точкам", None))
ui.page_header(
    "Маржа по точкам",
    "Точки с маржой ниже средней по сети выделены оранжевым. Динамика по дням — в «Подробнее».",
)

c1, _ = st.columns([2, 3])
with c1:
    d0, d1 = ui.period_filter("mg_period")


@st.cache_data(show_spinner=False, ttl=600)
def load(d0, d1, store_ids, grain):
    return metrics.enrich(repo.sales_aggregates(ui.get_conn(), d0, d1, grain, store_ids=store_ids))


bs = ui.to_display(load(d0, d1, None, "store")).sort_values("gross_margin")
net_m = float(load(d0, d1, None, "network").iloc[0]["gross_margin"]) * 100
low = bs[bs["gross_margin"] * 100 < net_m]
if len(bs):
    worst = bs.iloc[0]
    st.markdown(
        f"#### Ниже средней по сети ({ui.fmt_pct(net_m / 100)}) — {len(low)} из {len(bs)} точек. "
        f"Ниже всех — {worst['store_name']}: {ui.fmt_pct(worst['gross_margin'])}."
    )
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
lo = max(0, float((bs["gross_margin"] * 100).min()) - 2) if len(bs) else 0
hi = float((bs["gross_margin"] * 100).max()) + 1.5 if len(bs) else 100
fig.update_layout(
    title="Маржинальность точек, %",
    height=420,
    xaxis={"title": None, "ticksuffix": "%", "range": [lo, hi]},
    yaxis_title=None,
)
st.plotly_chart(fig, width="stretch")
# --- Уровень 3: одна точка — какие цены система предлагает изменить ---------------------------
names = dict(zip(bs["store_id"], bs["store_name"], strict=True)) if len(bs) else {}
if names:
    si = st.selectbox("Открыть точку", list(names), format_func=names.get, key="mg_store")
    run = repo.last_done_run(ui.get_conn())
    recs = repo.recommendations(ui.get_conn(), run["run_id"]) if run is not None else pd.DataFrame()
    if not recs.empty:
        r = recs[(recs["store_id"] == si) & (recs["status"] != "NO_CHANGE")].copy()
        r["m_now"] = [
            margin(p, c) if c and p and not pd.isna(c) and not pd.isna(p) and c > 0 else None
            for p, c in zip(r["current_price"], r["unit_cost"], strict=True)
        ]
        r["m_after"] = [
            margin(p, c) if s == "PROPOSED" and c and p and not pd.isna(p) else None
            for p, c, s in zip(r["recommended_price"], r["unit_cost"], r["status"], strict=True)
        ]
        r["_o"] = r["status"].map({"PROPOSED": 0, "CONFLICT": 1, "BLOCKED": 2, "ON_HOLD": 3})
        r = r.sort_values(["_o", "m_now"], na_position="last")
        show = pd.DataFrame(
            {
                "Товар": r["product_name"],
                "Маржа сейчас": r["m_now"],
                "Маржа после": r["m_after"],
                "Новая цена, ₽": r["recommended_price"].where(r["status"] == "PROPOSED"),
                "Статус": r["status"].map(ui.STATUS_LABEL),
            }
        ).head(8)
        fmts = {"Маржа сейчас": "pct", "Маржа после": "pct", "Новая цена, ₽": "rub"}
        st.dataframe(
            ui.styled(show, fmts), hide_index=True, width="stretch", column_config=ui.num_cfg(fmts)
        )
        if st.button(f"Все цены точки ({len(r)}) ›", type="primary"):
            st.session_state["pr_preset_store"] = [si]
            st.session_state["pr_preset_status"] = []
            st.switch_page("views/prices.py")
ui.link("views/scenarios.py", "Что если изменить правило ›")
with st.expander("Подробнее: выручка и маржинальность по дням"):
    stores = ui.cached_stores()
    sel = st.multiselect(
        "Точки",
        stores["store_id"],
        default=[ui.my_store()] if ui.my_store() else [],
        placeholder="Вся сеть",
        format_func=dict(zip(stores["store_id"], stores["name"], strict=True)).get,
    )
    store_ids = list(sel) or None
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
ui.footer()
