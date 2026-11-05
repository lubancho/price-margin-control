"""Главная: что происходит с ценами и маржой и на что обратить внимание (документ 08, раздел 3)."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from components import ui

from pmc import metrics
from pmc import repository as repo
from pmc.pricing import margin

ui.page_header(
    "Что происходит с ценами и маржой",
    "Продажи и маржа — за выбранный период; рекомендации и предупреждения — по последнему расчёту.",
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


@st.cache_data(show_spinner=False, ttl=600)
def dq_rows() -> int:
    dq = repo.dq_summary(ui.get_conn())
    return int(dq.loc[dq["severity"] == "CRIT", "issues"].sum()) if len(dq) else 0


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
where = "по выбранным точкам" if store_ids else "по сети"
summary = f"Маржинальность {where} за {days} дн. — **{ui.fmt_pct(m.gross_margin)}**"
if margin_dpp is not None:
    word = "выше" if margin_dpp >= 0 else "ниже"
    summary += f" ({word} предыдущего периода на {ui.fmt_num(abs(margin_dpp) * 100)} п. п.)"
if run is not None:
    summary += (
        f". Система предлагает изменить **{proposed}** цен; **{conflicts}** позиций ждут решения "
        f"вручную, **{blocked}** не рассчитаны из-за ошибок данных."
    )
st.markdown(summary)

# --- Уровень 1: состояние ------------------------------------------------------------------------
ui.level(
    "Состояние",
    "Основные показатели",
    f"Изменение — к предыдущим {days} дн. ({p0:%d.%m.%Y} — {p1:%d.%m.%Y}); зелёный — лучше для бизнеса."
    if has_prev
    else "Для выбранного периода нет предыдущего периода той же длины — сравнение не показывается.",
)
NOTE = "к предыдущему периоду"
k = [*st.columns(3), *st.columns(3)]
k[0].metric(
    "Валовая маржинальность",
    ui.fmt_pct(m.gross_margin),
    ui.fmt_pp(margin_dpp) if margin_dpp is not None else None,
    delta_description=NOTE,
    help=ui.HELP["margin"] + f" Покрытие себестоимостью: {ui.fmt_pct(m.cost_coverage)} выручки.",
)
k[1].metric(
    "Выручка",
    ui.fmt_rub(m.revenue),
    delta(m.revenue, mp.revenue if mp else None),
    delta_description=NOTE,
    help=ui.HELP["revenue"],
)
k[2].metric(
    "Валовая прибыль",
    ui.fmt_rub(m.gross_profit),
    delta(m.gross_profit, mp.gross_profit if mp else None),
    delta_description=NOTE,
    help=ui.HELP["gp"],
)
if run is not None:
    k[3].metric(
        "Предложено изменить цен",
        ui.fmt_int(proposed),
        f"расчёт на {run['as_of_date']:%d.%m.%Y}",
        delta_color="off",
        delta_arrow="off",
        help=ui.HELP["proposed"],
    )
    k[4].metric(
        "Нужно решение вручную",
        ui.fmt_int(conflicts),
        "конфликт правил",
        delta_color="off",
        delta_arrow="off",
        help=ui.HELP["conflict"],
    )
    k[5].metric(
        "Проблемы данных",
        ui.fmt_int(blocked),
        f"+ {dq_rows()} строк исключено при загрузке",
        delta_color="off",
        delta_arrow="off",
        help=ui.HELP["dq"],
    )
else:
    for i, label in (
        (3, "Предложено изменить цен"),
        (4, "Нужно решение вручную"),
        (5, "Проблемы данных"),
    ):
        k[i].metric(label, ui.NA, "расчёт не выполнялся", delta_color="off", delta_arrow="off")

# --- Уровень 2: что требует внимания ------------------------------------------------------------
ui.level("Внимание", "Что требует внимания", "По важности. «Открыть» — перейти к списку.")
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
items = []
if blocked:
    items.append(
        dict(lvl="crit", title="Позиции без расчёта цены", value=f"{blocked} позиций",
             why="Ошибки в себестоимости или цене: маржа и рекомендация по ним недостоверны. "
             "Сначала исправить данные.",
             page="views/prices.py", state={"pr_preset_status": ["BLOCKED"]}))  # fmt: skip
if not df.empty and df["below_cost"].any():
    items.append(
        dict(lvl="crit", title="Цена ниже себестоимости", value=f"{int(df['below_cost'].sum())} позиций",
             why="Каждая продажа этих позиций убыточна.",
             page="views/prices.py", state={"pr_preset_dev": ["below_cost"]}))  # fmt: skip
if not df.empty and df["below_min"].any():
    items.append(
        dict(lvl="crit", title="Маржа ниже минимальной", value=f"{int(df['below_min'].sum())} позиций",
             why="Плановая маржа ниже порога правила. По большинству система уже предложила новую цену.",
             page="views/prices.py", state={"pr_preset_dev": ["below_min"]}))  # fmt: skip
if conflicts:
    items.append(
        dict(lvl="warn", title="Правила противоречат друг другу", value=f"{conflicts} позиций",
             why="Для минимальной маржи нужна цена выше лимита изменения — система не выбирает цену сама.",
             page="views/prices.py", state={"pr_preset_status": ["CONFLICT"]}))  # fmt: skip
if margin_dpp is not None and margin_dpp < 0:
    jumps = int((alerts["alert_code"] == "COST_JUMP").sum()) if not alerts.empty else 0
    items.append(
        dict(lvl="warn", title="Маржинальность снизилась", value=ui.fmt_pp(margin_dpp),
             why=f"{ui.fmt_pct(m.gross_margin)} против {ui.fmt_pct(mp.gross_margin)}."
             + (f" Возможный фактор: у {jumps} позиций заметно выросла себестоимость — проверить." if jumps else ""),
             page="views/alerts.py"))  # fmt: skip
by_store = ui.to_display(load(d0, d1, store_ids, "store"))
if not store_ids and len(by_store) and m.gross_margin is not None:
    worst = by_store.sort_values("gross_margin").iloc[0]
    if worst["gross_margin"] < float(m.gross_margin):
        items.append(
            dict(lvl="info", title=f"Самая низкая маржа — {worst['store_name']}",
                 value=ui.fmt_pp(worst["gross_margin"] - float(m.gross_margin)) + " к сети",
                 why=f"{ui.fmt_pct(worst['gross_margin'])} при {ui.fmt_pct(m.gross_margin)} по сети."))  # fmt: skip
ui.attention(items, "ov_attn")

# --- Проверить в первую очередь -----------------------------------------------------------------
if not df.empty:
    ui.level(
        "Проверить",
        "Проверить в первую очередь",
        "Убыточные цены, затем маржа ниже минимальной, затем ошибки данных.",
    )
    df["prio"] = [
        0 if bc else 1 if bm else 2 if s == "BLOCKED" else 9
        for bc, bm, s in zip(df["below_cost"], df["below_min"], df["status"], strict=True)
    ]
    top = df[df["prio"] < 9].sort_values(["prio", "plan_margin"], na_position="last").head(10)
    problem = {0: "Цена ниже себестоимости", 1: "Маржа ниже минимальной", 2: "Ошибка данных"}
    show = pd.DataFrame(
        {
            "Товар": top["product_name"],
            "Точка": top["store_name"],
            "Цена сейчас, ₽": top["current_price"],
            "Маржа сейчас": top["plan_margin"],
            "Проблема": top["prio"].map(problem),
            "Что предлагает система": top["status"].map(ui.STATUS_LABEL),
            "Новая цена, ₽": top["recommended_price"].where(top["status"] == "PROPOSED"),
        }
    )
    fmts = {"Цена сейчас, ₽": "rub", "Маржа сейчас": "pct", "Новая цена, ₽": "rub"}
    st.dataframe(
        ui.styled(show, fmts), hide_index=True, width="stretch", column_config=ui.num_cfg(fmts)
    )
    st.caption(
        f"Всего таких позиций: {int((df['prio'] < 9).sum())}. Полный список и карточки — на экране «Цены и рекомендации»."
    )

# --- Уровень 3: подробно ------------------------------------------------------------------------
ui.level("Подробно", "Динамика и сравнение точек")
daily = ui.to_display(load(d0, d1, store_ids, "day"))
g1, g2 = st.columns(2)
with g1:
    fig = go.Figure()
    fig.add_scatter(
        x=daily["sale_date"],
        y=daily["gross_margin"] * 100,
        name="Маржинальность за день",
        line={"color": ui.PALETTE["accent"], "width": 2.5},
    )
    if run is not None and not recs.empty:
        min_m = recs.loc[recs["rule_scope"] == "global", "min_margin"]
        if len(min_m):
            fig.add_hline(
                y=float(min_m.iloc[0]) * 100,
                line_dash="dash",
                line_color=ui.PALETTE["crit"],
                annotation_text="минимум правила",
                annotation_position="bottom right",
            )
    fig.update_layout(
        title="Как меняется валовая маржинальность, % по дням", height=360, yaxis_ticksuffix="%"
    )
    fig.update_xaxes(tickformat="%d.%m", hoverformat="%d.%m.%Y")
    st.plotly_chart(fig, width="stretch")
with g2:
    bs = by_store.sort_values("gross_margin")
    net = float(m.gross_margin) * 100 if m.gross_margin is not None else None
    colors = [
        ui.PALETTE["warn"] if net is not None and v * 100 < net else ui.PALETTE["accent"]
        for v in bs["gross_margin"]
    ]
    fig = px.bar(
        bs, x=bs["gross_margin"] * 100, y="store_name", orientation="h", custom_data=["revenue"]
    )
    fig.update_traces(
        marker_color=colors,
        hovertemplate="%{y}<br>Маржинальность %{x:.1f}%<br>Выручка %{customdata[0]:,.0f} ₽<extra></extra>",
    )
    if net is not None:
        fig.add_vline(
            x=net,
            line_dash="dash",
            line_color=ui.PALETTE["muted"],
            annotation_text="среднее по выбранным",
        )
    lo = max(0, float((bs["gross_margin"] * 100).min()) - 3) if len(bs) else 0
    fig.update_layout(
        title="В каких точках маржа ниже средней, %",
        height=360,
        xaxis={"title": None, "ticksuffix": "%", "range": [lo, None]},
        yaxis_title=None,
    )
    st.plotly_chart(fig, width="stretch")

if m.lines_no_cost:
    st.caption(
        f"Строк продаж без себестоимости на дату продажи: {m.lines_no_cost}. Они учтены в выручке, "
        "но не в валовой прибыли."
    )
ui.footer()
