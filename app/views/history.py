"""Экран «История»: запуски, журнал событий, версии правил, решения по рекомендациям."""

from __future__ import annotations

import streamlit as st
from components import ui

from pmc import repository as repo
from pmc.db import fetch_df

ui.page_header(
    "Методика и история",
    "Как считаются показатели и цены, какие правила действуют и кто что делал. "
    "Журнал только дополняется: записи нельзя изменить или удалить.",
)

conn = ui.get_conn()
t0, t1, t2, t3, t4 = st.tabs(
    ["Методика", "Запуски расчёта", "Журнал событий", "Версии правил", "Решения по рекомендациям"]
)

with t0:
    m1, m2 = st.columns(2)
    m1.markdown(
        """
**Как система рассчитывает цену**

1. Цена для целевой маржи: `цена = себестоимость / (1 − целевая маржа)`, округление вверх до шага.
2. Не ниже минимальной маржи: `мин. цена = ⌈себестоимость / (1 − минимальная маржа)⌉`.
3. Снижение цены — только если правило разрешает; иначе «Цена в норме».
4. Лимит изменения: не больше заданного % за пересмотр. Если лимит не даёт достичь минимальной
   маржи — «Нужно решение вручную».
5. Товар в промоакции — изменение откладывается.
6. Ошибки данных (нет себестоимости, цена ≤ 0, товар выведен) — расчёт не выполняется.

Приоритет правил: товар → категория → точка → вся сеть. Денежные расчёты — на Decimal.
"""
    )
    m2.markdown(
        f"""
**Показатели**

- **Валовая маржинальность** — {ui.HELP["margin"]}
- **Валовая прибыль** — {ui.HELP["gp"]}
- **Маржа сейчас** — {ui.HELP["plan_margin"]}
- **Маржа после** — {ui.HELP["after_margin"]}

**Ограничения модели**

- Данные синтетические, сгенерированы для учебного проекта.
- Маржа валовая: аренда, персонал и логистика не учитываются.
- Реакция спроса на цену не оценена по данным — сценарии не являются прогнозом.
- Система не меняет цены в кассе: согласование только фиксирует решение.
"""
    )

with t1:
    runs = repo.runs(conn)
    runs["status"] = runs["status"].map(
        {"done": "Завершён", "failed": "Ошибка", "running": "Выполняется"}
    )
    runs["run_id"] = runs["run_id"].str[:8]
    st.dataframe(
        runs.rename(
            columns={
                "run_id": "Запуск",
                "started_at": "Начало",
                "finished_at": "Окончание",
                "as_of_date": "Дата расчёта",
                "rule_version": "Правила",
                "status": "Статус",
                "is_forced": "Принудительно",
                "triggered_by": "Кто запустил",
                "offers_total": "Предложений",
                "error_text": "Ошибка",
            }
        ),
        hide_index=True,
        width="stretch",
        column_config={
            "Начало": st.column_config.DatetimeColumn(format="DD.MM.YYYY HH:mm:ss"),
            "Окончание": st.column_config.DatetimeColumn(format="DD.MM.YYYY HH:mm:ss"),
        },
    )

with t2:
    ev = repo.events(conn)
    types = sorted(ev["event_type"].unique())
    sel = st.multiselect("Тип события", types, placeholder="Все")
    if sel:
        ev = ev[ev["event_type"].isin(sel)]
    st.dataframe(
        ev.rename(
            columns={
                "event_at": "Время",
                "event_type": "Событие",
                "severity": "Уровень",
                "actor_role": "Роль",
                "message": "Описание",
                "run_id": "Запуск",
            }
        ),
        hide_index=True,
        width="stretch",
        height=480,
        column_config={
            "Время": st.column_config.DatetimeColumn(format="DD.MM.YYYY HH:mm:ss"),
            "Описание": st.column_config.TextColumn(width="large"),
        },
    )

with t3:
    sets = repo.rule_sets(conn)
    st.dataframe(
        sets.rename(
            columns={
                "version": "Версия",
                "effective_from": "Действует с",
                "is_locked": "Использована",
                "created_at": "Создана",
                "created_by": "Автор",
                "comment": "Комментарий",
                "rules": "Правил",
            }
        ),
        hide_index=True,
        width="stretch",
    )
    if len(sets):
        v = st.selectbox("Показать правила версии", sets["version"])
        rows = fetch_df(
            conn,
            """
            SELECT r.scope_type AS "Уровень", COALESCE(c.name, s.name, p.name, 'вся сеть') AS "Объект",
                   r.target_margin * 100 AS "Цель, %%", r.min_margin * 100 AS "Мин., %%",
                   r.max_change * 100 AS "Лимит изм., %%", r.rounding_step AS "Шаг, ₽",
                   r.rounding_mode AS "Округление", r.allow_decrease AS "Снижение"
            FROM calc.pricing_rule r JOIN calc.rule_set rs USING (rule_set_id)
            LEFT JOIN core.category c ON r.scope_type = 'category' AND c.category_id = r.scope_id
            LEFT JOIN core.store s ON r.scope_type = 'store' AND s.store_id = r.scope_id
            LEFT JOIN core.product p ON r.scope_type = 'product' AND p.product_id = r.scope_id
            WHERE rs.version = %s ORDER BY r.rule_id""",
            (int(v),),
        )
        st.dataframe(ui.to_display(rows), hide_index=True, width="stretch")

with t4:
    dec = fetch_df(
        conn,
        """
        SELECT r.decided_at AS "Время", r.decided_by AS "Роль", r.status AS "Решение", s.name AS "Точка",
               p.name AS "Товар", r.current_price AS "Было, ₽", r.recommended_price AS "Стало, ₽",
               r.decision_comment AS "Комментарий"
        FROM calc.price_recommendation r JOIN core.store s USING (store_id) JOIN core.product p USING (product_id)
        WHERE r.decided_at IS NOT NULL ORDER BY r.decided_at DESC LIMIT 500""",
    )
    if dec.empty:
        st.info("Решений по рекомендациям пока нет.")
    else:
        dec["Решение"] = dec["Решение"].map(ui.STATUS_LABEL)
        st.dataframe(
            ui.to_display(dec),
            hide_index=True,
            width="stretch",
            column_config={"Время": st.column_config.DatetimeColumn(format="DD.MM.YYYY HH:mm")},
        )
ui.footer()
