"""Запросы к PostgreSQL. Единственный модуль, который знает про SQL-таблицы (кроме загрузчика).

Суммы считает база (NUMERIC — точно), отношения и логика — Python (ADR-02).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import psycopg

from pmc.db import fetch_all, fetch_df, fetch_one

# --- Справочники и даты -----------------------------------------------------------------------


def data_period(conn: psycopg.Connection) -> tuple[date, date] | None:
    row = fetch_one(conn, "SELECT min(sale_date) AS d0, max(sale_date) AS d1 FROM core.sale_line")
    if not row or row["d0"] is None:
        return None
    return row["d0"], row["d1"]


def stores(conn: psycopg.Connection) -> pd.DataFrame:
    return fetch_df(
        conn, "SELECT store_id, code, name, city, is_active FROM core.store ORDER BY store_id"
    )


def categories(conn: psycopg.Connection) -> pd.DataFrame:
    return fetch_df(conn, "SELECT category_id, name FROM core.category ORDER BY category_id")


def products(conn: psycopg.Connection) -> pd.DataFrame:
    return fetch_df(
        conn,
        "SELECT p.product_id, p.sku, p.name, p.category_id, c.name AS category_name, p.is_active "
        "FROM core.product p JOIN core.category c USING (category_id) ORDER BY p.product_id",
    )


# --- Метрики продаж ---------------------------------------------------------------------------

_DIMENSIONS = {
    "network": ([], []),
    "day": (["f.sale_date"], ["sale_date"]),
    "store": (["f.store_id", "s.name"], ["store_id", "store_name"]),
    "category": (["f.category_id", "c.name"], ["category_id", "category_name"]),
    "product": (
        ["f.product_id", "p.sku", "p.name", "c.name"],
        ["product_id", "sku", "product_name", "category_name"],
    ),
    "offer": (
        ["f.store_id", "s.name", "f.product_id", "p.name", "c.name"],
        ["store_id", "store_name", "product_id", "product_name", "category_name"],
    ),
}


def sales_aggregates(
    conn: psycopg.Connection,
    date_from: date,
    date_to: date,
    grain: str = "network",
    store_ids: list[int] | None = None,
    category_ids: list[int] | None = None,
    product_ids: list[int] | None = None,
) -> pd.DataFrame:
    """Суммы для метрик М-01…М-05 по выбранному срезу.

    revenue          — вся чистая выручка (М-01)
    revenue_costed   — выручка строк, для которых известна себестоимость на дату продажи
    cogs             — себестоимость реализованной продукции по тем же строкам
    net_qty          — чистое количество (продажи − возвраты, без отмен)
    lines_no_cost    — число строк без себестоимости (исключены из валовой прибыли)
    """
    if grain not in _DIMENSIONS:
        raise ValueError(f"Неизвестный срез: {grain}")
    exprs, names = _DIMENSIONS[grain]
    select_dims = "".join(f"{e} AS {n}, " for e, n in zip(exprs, names, strict=True))
    group_by = f"GROUP BY {', '.join(exprs)} ORDER BY {', '.join(exprs)}" if exprs else ""
    sql = f"""
        SELECT {select_dims}
               COALESCE(sum(f.net_revenue), 0)                                         AS revenue,
               COALESCE(sum(f.net_revenue) FILTER (WHERE f.unit_cost IS NOT NULL), 0)  AS revenue_costed,
               COALESCE(sum(f.net_cogs), 0)                                            AS cogs,
               COALESCE(sum(f.net_quantity), 0)                                        AS net_qty,
               count(*) FILTER (WHERE f.unit_cost IS NULL AND NOT f.is_cancelled)      AS lines_no_cost,
               count(DISTINCT f.order_id) FILTER (WHERE NOT f.is_cancelled)            AS orders
        FROM core.v_sale_fact f
        JOIN core.store s    ON s.store_id = f.store_id
        JOIN core.product p  ON p.product_id = f.product_id
        JOIN core.category c ON c.category_id = f.category_id
        WHERE f.sale_date BETWEEN %(d0)s AND %(d1)s
          AND (%(stores)s::int[] IS NULL OR f.store_id = ANY(%(stores)s::int[]))
          AND (%(cats)s::int[] IS NULL OR f.category_id = ANY(%(cats)s::int[]))
          AND (%(prods)s::int[] IS NULL OR f.product_id = ANY(%(prods)s::int[]))
        {group_by}
    """
    params = {
        "d0": date_from,
        "d1": date_to,
        "stores": store_ids or None,
        "cats": category_ids or None,
        "prods": product_ids or None,
    }
    return fetch_df(conn, sql, params)


def sale_lines_for_check(conn: psycopg.Connection, date_from: date, date_to: date) -> pd.DataFrame:
    """Сырые строки core.sale_line для независимой проверки расчётов в Python (TC-15)."""
    return fetch_df(
        conn,
        "SELECT sale_line_id, sale_date, store_id, product_id, quantity, unit_price, discount_amount, "
        "operation_type, is_cancelled FROM core.sale_line WHERE sale_date BETWEEN %s AND %s",
        (date_from, date_to),
    )


def product_costs(conn: psycopg.Connection) -> pd.DataFrame:
    return fetch_df(
        conn,
        "SELECT product_id, valid_from, unit_cost, calc_version FROM core.product_cost "
        "ORDER BY product_id, valid_from",
    )


def price_history(conn: psycopg.Connection, store_id: int, product_id: int) -> pd.DataFrame:
    return fetch_df(
        conn,
        "SELECT valid_from, price FROM core.store_price WHERE store_id=%s AND product_id=%s "
        "ORDER BY valid_from",
        (store_id, product_id),
    )


def cost_history(conn: psycopg.Connection, product_id: int) -> pd.DataFrame:
    return fetch_df(
        conn,
        "SELECT valid_from, unit_cost FROM core.product_cost WHERE product_id=%s "
        "ORDER BY valid_from",
        (product_id,),
    )


# --- Предложения на дату ----------------------------------------------------------------------

OFFERS_SQL = """
WITH cost_valid AS (
    SELECT DISTINCT ON (product_id) product_id, unit_cost, valid_from
    FROM core.product_cost WHERE valid_from <= %(as_of)s
    ORDER BY product_id, valid_from DESC
), cost_prev AS (
    SELECT DISTINCT ON (c.product_id) c.product_id, c.unit_cost AS previous_cost
    FROM core.product_cost c JOIN cost_valid v USING (product_id)
    WHERE c.valid_from < v.valid_from
    ORDER BY c.product_id, c.valid_from DESC
), cost_rejected AS (
    -- Последняя версия себестоимости была отклонена проверкой качества: актуальная себестоимость неизвестна.
    SELECT DISTINCT d.product_id
    FROM audit.dq_issue d LEFT JOIN cost_valid v USING (product_id)
    WHERE d.source_table = 'product_cost' AND d.check_code = 'NON_POSITIVE_COST'
      AND (d.details->>'valid_from')::date <= %(as_of)s
      AND (v.valid_from IS NULL OR (d.details->>'valid_from')::date > v.valid_from)
), price_now AS (
    SELECT DISTINCT ON (store_id, product_id) store_id, product_id, price, valid_from
    FROM core.store_price WHERE valid_from <= %(as_of)s
    ORDER BY store_id, product_id, valid_from DESC
), network_median AS (
    SELECT product_id, percentile_cont(0.5) WITHIN GROUP (ORDER BY price)::numeric(12,2) AS network_median
    FROM price_now GROUP BY product_id
), promo_now AS (
    SELECT DISTINCT s.store_id, pi.product_id
    FROM core.promo pr
    JOIN core.promo_item pi ON pi.promo_id = pr.promo_id
    JOIN core.store s ON pi.store_id IS NULL OR pi.store_id = s.store_id
    WHERE %(as_of)s BETWEEN pr.date_from AND pr.date_to
), q28 AS (
    SELECT store_id, product_id, sum(net_quantity) AS q0
    FROM core.sale_line
    WHERE sale_date BETWEEN %(as_of)s::date - 27 AND %(as_of)s
    GROUP BY store_id, product_id
)
SELECT s.store_id, s.name AS store_name, p.product_id, p.sku, p.name AS product_name,
       p.category_id, c.name AS category_name,
       (s.is_active AND p.is_active)       AS is_active,
       cv.unit_cost, cv.valid_from          AS cost_valid_from,
       cp.previous_cost,
       (cr.product_id IS NOT NULL)          AS cost_invalid,
       pn.price                             AS current_price,
       pn.valid_from                        AS price_valid_from,
       nm.network_median,
       (pr.product_id IS NOT NULL)          AS in_promo,
       COALESCE(q.q0, 0)                    AS q0
