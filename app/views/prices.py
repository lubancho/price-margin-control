"""Экран «Анализ цен» (макеты 3 и 4): рекомендации последнего расчёта, фильтры, согласование, карточка."""

from __future__ import annotations

from decimal import Decimal

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from components import ui

from pmc import metrics
from pmc import repository as repo
from pmc.alerts import TITLES
from pmc.pricing import margin
from pmc.run_service import RunError, decide, start_run

ui.page_header(
    "Анализ цен",
    "Рекомендации по последнему расчёту. Система не меняет цены в кассе — "
    "согласование только фиксирует решение.",
)


@st.cache_data(show_spinner=False, ttl=600)
def load_run():
    conn = ui.get_conn()
    run = repo.last_done_run(conn)
    if run is None:
        return None, pd.DataFrame()
    return run, repo.recommendations(conn, run["run_id"])


@st.cache_data(show_spinner=False, ttl=600)
def fact_by_offer(d0, d1):
    return metrics.enrich(repo.sales_aggregates(ui.get_conn(), d0, d1, "offer"))


@st.cache_data(show_spinner=False, ttl=600)
def open_alerts():
    return repo.alerts(ui.get_conn(), ["NEW", "IN_PROGRESS"])


run, recs = load_run()

# --- Запуск расчёта ------------------------------------------------------------------------------
top = st.columns([5, 2, 2])
with top[0]:
    if run is not None:
        ui.box(
            f"Расчёт на <b>{run['as_of_date']:%d.%m.%Y}</b> · правила <b>v{run['rule_version']}</b> · "
            f"запуск <code>{str(run['run_id'])[:8]}</code> · выполнен {run['finished_at']:%d.%m.%Y %H:%M}"
        )
    else:
        ui.box("Расчёт рекомендаций ещё не выполнялся.", "warn")
with top[1]:
    force = st.checkbox(
        "Пересчитать принудительно",
        disabled=not ui.can("run"),
        help="Создать новый запуск, даже если данные и правила не менялись",
    )
with top[2]:
    if st.button(
        "Рассчитать",
        type="primary",
        disabled=not ui.can("run"),
        width="stretch",
        help=None if ui.can("run") else "Доступно роли «Аналитик»",
    ):
        try:
            with st.spinner("Расчёт рекомендаций…"):
                res = start_run(
                    ui.get_conn(), actor=st.session_state.get("role", "аналитик"), force=force
                )
            ui.refresh_data()
            if res["status"] == "exists":
                st.session_state["flash"] = (
                    "info",
                    "Расчёт на эти данные и правила уже выполнен — показан "
                    "существующий результат. Для нового запуска отметьте "
                    "«Пересчитать принудительно».",
                )
            else:
                st.session_state["flash"] = (
                    "success",
                    f"Расчёт выполнен: {res['statuses']}; новых "
                    f"предупреждений {res['alerts_new']}.",
                )
            st.rerun()
        except RunError as exc:
            st.error(f"Расчёт не выполнен: {exc}")

if flash := st.session_state.pop("flash", None):
    getattr(st, flash[0])(flash[1])

if run is None or recs.empty:
    ui.footer()
    st.stop()

# --- Фильтры -------------------------------------------------------------------------------------
stores = ui.cached_stores()
cats = ui.cached_categories()
f = st.columns([2.6, 2, 2, 2, 2, 1.8])
with f[0]:
    d0, d1 = ui.period_filter("pr_period")
with f[1]:
    default = [ui.my_store()] if ui.my_store() else []
    f_store = st.multiselect(
        "Точка",
        stores["store_id"],
        default=default,
        placeholder="Все",
        format_func=dict(zip(stores["store_id"], stores["name"], strict=True)).get,
    )
with f[2]:
    f_cat = st.multiselect(
        "Категория",
        cats["category_id"],
        placeholder="Все",
        format_func=dict(zip(cats["category_id"], cats["name"], strict=True)).get,
    )
