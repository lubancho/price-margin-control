"""Экран «Сценарии» (макет 2): «что если» по правилам и сравнение до/после."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from components import ui

from pmc import repository as repo
from pmc import scenarios
from pmc.pricing import RoundingMode
from pmc.rules import rule_from_row
from pmc.run_service import compute_recommendations, create_rule_set

ui.page_header(
    "Сценарии",
    "Правила здесь не сохраняются, пока вы не нажмёте «Сохранить как новую версию». "
    "Цены в кассе не меняются.",
)


@st.cache_data(show_spinner=False, ttl=600)
def base_data():
    conn = ui.get_conn()
    period = repo.data_period(conn)
    rs = repo.rule_set_for(conn, period[1])
    return period[1], rs, repo.rules_of(conn, rs["rule_set_id"]), repo.offers_as_of(conn, period[1])


as_of, rule_set, rule_rows, offers = base_data()
rules = [rule_from_row(r) for r in rule_rows]
g = next(r for r in rules if r.scope_type == "global")

left, right = st.columns([4, 8], gap="large")
with left:
    st.subheader("Параметры")
    st.caption(
        f"Основа: правила v{rule_set['version']} на {as_of:%d.%m.%Y}. Меняется глобальное правило сети."
    )
    tm = st.slider("Целевая маржа, %", 0, 90, int(g.target_margin * 100))
    mm = st.slider("Минимальная маржа, %", 0, 90, int(g.min_margin * 100))
    mc = st.slider("Лимит изменения цены, %", 1, 50, int(g.max_change * 100))
    step = st.selectbox(
        "Шаг округления, ₽", [1, 5, 10, 50], index=[1, 5, 10, 50].index(int(g.rounding_step))
    )
    allow_dec = st.checkbox("Разрешить снижение цены", value=g.allow_decrease)
    replace_all = st.checkbox(
        "Применить ко всем категориям",
        value=False,
        help="Если включено — правила категорий не действуют, только эти параметры",
    )
    st.divider()
    mode = st.radio(
        "Спрос",
        ["fixed", "coefficient"],
        horizontal=True,
        format_func={"fixed": "фиксированный", "coefficient": "коэффициент реакции"}.get,
    )
    e = st.number_input(
        "Коэффициент реакции спроса e",
        -3.0,
        0.0,
        -0.5,
        0.1,
        disabled=mode == "fixed",
        help="Задаётся вручную. При e = −0,5 рост цены на 10% снижает продажи на 5%.",
    )

params_error = None
if mm > tm:
    params_error = "Минимальная маржа не может быть больше целевой."
elif tm >= 100:
    params_error = "Целевая маржа должна быть меньше 100%."
if params_error:
    right.error(params_error)
    ui.footer()
    st.stop()

global_params = dict(
    target_margin=Decimal(tm) / 100,
    min_margin=Decimal(mm) / 100,
    max_change=Decimal(mc) / 100,
    rounding_step=Decimal(step),
    rounding_mode=RoundingMode.UP,
    allow_decrease=allow_dec,
)
index = scenarios.override_rules(rules, global_params, replace_all)
results = compute_recommendations(offers, index)
rows = offers.copy()
rows["status"] = [r[3].status.value for r in results]
rows["recommended_price"] = [r[3].recommended_price for r in results]
assumption = scenarios.DemandAssumption(mode, Decimal(str(e)))
sim = scenarios.simulate(rows, assumption)
total = scenarios.totals(sim).iloc[0]

with right:
    st.subheader("Результат: сейчас и после применения рекомендаций")
    comp = pd.DataFrame(
        {
            "Показатель": ["Выручка за 28 дней", "Валовая прибыль", "Валовая маржинальность"],
            "Сейчас": [
                ui.fmt_rub(total["rev0"]),
                ui.fmt_rub(total["gp0"]),
                ui.fmt_pct(total["margin0"]),
            ],
            "После": [
                ui.fmt_rub(total["rev1"]),
                ui.fmt_rub(total["gp1"]),
                ui.fmt_pct(total["margin1"]),
            ],
            "Разница": [
                ui.fmt_rub(total["rev1"] - total["rev0"]),
                ui.fmt_rub(total["gp1"] - total["gp0"]),
                (
                    ui.fmt_pp(total["margin1"] - total["margin0"])
                    if total["margin0"] is not None and total["margin1"] is not None
                    else "н/д"
                ),
            ],
        }
    )
    st.dataframe(comp, hide_index=True, width="stretch")
    cnt = Counter(rows["status"])
    st.caption(
        " · ".join(
            f"{ui.STATUS_LABEL[s]}: {cnt.get(s, 0)}"
            for s in ("PROPOSED", "NO_CHANGE", "CONFLICT", "BLOCKED", "ON_HOLD")
        )
        + f" · изменится цен: {total['changed']}"
    )
    by_cat = ui.to_display(scenarios.totals(sim, "category_name"))
    fig = go.Figure()
    fig.add_bar(
        x=by_cat["category_name"],
        y=by_cat["gp0"],
        name="Сейчас",
        marker_color=ui.PALETTE["accent2"],
    )
    fig.add_bar(
        x=by_cat["category_name"], y=by_cat["gp1"], name="После", marker_color=ui.PALETTE["accent"]
    )
    fig.update_layout(
        title="Валовая прибыль по категориям за 28 дней, ₽",
        barmode="group",
        height=300,
        yaxis_tickformat=",.0f",
    )
    st.plotly_chart(fig, width="stretch")
    ui.box(
        "<b>Допущения</b><br>" + "<br>".join(f"• {line}" for line in assumption.describe()), "warn"
    )

    if ui.can("rules"):
        with st.form("save_rules"):
            comment = st.text_input("Комментарий к новой версии правил")
            if st.form_submit_button("Сохранить как новую версию правил"):
                new_rules = index.all()
                v = create_rule_set(
                    ui.get_conn(), new_rules, st.session_state["role"], as_of, comment or None
                )
                ui.refresh_data()
                st.success(f"Создана версия правил v{v}. Выполните расчёт на экране «Анализ цен».")
    else:
        st.caption("Сохранение правил доступно роли «Аналитик».")
ui.footer()
