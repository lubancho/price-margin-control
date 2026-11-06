"""Главная: что происходит с ценами и маржой и на что обратить внимание (документ 08, раздел 3)."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from components import ui

from pmc import metrics
from pmc import repository as repo
from pmc.pricing import margin

ui.page_header("Обзор: цены и маржа")

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
p0, p1 = d0 - timedelta(days=days), d0 - timedelta(days=1)
period = ui.cached_period()
has_prev = period is not None and p0 >= period[0]
cur = load(d0, d1, store_ids, "network")
prev = load(p0, p1, store_ids, "network")
run, recs, alerts = last_run_data()
if store_ids and not recs.empty:
    recs = recs[recs["store_id"].isin(store_ids)]
    alerts = alerts[alerts["store_id"].isin(store_ids) | alerts["store_id"].isna()]

cols = ["revenue", "revenue_costed", "cogs", "net_qty", "lines_no_cost"]
m = metrics.from_sums(*cur.iloc[0][cols])
mp = metrics.from_sums(*prev.iloc[0][cols]) if has_prev else None


def delta(a, b):
    if b is None:
        return None
    r = metrics.safe_ratio(a - b, abs(b)) if b else None
    return None if r is None else ui.fmt_pct(r, signed=True)


n_status = recs["status"].value_counts() if not recs.empty else pd.Series(dtype=int)
blocked, conflicts, proposed = (
    int(n_status.get(s, 0)) for s in ("BLOCKED", "CONFLICT", "PROPOSED")
)
margin_dpp = (
    m.gross_margin - mp.gross_margin
    if mp is not None and m.gross_margin is not None and mp.gross_margin is not None
    else None
)
where = "выбранных точек" if store_ids else "сети"
need_decision = conflicts + blocked
df = recs.copy()
if not df.empty:
    df["plan_margin"] = [
        margin(p, c)
        if c is not None and not pd.isna(c) and c > 0 and p is not None and not pd.isna(p)
        else None
        for p, c in zip(df["current_price"], df["unit_cost"], strict=True)
    ]
    df["below_cost"] = [
        bool(p is not None and c is not None and not pd.isna(p) and not pd.isna(c) and p < c)
        for p, c in zip(df["current_price"], df["unit_cost"], strict=True)
    ]
    df["below_min"] = [
        bool(not bc and pm is not None and mm is not None and pm < mm)
        for bc, pm, mm in zip(df["below_cost"], df["plan_margin"], df["min_margin"], strict=True)
    ]

# Проблемы по убыванию важности: деньги → достоверность расчёта → решения → динамика.
items = []
if not df.empty and df["below_cost"].any():
    items.append(
        dict(lvl="crit", title="Цена ниже себестоимости", value=f"{int(df['below_cost'].sum())} позиций",
             why="Каждая продажа этих позиций убыточна.",
             page="views/prices.py", state={"pr_preset_dev": ["below_cost"]}))  # fmt: skip
if not df.empty and df["below_min"].any():
    items.append(
        dict(lvl="crit", title="Маржа ниже минимальной", value=f"{int(df['below_min'].sum())} позиций",
             why="По большинству система уже предложила новую цену.",
             page="views/prices.py", state={"pr_preset_dev": ["below_min"]}))  # fmt: skip
if blocked:
    items.append(
        dict(lvl="crit", title="Цена не рассчитана из-за ошибок в данных", value=f"{blocked} позиций",
             why="Сначала нужно исправить себестоимость или цену в учётной системе.",
             page="views/prices.py", state={"pr_preset_status": ["BLOCKED"]}))  # fmt: skip
if conflicts:
    items.append(
        dict(lvl="warn", title="Нужно решение руководителя", value=f"{conflicts} позиций",
             why="Правила противоречат друг другу: система не может выбрать цену сама.",
             page="views/prices.py", state={"pr_preset_status": ["CONFLICT"]}))  # fmt: skip
if margin_dpp is not None and margin_dpp < 0:
    jumps = int((alerts["alert_code"] == "COST_JUMP").sum()) if not alerts.empty else 0
    items.append(
        dict(lvl="warn", title="Маржа снизилась", value=ui.fmt_pp(margin_dpp),
             why=(f"У {jumps} позиций заметно выросла себестоимость — стоит проверить." if jumps
                  else "Сравните точки на странице «Маржа и продажи»."),
             page="views/alerts.py"))  # fmt: skip

# --- Главный показатель и вывод -------------------------------------------------------------------
verdict = f"### Маржа {where} — {ui.fmt_pct(m.gross_margin)}"
st.markdown(verdict)
sub = []
if margin_dpp is not None:
    sub.append(
        f"Это на {ui.fmt_num(abs(margin_dpp) * 100)} п. п. {'выше' if margin_dpp >= 0 else 'ниже'}, "
        f"чем в прошлые {days} дн."
    )
if items:
    sub.append(f"Главное сейчас: {items[0]['title'].lower()} — {items[0]['value']}.")
if sub:
    st.markdown(" ".join(sub))

NOTE = "к прошлому"
k = st.columns(4)
k[0].metric(
    "Маржа",
    ui.fmt_pct(m.gross_margin),
    ui.fmt_pp(margin_dpp) if margin_dpp is not None else None,
    delta_description=NOTE,
    help=ui.HELP["margin"] + f" Покрытие себестоимостью: {ui.fmt_pct(m.cost_coverage)} выручки.",
)
k[1].metric(
    "Валовая прибыль",
    ui.fmt_rub(m.gross_profit),
    delta(m.gross_profit, mp.gross_profit if mp else None),
    delta_description=NOTE,
    help=ui.HELP["gp"],
)
if run is not None:
    k[2].metric(
        "Цен к пересмотру",
        ui.fmt_int(proposed),
        f"расчёт на {run['as_of_date']:%d.%m.%Y}",
        delta_color="off",
        delta_arrow="off",
        help=ui.HELP["proposed"],
    )
    k[3].metric(
        "Ждут решения",
        ui.fmt_int(need_decision),
        "конфликты и ошибки",
        delta_color="off",
        delta_arrow="off",
        help=ui.HELP["conflict"] + " " + ui.HELP["dq"],
    )
else:
    k[2].metric(
        "Цен к пересмотру", ui.NA, "расчёт не выполнялся", delta_color="off", delta_arrow="off"
    )
    k[3].metric("Ждут решения", ui.NA, "расчёт не выполнялся", delta_color="off", delta_arrow="off")

# --- Не больше трёх главных проблем ---------------------------------------------------------------
ui.level(
    "Внимание",
    "Главные проблемы",
    f"Показаны 3 из {len(items)} — остальные в разделе «Рекомендации»." if len(items) > 3 else None,
)
ui.attention(items[:3], "ov_attn")
if run is not None and st.button(
    f"Пересмотреть цены: {ui.fmt_int(proposed)} предложений →", type="primary"
):
    st.session_state["pr_preset_status"] = ["PROPOSED"]
    st.switch_page("views/prices.py")

# --- Один график ---------------------------------------------------------------------------------
daily = ui.to_display(load(d0, d1, store_ids, "day"))
fig = go.Figure()
fig.add_scatter(
    x=daily["sale_date"],
    y=daily["gross_margin"] * 100,
    name="Маржа за день",
    line={"color": ui.PALETTE["accent"], "width": 3},
)
if run is not None and not recs.empty:
    min_m = recs.loc[recs["rule_scope"] == "global", "min_margin"]
    if len(min_m):
        fig.add_hline(
            y=float(min_m.iloc[0]) * 100,
            line_dash="dash",
            line_color=ui.PALETTE["crit"],
            annotation_text="минимум по правилу",
            annotation_position="bottom right",
        )
fig.update_layout(title="Маржа по дням, %", height=300, yaxis_ticksuffix="%", showlegend=False)
fig.update_xaxes(tickformat="%d.%m", hoverformat="%d.%m.%Y")
st.plotly_chart(fig, width="stretch")
if m.lines_no_cost:
    st.caption(
        f"Строк продаж без себестоимости на дату продажи: {m.lines_no_cost}. Они учтены в выручке, "
        "но не в валовой прибыли."
    )
ui.footer()