with f[3]:
    f_text = st.text_input("Товар", placeholder="название или артикул")
SCOPE_LABEL = {"global": "Вся сеть", "category": "Категория", "store": "Точка", "product": "Товар"}
DEVIATIONS = {
    "below_cost": "Цена ниже себестоимости",
    "below_min": "Ниже минимальной маржи",
    "below_target": "Ниже целевой маржи",
    "network": "Отклонение от медианы сети",
    "data_error": "Ошибка данных",
    "none": "Без отклонений",
}
with f[4]:
    f_dev = st.multiselect(
        "Тип отклонения", list(DEVIATIONS), format_func=DEVIATIONS.get, placeholder="Любой"
    )
with f[5]:
    f_status = st.multiselect(
        "Статус", list(ui.STATUS_LABEL), format_func=ui.STATUS_LABEL.get, placeholder="Все"
    )

df = recs.copy()
df["plan_margin"] = [
    margin(p, c) if c is not None and not pd.isna(c) and c > 0 else None
    for p, c in zip(df["current_price"], df["unit_cost"], strict=True)
]
deviation_offers = set(
    open_alerts()
    .query("alert_code == 'PRICE_DEVIATION'")[["store_id", "product_id"]]
    .itertuples(index=False, name=None)
)


def deviation(r) -> str:
    if r.status == "BLOCKED":
        return "data_error"
    if r.plan_margin is None or pd.isna(r.plan_margin):
        return "data_error"
    if r.current_price < r.unit_cost:
        return "below_cost"
    if r.min_margin is not None and r.plan_margin < r.min_margin:
        return "below_min"
    if r.target_margin is not None and r.plan_margin < r.target_margin:
        return "below_target"
    if (r.store_id, r.product_id) in deviation_offers:
        return "network"
    return "none"


df["deviation"] = [deviation(r) for r in df.itertuples(index=False)]
fact = fact_by_offer(d0, d1)[["store_id", "product_id", "revenue", "gross_margin"]]
df = df.merge(fact, on=["store_id", "product_id"], how="left")

if f_store:
    df = df[df["store_id"].isin(f_store)]
if f_cat:
    df = df[df["category_id"].isin(f_cat)]
if f_text:
    t = f_text.strip().lower()
    df = df[
        df["product_name"].str.lower().str.contains(t, regex=False)
        | df["sku"].str.lower().str.contains(t)
    ]
if f_dev:
    df = df[df["deviation"].isin(f_dev)]
if f_status:
    df = df[df["status"].isin(f_status)]

counts = df["status"].value_counts()
st.caption(
    " · ".join(
        f"{ui.STATUS_LABEL[s]}: {counts.get(s, 0)}"
        for s in ("PROPOSED", "NO_CHANGE", "CONFLICT", "BLOCKED", "ON_HOLD", "APPROVED", "REJECTED")
    )
)

order = {
    "BLOCKED": 0,
    "CONFLICT": 1,
    "PROPOSED": 2,
    "ON_HOLD": 3,
    "APPROVED": 4,
    "REJECTED": 5,
    "NO_CHANGE": 6,
}
df = df.assign(
    _o=df["status"].map(order), _c=df["change_pct"].map(lambda v: -abs(v) if v is not None else 0)
)
df = df.sort_values(["_o", "_c", "store_id", "product_id"]).reset_index(drop=True)