FROM core.store s
CROSS JOIN core.product p
JOIN core.category c ON c.category_id = p.category_id
LEFT JOIN cost_valid cv     ON cv.product_id = p.product_id
LEFT JOIN cost_prev cp      ON cp.product_id = p.product_id
LEFT JOIN cost_rejected cr  ON cr.product_id = p.product_id
LEFT JOIN price_now pn      ON pn.store_id = s.store_id AND pn.product_id = p.product_id
LEFT JOIN network_median nm ON nm.product_id = p.product_id
LEFT JOIN promo_now pr      ON pr.store_id = s.store_id AND pr.product_id = p.product_id
LEFT JOIN q28 q             ON q.store_id = s.store_id AND q.product_id = p.product_id
ORDER BY s.store_id, p.product_id
"""


def offers_as_of(conn: psycopg.Connection, as_of: date) -> pd.DataFrame:
    return fetch_df(conn, OFFERS_SQL, {"as_of": as_of})


def margin_windows(conn: psycopg.Connection, as_of: date, window: int = 14) -> pd.DataFrame:
    """Выручка и себестоимость предложения за последние N дней и за N дней до них (для MARGIN_DROP)."""
    return fetch_df(
        conn,
        """
        SELECT store_id, product_id,
               sum(net_revenue) FILTER (WHERE sale_date >  %(as_of)s::date - %(w)s) AS rev_cur,
               sum(net_cogs)    FILTER (WHERE sale_date >  %(as_of)s::date - %(w)s) AS cogs_cur,
               sum(net_revenue) FILTER (WHERE sale_date <= %(as_of)s::date - %(w)s) AS rev_prev,
               sum(net_cogs)    FILTER (WHERE sale_date <= %(as_of)s::date - %(w)s) AS cogs_prev
        FROM core.v_sale_fact
        WHERE sale_date BETWEEN %(as_of)s::date - 2 * %(w)s + 1 AND %(as_of)s
          AND unit_cost IS NOT NULL
        GROUP BY store_id, product_id
        """,
        {"as_of": as_of, "w": window},
    )


def cost_changes(conn: psycopg.Connection, as_of: date, lookback_days: int = 30) -> pd.DataFrame:
    """Версии себестоимости за последние N дней вместе с предыдущей версией (для COST_JUMP)."""
    return fetch_df(
        conn,
        """
        SELECT product_id, valid_from, unit_cost, previous_cost FROM (
            SELECT product_id, valid_from, unit_cost,
                   LAG(unit_cost) OVER (PARTITION BY product_id ORDER BY valid_from) AS previous_cost
            FROM core.product_cost WHERE valid_from <= %(as_of)s
        ) t
        WHERE previous_cost IS NOT NULL AND valid_from > %(as_of)s::date - %(lb)s
        """,
        {"as_of": as_of, "lb": lookback_days},
    )


def critical_dq_issues(conn: psycopg.Connection) -> pd.DataFrame:
    return fetch_df(
        conn,
        "SELECT issue_id, source_table, source_key, check_code, store_id, product_id, details "
        "FROM audit.dq_issue WHERE severity = 'CRIT' ORDER BY issue_id",
    )


def dq_summary(conn: psycopg.Connection) -> pd.DataFrame:
    return fetch_df(
        conn,
        "SELECT check_code, severity, count(*) AS issues FROM audit.dq_issue "
        "GROUP BY 1, 2 ORDER BY 3 DESC",
    )


# --- Правила ----------------------------------------------------------------------------------


def rule_set_for(conn: psycopg.Connection, as_of: date) -> dict | None:
    return fetch_one(
        conn,
        "SELECT rule_set_id, version, effective_from, is_locked FROM calc.rule_set "
        "WHERE effective_from <= %s ORDER BY version DESC LIMIT 1",
        (as_of,),
    )


def latest_rule_set(conn: psycopg.Connection) -> dict | None:
    return fetch_one(
        conn,
        "SELECT rule_set_id, version, effective_from, is_locked FROM calc.rule_set "
        "ORDER BY version DESC LIMIT 1",
    )


def rules_of(conn: psycopg.Connection, rule_set_id: int) -> list[dict]:
    return fetch_all(
        conn,
        "SELECT * FROM calc.pricing_rule WHERE rule_set_id = %s ORDER BY rule_id",
        (rule_set_id,),
    )


def rule_sets(conn: psycopg.Connection) -> pd.DataFrame:
    return fetch_df(
        conn,
        """SELECT rs.version, rs.effective_from, rs.is_locked, rs.created_at, rs.created_by, rs.comment,
                  count(r.rule_id) AS rules
           FROM calc.rule_set rs LEFT JOIN calc.pricing_rule r USING (rule_set_id)
           GROUP BY rs.rule_set_id ORDER BY rs.version DESC""",
    )


# --- Результаты расчёта -----------------------------------------------------------------------


def last_done_run(conn: psycopg.Connection) -> dict | None:
    return fetch_one(
        conn,
        "SELECT r.*, rs.version AS rule_version FROM calc.pricing_run r JOIN calc.rule_set rs USING (rule_set_id) "
        "WHERE r.status = 'done' ORDER BY r.finished_at DESC LIMIT 1",
    )


def recommendations(conn: psycopg.Connection, run_id) -> pd.DataFrame:
    return fetch_df(
        conn,
        """
        SELECT r.recommendation_id, r.store_id, s.name AS store_name, r.product_id, p.sku,
               p.name AS product_name, p.category_id, c.name AS category_name,
               r.unit_cost, r.current_price, r.recommended_price, r.change_abs, r.change_pct,
               r.status, r.reason_code, r.reason_text, r.details, r.decided_by, r.decided_at,
               rl.scope_type AS rule_scope, rl.min_margin, rl.target_margin
        FROM calc.price_recommendation r
        JOIN core.store s ON s.store_id = r.store_id
        JOIN core.product p ON p.product_id = r.product_id
        JOIN core.category c ON c.category_id = p.category_id
        LEFT JOIN calc.pricing_rule rl ON rl.rule_id = r.rule_id
        WHERE r.run_id = %s
        ORDER BY r.store_id, r.product_id
        """,
        (run_id,),
    )


def runs(conn: psycopg.Connection, limit: int = 100) -> pd.DataFrame:
    return fetch_df(
        conn,
        """
        SELECT r.run_id::text AS run_id, r.started_at, r.finished_at, r.as_of_date, rs.version AS rule_version, r.status,
               r.is_forced, r.triggered_by, r.offers_total, r.error_text
        FROM calc.pricing_run r JOIN calc.rule_set rs USING (rule_set_id)
        ORDER BY r.started_at DESC LIMIT %s
        """,
        (limit,),
    )


def alerts(conn: psycopg.Connection, statuses: list[str] | None = None) -> pd.DataFrame:
    return fetch_df(
        conn,
        """
        SELECT a.alert_id, a.severity, a.alert_code, a.store_id, s.name AS store_name, a.product_id,
               p.name AS product_name, a.description, a.first_seen_at, a.last_seen_at, a.occurrences,
               a.status, a.status_changed_by, a.status_changed_at
        FROM calc.alert a
        LEFT JOIN core.store s ON s.store_id = a.store_id
        LEFT JOIN core.product p ON p.product_id = a.product_id
        WHERE (%(st)s::text[] IS NULL OR a.status = ANY(%(st)s::text[]))
        ORDER BY CASE a.severity WHEN 'CRIT' THEN 0 ELSE 1 END, a.first_seen_at DESC, a.alert_id
        """,
        {"st": statuses or None},
    )


def events(conn: psycopg.Connection, limit: int = 500) -> pd.DataFrame:
    return fetch_df(
        conn,
        "SELECT event_at, event_type, severity, actor_role, message, run_id::text AS run_id FROM audit.event_log "
        "ORDER BY event_id DESC LIMIT %s",
        (limit,),
    )
