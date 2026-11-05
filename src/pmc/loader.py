"""Загрузка демо-данных: справочники → core, факты → raw → проверки → core.

Порядок (FR-02):
1. Справочники (точки, категории, товары, акции, календарь) — сразу в core.
2. Продажи, цены, себестоимость — сначала в raw «как есть», включая ошибочные строки.
3. Проверки качества (pmc.quality): валидные строки → core, проблемы → audit.dq_issue.
4. Остатки и списания — в core.
5. Стартовый набор правил ценообразования v1 и запись в журнал.
Всё выполняется в одной транзакции: при ошибке база остаётся как была.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import date

import pandas as pd
import psycopg
from psycopg.types.json import Jsonb

from pmc import quality
from pmc.db import copy_rows, execute, fetch_one
from pmc.generator.facts import DemoData

log = logging.getLogger(__name__)

SALE_COLS = [
    "sale_line_id",
    "order_id",
    "sold_at",
    "sale_date",
    "store_id",
    "product_id",
    "quantity",
    "unit_price",
    "discount_amount",
    "operation_type",
    "is_cancelled",
    "promo_id",
]
COST_COLS = ["product_id", "valid_from", "unit_cost", "source", "calc_version"]
PRICE_COLS = ["store_id", "product_id", "valid_from", "price"]

DEFAULT_RULES = [
    # scope_type, scope_id, target, min, max_change, step, mode, allow_decrease
    ("global", None, "0.45", "0.30", "0.10", "10", "up", False),
    ("category", 2, "0.45", "0.30", "0.10", "5", "up", False),  # Суши: дешёвые позиции, шаг 5 ₽
    ("category", 3, "0.40", "0.28", "0.10", "10", "up", False),  # Наборы
    ("category", 4, "0.55", "0.40", "0.15", "5", "up", False),  # Напитки: шаг 5 ₽
]

_ALL_TABLES = [
    "calc.alert",
    "calc.price_recommendation",
    "calc.pricing_run",
    "calc.pricing_rule",
    "calc.rule_set",
    "core.sale_line",
    "core.stock_daily",
    "core.write_off",
    "core.store_price",
    "core.product_cost",
    "core.promo_item",
    "core.promo",
    "core.store_calendar",
    "core.product",
    "core.category",
    "core.store",
    "raw.sale_line",
    "raw.product_cost",
    "raw.store_price",
    "audit.dq_issue",
    "audit.load_batch",
    "audit.event_log",
]


def records(df: pd.DataFrame, cols: list[str]) -> list[tuple]:
    """DataFrame → список кортежей с обычными типами Python (None вместо пропусков)."""
    out = []
    for c in cols:
        s = df[c]
        if pd.api.types.is_datetime64_any_dtype(s):
            out.append([None if pd.isna(v) else v.to_pydatetime() for v in s])
        else:
            out.append(s.astype(object).where(s.notna(), None).tolist())
    return list(zip(*out, strict=True)) if out else []


def row_hash(values: tuple) -> str:
    return hashlib.sha256(
        "|".join("" if v is None else str(v) for v in values).encode()
    ).hexdigest()


def is_loaded(conn: psycopg.Connection) -> bool:
    row = fetch_one(conn, "SELECT count(*) AS n FROM core.store")
    return bool(row and row["n"] > 0)


def reset(conn: psycopg.Connection) -> None:
    """Полная очистка данных (структура БД остаётся)."""
    execute(conn, f"TRUNCATE {', '.join(_ALL_TABLES)} RESTART IDENTITY CASCADE")


def load_demo(conn: psycopg.Connection, data: DemoData) -> dict:
    batch_id = uuid.uuid4()
    with conn.transaction():
        _load_reference(conn, data)

        raw_counts = {
            "sale_line": copy_rows(
                conn,
                "raw.sale_line",
                ["batch_id", *SALE_COLS],
                ((batch_id, *r) for r in records(data.sale_line, SALE_COLS)),
            ),
            "product_cost": copy_rows(
                conn,
                "raw.product_cost",
                ["batch_id", *COST_COLS],
                ((batch_id, *r) for r in records(data.product_cost, COST_COLS)),
            ),
            "store_price": copy_rows(
                conn,
                "raw.store_price",
                ["batch_id", *PRICE_COLS],
                ((batch_id, *r) for r in records(data.store_price, PRICE_COLS)),
            ),
        }

        store_ids = set(data.stores["store_id"].tolist())
        product_ids = set(data.products["product_id"].tolist())
        promo_ids = set(data.promo["promo_id"].tolist())
        sales = quality.check_sale_lines(
            data.sale_line[SALE_COLS], store_ids, product_ids, promo_ids
        )
        costs = quality.check_costs(data.product_cost[COST_COLS], product_ids)
        prices = quality.check_prices(data.store_price[PRICE_COLS], store_ids, product_ids)
        missing = quality.check_missing_prices(prices.valid, store_ids, product_ids, data.end)
        issues = sales.issues + costs.issues + prices.issues + missing

        core_counts = {
            "sale_line": copy_rows(
                conn,
                "core.sale_line",
                [*SALE_COLS, "source_row_hash"],
                ((*r, row_hash(r)) for r in records(sales.valid, SALE_COLS)),
            ),
            "product_cost": copy_rows(
                conn, "core.product_cost", COST_COLS, records(costs.valid, COST_COLS)
            ),
            "store_price": copy_rows(
                conn, "core.store_price", PRICE_COLS, records(prices.valid, PRICE_COLS)
            ),
        }
        stock_cols = list(data.stock_daily.columns)
        core_counts["stock_daily"] = copy_rows(
            conn, "core.stock_daily", stock_cols, records(data.stock_daily, stock_cols)
        )
        wo_cols = list(data.write_off.columns)
        core_counts["write_off"] = copy_rows(
            conn, "core.write_off", wo_cols, records(data.write_off, wo_cols)
        )

        copy_rows(
            conn,
            "audit.dq_issue",
            [
                "batch_id",
                "source_table",
                "source_key",
                "check_code",
                "severity",
                "store_id",
                "product_id",
                "details",
            ],
            (
                (
                    batch_id,
                    i["source_table"],
                    i["source_key"],
                    i["check_code"],
                    i["severity"],
                    i["store_id"],
                    i["product_id"],
                    json.dumps(i["details"], ensure_ascii=False),
                )
                for i in issues
            ),
        )
        execute(
            conn,
            "INSERT INTO audit.load_batch (batch_id, seed, rows_raw, rows_core, issues) VALUES (%s,%s,%s,%s,%s)",
            (batch_id, data.seed, Jsonb(raw_counts), Jsonb(core_counts), len(issues)),
        )
        ensure_default_rules(conn, data.start)
        execute(
            conn,
            "INSERT INTO audit.event_log (event_type, actor_role, message, payload) VALUES (%s,%s,%s,%s)",
            (
                "DATA_LOADED",
                "система",
                f"Загружены демо-данные: {core_counts['sale_line']} строк продаж, проблем качества: {len(issues)}",
                Jsonb({"batch_id": str(batch_id), "raw": raw_counts, "core": core_counts}),
            ),
        )
    log.info("Загрузка завершена: raw=%s core=%s проблем=%s", raw_counts, core_counts, len(issues))
    return {"batch_id": batch_id, "raw": raw_counts, "core": core_counts, "issues": len(issues)}


def _load_reference(conn: psycopg.Connection, data: DemoData) -> None:
    cols = ["store_id", "code", "name", "region", "city", "is_active", "opened_on"]
    copy_rows(conn, "core.store", cols, records(data.stores, cols))
    cols = ["category_id", "name", "parent_id"]
    copy_rows(conn, "core.category", cols, records(data.categories, cols))
    cols = ["product_id", "sku", "name", "category_id", "sale_unit", "is_fresh", "is_active"]
    copy_rows(conn, "core.product", cols, records(data.products, cols))
    cols = ["promo_id", "name", "date_from", "date_to", "discount_pct"]
    copy_rows(conn, "core.promo", cols, records(data.promo, cols))
    cols = ["promo_id", "product_id", "store_id"]
    copy_rows(conn, "core.promo_item", cols, records(data.promo_item, cols))
    cols = ["store_id", "calendar_date", "is_open", "note"]
    copy_rows(conn, "core.store_calendar", cols, records(data.store_calendar, cols))


def ensure_default_rules(conn: psycopg.Connection, effective_from: date) -> None:
    if fetch_one(conn, "SELECT 1 AS x FROM calc.rule_set LIMIT 1"):
        return
    rs = fetch_one(
        conn,
        "INSERT INTO calc.rule_set (version, effective_from, created_by, comment) "
        "VALUES (1, %s, 'система', 'Стартовые правила демо-версии') RETURNING rule_set_id",
        (effective_from,),
    )
    for scope_type, scope_id, tm, mm, mc, step, mode, dec in DEFAULT_RULES:
        execute(
            conn,
            "INSERT INTO calc.pricing_rule (rule_set_id, scope_type, scope_id, target_margin, min_margin, "
            "max_change, rounding_step, rounding_mode, allow_decrease) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (rs["rule_set_id"], scope_type, scope_id, tm, mm, mc, step, mode, dec),
        )