show = ui.to_display(
    pd.DataFrame(
        {
            "Точка": df["store_name"],
            "Товар": df["product_name"],
            "Категория": df["category_name"],
            "Себестоимость, ₽": df["unit_cost"],
            "Цена, ₽": df["current_price"],
            "Маржа план, %": df["plan_margin"].map(
                lambda v: v * 100 if v is not None and not pd.isna(v) else None
            ),
            "Маржа факт, %": df["gross_margin"].map(
                lambda v: v * 100 if v is not None and not pd.isna(v) else None
            ),
            "Рекомендация, ₽": df["recommended_price"].where(
                df["status"].isin(["PROPOSED", "APPROVED", "REJECTED"])
            ),
            "Изм., ₽": df["change_abs"].where(
                df["status"].isin(["PROPOSED", "APPROVED", "REJECTED"])
            ),
            "Изм., %": df["change_pct"]
            .map(lambda v: v * 100 if v is not None and not pd.isna(v) else None)
            .where(df["status"].isin(["PROPOSED", "APPROVED", "REJECTED"])),
            "Причина": df["reason_text"],
            "Статус": df["status"].map(ui.STATUS_LABEL),
        }
    )
)
event = st.dataframe(
    show,
    hide_index=True,
    height=460,
    on_select="rerun",
    selection_mode="multi-row",
    key="rec_table",
    column_config={
        "Точка": st.column_config.TextColumn(width="medium"),
        "Товар": st.column_config.TextColumn(width="medium"),
        "Категория": None,
        "Себестоимость, ₽": st.column_config.NumberColumn(
            "Себест., ₽", format="%.2f", width="small"
        ),
        "Цена, ₽": st.column_config.NumberColumn(format="%.0f", width="small"),
        "Маржа план, %": st.column_config.NumberColumn(
            "Маржа, %",
            format="%.1f",
            width="small",
            help="Плановая маржа при текущей цене и себестоимости",
        ),
        "Маржа факт, %": st.column_config.NumberColumn(
            "Факт, %",
            format="%.1f",
            width="small",
            help="Фактическая валовая маржинальность за выбранный период",
        ),
        "Рекомендация, ₽": st.column_config.NumberColumn(
            "Рекоменд., ₽", format="%.0f", width="small"
        ),
        "Изм., ₽": st.column_config.NumberColumn(format="%+.0f", width="small"),
        "Изм., %": st.column_config.NumberColumn(format="%+.1f", width="small"),
        "Причина": st.column_config.TextColumn(width="large"),
        "Статус": st.column_config.TextColumn(width="small"),
    },
)
selected = df.iloc[event.selection.rows] if event and event.selection.rows else df.iloc[0:0]
st.caption(f"Показано {len(df)} из {len(recs)} предложений. Выбрано: {len(selected)}.")


