"""Главная: что происходит с ценами и маржой и на что обратить внимание (документ 08, раздел 3)."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import streamlit as st
from components import ui

from pmc import metrics
from pmc import repository as repo

ui.page_header("Сводка")

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
crit = int((alerts["severity"] == "CRIT").sum()) if not alerts.empty else 0

# --- Уровень 1: одна фраза и четыре «двери» в разбор -------------------------------------------
sub = f"Маржа {where} — {ui.fmt_pct(m.gross_margin)}"
if margin_dpp is not None:
    sub += (
        f": на {ui.fmt_num(abs(margin_dpp) * 100)} п. п. "
        f"{'выше' if margin_dpp >= 0 else 'ниже'}, чем в прошлые {days} дн."
    )
st.markdown(f"### {sub}")

k = st.columns(4)
ui.door(
    k[0], "Маржа", ui.fmt_pct(m.gross_margin),
    delta=ui.fmt_pp(margin_dpp) if margin_dpp is not None else None,
    note=f"валовая прибыль {ui.fmt_rub(m.gross_profit)}",
    action="Где ниже", page="views/margin.py", key="door_margin",
    help=ui.HELP["margin"] + f" Покрытие себестоимостью: {ui.fmt_pct(m.cost_coverage)} выручки.",
)  # fmt: skip
if run is not None:
    ui.door(
        k[1], "Цены к пересмотру", ui.fmt_int(proposed),
        note=f"расчёт на {run['as_of_date']:%d.%m.%Y}", action="Открыть список",
        page="views/prices.py", state={"pr_preset_status": ["PROPOSED"]}, key="door_prices",
        help=ui.HELP["proposed"],
    )  # fmt: skip
    ui.door(
        k[2], "Нужно решение", ui.fmt_int(conflicts), note="правила противоречат друг другу",
        action="Решить", page="views/prices.py", state={"pr_preset_status": ["CONFLICT"]},
        key="door_conflict", help=ui.HELP["conflict"],
    )  # fmt: skip
    ui.door(
        k[3], "Ошибки и нарушения", ui.fmt_int(crit),
        note=f"критичных · цена не рассчитана: {ui.fmt_int(blocked)}", action="Разобрать",
        page="views/alerts.py", key="door_alerts", help=ui.HELP["dq"],
    )  # fmt: skip
else:
    for c, label in zip(
        k[1:], ["Цены к пересмотру", "Нужно решение", "Ошибки и нарушения"], strict=True
    ):
        ui.door(c, label, ui.NA, note="расчёт не выполнялся", key=f"door_{label}")
if m.lines_no_cost:
    st.caption(
        f"Строк продаж без себестоимости на дату продажи: {m.lines_no_cost}. Они учтены в выручке, "
        "но не в валовой прибыли."
    )
ui.footer()
