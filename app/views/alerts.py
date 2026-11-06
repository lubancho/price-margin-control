"""Что сделать → «Ошибки в данных и правилах» (макет 5): ошибки и отклонения, уровни, статус обработки."""

from __future__ import annotations

import pandas as pd
import streamlit as st
from components import ui

from pmc import repository as repo
from pmc.alerts import TITLES
from pmc.run_service import set_alert_status

ui.crumbs(("Сводка", "views/overview.py"), ("Ошибки в данных и правилах", None))
ui.page_header(
    "Ошибки в данных и правилах",
    "Ошибки в исходных данных и нарушения правил: сначала то, из-за чего расчёт недостоверен. "
    "Повторное обнаружение той же проблемы не создаёт дубль: растёт счётчик «повторов».",
)

# Тип → (уровень, влияет на достоверность расчёта, что значит, что сделать)
TYPE_INFO = {
    "INVALID_FIN_DATA": ("crit", True, "Себестоимость или цена некорректны — расчёт по позиции невозможен.",
                         "Исправить данные в учётной системе и пересчитать."),
    "PRICE_UNDETERMINED": ("crit", True, "Нет действующей цены на дату расчёта.", "Проверить прайс точки."),
    "PRICE_BELOW_COST": ("crit", False, "Продажа по текущей цене убыточна.",
                         "Проверить себестоимость и цену; при подтверждении — поднять цену."),
    "MARGIN_BELOW_MIN": ("crit", False, "Маржа ниже минимального порога правила.", "Открыть рекомендацию по цене."),
    "RULE_CONFLICT": ("warn", False, "Для минимальной маржи нужна цена выше лимита изменения.",
                      "Принять решение вручную: цена или пересмотр правила."),
    "COST_JUMP": ("warn", False, "Себестоимость заметно изменилась после последнего пересмотра цены.",
                  "Проверить закупочную цену и техкарту."),
    "MARGIN_DROP": ("warn", False, "Маржинальность позиции снизилась к предыдущему периоду.",
                    "Проверить скидки и себестоимость."),
    "PRICE_DEVIATION": ("warn", False, "Цена в точке сильно отличается от медианы сети.",
                        "Проверить, не ошибка ли это в прайсе."),
}  # fmt: skip


@st.cache_data(show_spinner=False, ttl=600)
def load_alerts():
    return repo.alerts(ui.get_conn())


@st.cache_data(show_spinner=False, ttl=600)
def load_dq():
    return repo.dq_summary(ui.get_conn())


al = load_alerts()
if al.empty:
    st.info("Предупреждений нет. Выполните расчёт на экране «Цены к пересмотру».")
    ui.footer()
    st.stop()


@st.cache_data(show_spinner=False, ttl=600)
def on_hold_count() -> int:
    conn = ui.get_conn()
    run = repo.last_done_run(conn)
    if run is None:
        return 0
    return int((repo.recommendations(conn, run["run_id"])["status"] == "ON_HOLD").sum())


opened = al[al["status"].isin(["NEW", "IN_PROGRESS"])]
reliab = opened["alert_code"].map(lambda c: TYPE_INFO.get(c, ("", False))[1])
dq = load_dq()
dup = int(dq.loc[dq["severity"] == "WARN", "issues"].sum()) if len(dq) else 0
k = st.columns(4)
k[0].metric(
    "Критично",
    ui.fmt_int((opened["severity"] == "CRIT").sum()),
    f"из них {int(reliab.sum())} — ошибки данных",
    delta_color="off",
    delta_arrow="off",
    help="Расчёт недостоверен или продажи убыточны",
)
k[1].metric(
    "Требует внимания",
    ui.fmt_int((opened["severity"] == "WARN").sum()),
    help="Маржа под угрозой, нужна проверка",
)
k[2].metric(
    "Информация",
    ui.fmt_int(on_hold_count() + (1 if dup else 0)),
    "промо-позиции, дубли при загрузке",
    delta_color="off",
    delta_arrow="off",
    help="Учтено системой, действий не требует",
)
k[3].metric("Решено / отклонено", ui.fmt_int(al["status"].isin(["RESOLVED", "DISMISSED"]).sum()))

ui.level("По типам", "Какие проблемы найдены", "Сначала — влияющие на достоверность расчёта.")
by_type = opened.groupby("alert_code").size().reset_index(name="n")
by_type["lvl"] = by_type["alert_code"].map(lambda c: TYPE_INFO.get(c, ("warn",))[0])
by_type["rel"] = by_type["alert_code"].map(lambda c: TYPE_INFO.get(c, ("", False))[1])
by_type["order"] = by_type["lvl"].map({"crit": 0, "warn": 2}) + (~by_type["rel"]).astype(int)
by_type = by_type.sort_values(["order", "n"], ascending=[True, False])
summary = pd.DataFrame(
    {
        "Уровень": [
            ui.LEVELS[x] + (" · достоверность" if r else "")
            for x, r in zip(by_type["lvl"], by_type["rel"], strict=True)
        ],
        "Что найдено": by_type["alert_code"].map(TITLES),
        "Сколько": by_type["n"],
        "Что это значит": by_type["alert_code"].map(lambda c: TYPE_INFO.get(c, ("", False, ""))[2]),
        "Что сделать": by_type["alert_code"].map(
            lambda c: TYPE_INFO.get(c, ("", False, "", ""))[3]
        ),
    }
)
info_rows = []
if on_hold_count():
    info_rows.append(["Информация", "Изменение цены отложено (промо)", on_hold_count(),
                      "Товар в промоакции — изменение перенесено.", "Действий не требуется."])  # fmt: skip
if dup:
    info_rows.append(["Информация", "Дубли строк при загрузке", dup,
                      "Повторяющиеся строки удалены автоматически.", "Действий не требуется."])  # fmt: skip
if info_rows:
    summary = pd.concat(
        [summary, pd.DataFrame(info_rows, columns=summary.columns)], ignore_index=True
    )
st.dataframe(summary, hide_index=True, width="stretch")

ui.level("Список", "Предупреждения")

stores = ui.cached_stores()
f = st.columns(4)
f_sev = f[0].multiselect(
    "Уровень", ["CRIT", "WARN"], format_func=ui.SEVERITY_LABEL.get, placeholder="Любой"
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
df = df.assign(
    _o=df["severity"].map({"CRIT": 0, "WARN": 2})
    + (~df["alert_code"].map(lambda c: TYPE_INFO.get(c, ("", False))[1])).astype(int)
)
df = df.sort_values(["_o", "first_seen_at"], ascending=[True, False]).reset_index(drop=True)

show = pd.DataFrame(
    {
        "№": df["alert_id"],
        "Уровень": df["severity"].map(
            lambda s: ("● " if s == "CRIT" else "▲ ") + ui.SEVERITY_LABEL[s]
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
    st.dataframe(
        pd.DataFrame(
            {
                "Проверка": dq["check_code"].map(lambda c: ui.DQ_LABEL.get(c, c)),
                "Код": dq["check_code"],
                "Уровень": dq["severity"].map(
                    {"CRIT": "Исключено из расчёта", "WARN": "Информация"}
                ),
                "Строк": dq["issues"],
            }
        ),
        hide_index=True,
    )
    st.caption("Строки с ошибками не попадают в расчёты и сохраняются в журнале проблем качества.")
ui.footer()