# --- Карточка предложения -------------------------------------------------------------------------
@st.dialog("Карточка товарного предложения", width="large")
def offer_card(r) -> None:
    conn = ui.get_conn()
    sev = "КРИТИЧНО" if r.deviation in ("below_cost", "below_min", "data_error") else ""
    st.markdown(f"**{r.product_name}** · {r.sku} · {r.category_name} · {r.store_name}")
    if sev:
        st.error(f"{sev}: {DEVIATIONS[r.deviation]}")
    c = st.columns(4)
    c[0].metric("Себестоимость", ui.fmt_rub(r.unit_cost, 2))
    c[1].metric(
        "Текущая цена",
        ui.fmt_rub(r.current_price),
        ui.fmt_pct(r.plan_margin) + " маржа",
        delta_color="off",
        delta_arrow="off",
    )
    rec_price = (
        ui.fmt_rub(r.recommended_price)
        if r.status in ("PROPOSED", "APPROVED")
        else ui.STATUS_LABEL[r.status]
    )
    c[2].metric("Рекомендация", rec_price)
    c[3].metric(
        "Правило",
        SCOPE_LABEL.get(r.rule_scope, "—"),
        f"мин. {ui.fmt_pct(r.min_margin)} · цель {ui.fmt_pct(r.target_margin)}",
        delta_color="off",
        delta_arrow="off",
    )
    ph = repo.price_history(conn, int(r.store_id), int(r.product_id))
    ch = repo.cost_history(conn, int(r.product_id))
    end = run["as_of_date"]
    fig = go.Figure()
    if len(ph):
        xs = [*ph["valid_from"], end]
        ys = [*ph["price"].map(float), float(ph["price"].iloc[-1])]
        fig.add_scatter(
            x=xs,
            y=ys,
            mode="lines",
            line_shape="hv",
            name="Цена в точке",
            line={"color": ui.PALETTE["accent"], "width": 2.5},
        )
    if len(ch):
        xs = [*ch["valid_from"], end]
        ys = [*ch["unit_cost"].map(float), float(ch["unit_cost"].iloc[-1])]
        fig.add_scatter(
            x=xs,
            y=ys,
            mode="lines",
            line_shape="hv",
            name="Себестоимость",
            line={"color": ui.PALETTE["warn"], "width": 2.5},
        )
    fig.update_layout(height=230, title="История цены и себестоимости", yaxis_title="₽")
    fig.update_yaxes(rangemode="tozero")
    fig.update_xaxes(tickformat="%d.%m", hoverformat="%d.%m.%Y")
    st.plotly_chart(fig, width="stretch")
    st.markdown(f"**Почему:** {r.reason_text}")
    others = recs[(recs["product_id"] == r.product_id) & recs["current_price"].notna()][
        "current_price"
    ]
    if len(others):
        st.caption(
            f"Цена этой позиции в сети: медиана {ui.fmt_rub(others.median())}, "
            f"диапазон {ui.fmt_rub(others.min())} — {ui.fmt_rub(others.max())}"
        )
    al = open_alerts()
    al = al[
        (al["product_id"] == r.product_id)
        & (al["store_id"].isna() | (al["store_id"] == r.store_id))
    ]
    if len(al):
        st.markdown(
            "**Открытые предупреждения:** "
            + ", ".join(sorted({TITLES[a] for a in al["alert_code"]}))
        )
    if r.status in ("PROPOSED", "CONFLICT") and ui.can("decide"):
        st.divider()
        cols = st.columns([1.2, 3, 1.3])
        default_price = float(r.recommended_price or Decimal(r.details.get("min_price", 0) or 0))
        manual = cols[0].number_input(
            "Цена вручную, ₽",
            min_value=0.0,
            value=default_price,
            step=10.0,
            disabled=r.status != "CONFLICT",
        )
        comment = cols[1].text_input("Комментарий")
        if cols[2].button("Согласовать", type="primary", width="stretch"):
            try:
                decide(
                    conn,
                    [int(r.recommendation_id)],
                    True,
                    st.session_state["role"],
                    Decimal(str(manual)) if r.status == "CONFLICT" else None,
                    comment or None,
                )
                ui.refresh_data()
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))


b = st.columns([2, 2, 2, 4])
if b[0].button(
    "Открыть карточку",
    disabled=len(selected) != 1,
    width="stretch",
    help="Выберите одну строку таблицы",
):
    offer_card(selected.iloc[0])
can_decide = ui.can("decide")
decidable = selected[selected["status"] == "PROPOSED"]
if b[1].button(
    "Согласовать",
    type="primary",
    disabled=not can_decide or decidable.empty,
    width="stretch",
    help="Роль «Руководитель сети»; согласуются строки со статусом «Предложено». "
    "Конфликт — через карточку с ценой вручную.",
):
    n = decide(
        ui.get_conn(),
        decidable["recommendation_id"].astype(int).tolist(),
        True,
        st.session_state["role"],
    )
    ui.refresh_data()
    st.session_state["flash"] = ("success", f"Согласовано рекомендаций: {n}")
    st.rerun()
if b[2].button(
    "Отклонить",
    disabled=not can_decide or selected[selected["status"].isin(["PROPOSED", "CONFLICT"])].empty,
    width="stretch",
):
    ids = (
        selected[selected["status"].isin(["PROPOSED", "CONFLICT"])]["recommendation_id"]
        .astype(int)
        .tolist()
    )
    n = decide(ui.get_conn(), ids, False, st.session_state["role"])
    ui.refresh_data()
    st.session_state["flash"] = ("success", f"Отклонено рекомендаций: {n}")
    st.rerun()
if not can_decide:
    b[3].caption("Согласование доступно роли «Руководитель сети».")
ui.footer()
