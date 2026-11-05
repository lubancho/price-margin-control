"""Экран «Предупреждения» (макет 5): ошибки и отклонения, критичность, статус обработки."""

from __future__ import annotations

import pandas as pd
import streamlit as st
from components import ui

from pmc import repository as repo
from pmc.alerts import TITLES
from pmc.run_service import set_alert_status

ui.page_header(
    "Предупреждения",
    "Повторное обнаружение той же проблемы не создаёт дубль — растёт счётчик "
    "«повторов». Информационные события — в разделе «История».",
)


@st.cache_data(show_spinner=False, ttl=600)
def load_alerts():
    return repo.alerts(ui.get_conn())


@st.cache_data(show_spinner=False, ttl=600)
def load_dq():
    return repo.dq_summary(ui.get_conn())


al = load_alerts()
if al.empty:
    st.info("Предупреждений нет. Выполните расчёт на экране «Анализ цен».")
    ui.footer()
    st.stop()

opened = al[al["status"].isin(["NEW", "IN_PROGRESS"])]
k = st.columns(4)
k[0].metric("Критичные (открытые)", ui.fmt_int((opened["severity"] == "CRIT").sum()))
k[1].metric("Предупреждения (открытые)", ui.fmt_int((opened["severity"] == "WARN").sum()))
k[2].metric("В работе", ui.fmt_int((al["status"] == "IN_PROGRESS").sum()))
k[3].metric("Решено / отклонено", ui.fmt_int(al["status"].isin(["RESOLVED", "DISMISSED"]).sum()))

stores = ui.cached_stores()
f = st.columns(4)
f_sev = f[0].multiselect(
    "Критичность", ["CRIT", "WARN"], format_func=ui.SEVERITY_LABEL.get, placeholder="Любая"
)
f_type = f[1].multiselect("Тип", sorted(TITLES), format_func=TITLES.get, placeholder="Любой")
default_store = [ui.my_store()] if ui.my_store() else []
f_store = f[2].multiselect(
    "Точка",
    stores["store_id"],
    default=default_store,
    placeholder="Все",
    format_func=dict(zip(stores["store_id"], stores["name"], strict=True)).get,
)
f_status = f[3].multiselect(
    "Статус",
    list(ui.ALERT_STATUS_LABEL),
    default=["NEW", "IN_PROGRESS"],
    format_func=ui.ALERT_STATUS_LABEL.get,
    placeholder="Все",
)

df = al
if f_sev:
    df = df[df["severity"].isin(f_sev)]
if f_type:
    df = df[df["alert_code"].isin(f_type)]
if f_store:
    df = df[df["store_id"].isin(f_store) | df["store_id"].isna()]
if f_status:
    df = df[df["status"].isin(f_status)]
df = df.reset_index(drop=True)

show = pd.DataFrame(
    {
        "№": df["alert_id"],
        "Критичность": df["severity"].map(
            lambda s: ("🔴 " if s == "CRIT" else "🟠 ") + ui.SEVERITY_LABEL[s]
        ),
        "Тип": df["alert_code"].map(TITLES),
        "Точка": df["store_name"].fillna("Вся сеть"),
        "Товар": df["product_name"].fillna("—"),
        "Описание": df["description"],
        "Обнаружено": df["first_seen_at"],
        "Повторов": df["occurrences"],
        "Статус": df["status"].map(ui.ALERT_STATUS_LABEL),
    }
)
event = st.dataframe(
    show,
    hide_index=True,
    height=440,
    on_select="rerun",
    selection_mode="multi-row",
    key="alerts_table",
    column_config={
        "Описание": st.column_config.TextColumn(width="large"),
        "Обнаружено": st.column_config.DatetimeColumn(format="DD.MM.YYYY HH:mm"),
    },
)
selected = df.iloc[event.selection.rows] if event and event.selection.rows else df.iloc[0:0]
st.caption(f"Показано {len(df)} из {len(al)}. Выбрано: {len(selected)}.")

c = st.columns([2, 2, 6])
new_status = c[0].selectbox(
    "Новый статус",
    list(ui.ALERT_STATUS_LABEL),
    index=1,
    format_func=ui.ALERT_STATUS_LABEL.get,
    label_visibility="collapsed",
)
if c[1].button("Изменить статус", disabled=selected.empty, type="primary", width="stretch"):
    for aid in selected["alert_id"].astype(int):
        set_alert_status(
            ui.get_conn(), aid, new_status, st.session_state.get("role", "пользователь")
        )
    ui.refresh_data()
    st.rerun()
c[2].caption("Изменение статуса записывается в журнал с ролью и временем.")

with st.expander("Проверки качества исходных данных при загрузке"):
    dq = load_dq()
    st.dataframe(
        dq.rename(columns={"check_code": "Проверка", "severity": "Критичность", "issues": "Строк"}),
        hide_index=True,
    )
    st.caption("Строки с ошибками не попадают в расчёты и сохраняются в журнале проблем качества.")
ui.footer()
