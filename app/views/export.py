"""Экран «Экспорт»: выгрузка CSV и Excel с сохранением типов (FR-13)."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st
from components import ui

from pmc import metrics
from pmc import repository as repo
from pmc.export import to_csv_bytes, to_xlsx_bytes

ui.page_header(
    "Экспорт",
    "Числа выгружаются числами, даты — датами. В каждом файле есть пометка о синтетических данных.",
)

conn = ui.get_conn()
DATASETS = {
    "recs": "Рекомендации последнего расчёта",
    "alerts": "Предупреждения",
    "stores": "Показатели по точкам за период",
    "products": "Показатели по товарам за период",
    "events": "Журнал событий",
}
c = st.columns([3, 2.5, 1.5])
ds = c[0].selectbox("Таблица", list(DATASETS), format_func=DATASETS.get)
with c[1]:
    d0, d1 = ui.period_filter("ex_period")
fmt = c[2].radio("Формат", ["XLSX", "CSV"], horizontal=True)


def build() -> tuple[pd.DataFrame, dict]:
    desc = {"Таблица": DATASETS[ds], "Сформировано": f"{datetime.now():%d.%m.%Y %H:%M}"}
    if ds == "recs":
        run = repo.last_done_run(conn)
        if run is None:
            return pd.DataFrame(), desc
        df = repo.recommendations(conn, run["run_id"]).drop(columns=["details"])
        df["status"] = df["status"].map(ui.STATUS_LABEL)
        desc |= {
            "Дата расчёта": f"{run['as_of_date']:%d.%m.%Y}",
            "Версия правил": f"v{run['rule_version']}",
            "Запуск": str(run["run_id"]),
        }
        return df.rename(
            columns={
                "store_name": "Точка",
                "product_name": "Товар",
                "category_name": "Категория",
                "sku": "Артикул",
                "unit_cost": "Себестоимость",
                "current_price": "Текущая цена",
                "recommended_price": "Рекомендуемая цена",
                "change_abs": "Изменение, руб",
                "change_pct": "Изменение, доля",
                "status": "Статус",
                "reason_text": "Причина",
                "reason_code": "Код причины",
            }
        ), desc
    if ds == "alerts":
        return repo.alerts(conn), desc
    if ds == "events":
        return repo.events(conn, 5000), desc
    grain = "store" if ds == "stores" else "product"
    df = metrics.enrich(repo.sales_aggregates(conn, d0, d1, grain))
    desc |= {
        "Период": f"{d0:%d.%m.%Y} — {d1:%d.%m.%Y}",
        "Методика": "Выручка с учётом скидок и возвратов; валовая прибыль по себестоимости на дату продажи",
    }
    return df.rename(
        columns={
            "revenue": "Выручка",
            "gross_profit": "Валовая прибыль",
            "gross_margin": "Маржинальность, доля",
            "net_qty": "Продано, шт (нетто)",
            "avg_price": "Средняя цена реализации",
            "orders": "Чеков",
            "cogs": "Себестоимость продаж",
        }
    ), desc


df, desc = build()
st.caption(f"Строк: {len(df)}")
st.dataframe(ui.to_display(df.head(200)), hide_index=True, height=360)
name = f"{ds}_{datetime.now():%Y%m%d_%H%M}"
if fmt == "XLSX":
    st.download_button(
        "Скачать XLSX",
        to_xlsx_bytes({"Данные": df}, desc),
        f"{name}.xlsx",
        type="primary",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        disabled=df.empty,
    )
else:
    st.download_button(
        "Скачать CSV",
        to_csv_bytes(df),
        f"{name}.csv",
        type="primary",
        mime="text/csv",
        disabled=df.empty,
    )
ui.footer()
