"""Подключение к PostgreSQL и небольшие помощники для запросов.

Используется драйвер psycopg 3 без ORM (ADR-03): в коде виден настоящий SQL.
Числа NUMERIC приходят в Python как Decimal — точность денег не теряется.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import pandas as pd
import psycopg
from psycopg.rows import dict_row

from pmc.config import get_settings

log = logging.getLogger(__name__)


def connect(url: str | None = None) -> psycopg.Connection:
    """Соединение в режиме autocommit: каждый запрос фиксируется сразу,
    а там, где нужна атомарность, код явно открывает `with conn.transaction():`."""
    return psycopg.connect(
        url or get_settings().database_url, row_factory=dict_row, autocommit=True
    )


@contextmanager
def transaction(url: str | None = None) -> Iterator[psycopg.Connection]:
    """Одна транзакция: при исключении всё откатывается (ADR-09)."""
    conn = connect(url)
    try:
        with conn.transaction():
            yield conn
    finally:
        conn.close()


def fetch_all(
    conn: psycopg.Connection, sql: str, params: Sequence | dict | None = None
) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def fetch_one(
    conn: psycopg.Connection, sql: str, params: Sequence | dict | None = None
) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def fetch_df(
    conn: psycopg.Connection, sql: str, params: Sequence | dict | None = None
) -> pd.DataFrame:
    """Результат запроса как DataFrame. Колонки NUMERIC остаются Decimal (dtype object)."""
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
        columns = [d.name for d in cur.description] if cur.description else []
    return pd.DataFrame(rows, columns=columns)


def execute(conn: psycopg.Connection, sql: str, params: Sequence | dict | None = None) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def copy_rows(conn: psycopg.Connection, table: str, columns: Sequence[str], rows: Any) -> int:
    """Быстрая массовая вставка через COPY. rows — итерируемое кортежей."""
    cols = ", ".join(columns)
    n = 0
    with conn.cursor() as cur, cur.copy(f"COPY {table} ({cols}) FROM STDIN") as cp:
        for row in rows:
            cp.write_row(row)
            n += 1
    return n


def check_connection(url: str | None = None) -> str:
    """Проверка подключения: возвращает версию сервера или выбрасывает исключение."""
    with connect(url) as conn:
        row = fetch_one(conn, "SELECT version() AS v")
    return row["v"] if row else "?"
