"""Проверки качества данных: «сырые» данные → валидные строки + список проблем.

Простыми словами: каждая функция получает таблицу «как пришла», откладывает плохие строки
в журнал проблем с понятным кодом и возвращает только хорошие строки.
Функции не обращаются к базе и не зависят от Streamlit — их проверяют модульные тесты.

Коды проверок:
DUPLICATE              — строка повторяет уже загруженную (оставляем первую)
MISSING_VALUE          — пустое обязательное поле
FK_VIOLATION           — ссылка на несуществующий товар/точку/акцию
OUT_OF_RANGE           — количество ≤ 0, цена < 0, скидка < 0, неизвестный тип операции
DISCOUNT_OUT_OF_RANGE  — скидка больше суммы строки
NON_POSITIVE_COST      — себестоимость ≤ 0
NON_POSITIVE_PRICE     — цена ≤ 0
MISSING_PRICE          — у активной точки нет цены на активный товар на дату
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

SEVERITY = {
    "DUPLICATE": "WARN",
    "MISSING_VALUE": "CRIT",
    "FK_VIOLATION": "CRIT",
    "OUT_OF_RANGE": "CRIT",
    "DISCOUNT_OUT_OF_RANGE": "CRIT",
    "NON_POSITIVE_COST": "CRIT",
    "NON_POSITIVE_PRICE": "CRIT",
    "MISSING_PRICE": "CRIT",
}


@dataclass
class CheckResult:
    valid: pd.DataFrame
    issues: list[dict] = field(default_factory=list)

    def count(self, code: str) -> int:
        return sum(1 for i in self.issues if i["check_code"] == code)


def _issue(table: str, key: str, code: str, row: pd.Series | None = None, **details) -> dict:
    store_id = product_id = None
    if row is not None:
        store_id = _int_or_none(row.get("store_id"))
        product_id = _int_or_none(row.get("product_id"))
    return {
        "source_table": table,
        "source_key": key,
        "check_code": code,
        "severity": SEVERITY[code],
        "store_id": store_id,
        "product_id": product_id,
        "details": {k: _jsonable(v) for k, v in details.items()},
    }


def _int_or_none(v) -> int | None:
    return None if v is None or pd.isna(v) else int(v)


def _jsonable(v):
    if v is None or (not isinstance(v, str | bool) and pd.isna(v)):
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if hasattr(v, "item"):
        return v.item()
    return v


def _reject(
    df: pd.DataFrame, mask: pd.Series, table: str, code: str, key_fn, detail_cols: Iterable[str]
):
    issues = [
        _issue(table, key_fn(row), code, row, **{c: row[c] for c in detail_cols})
        for _, row in df[mask].iterrows()
    ]
    return df[~mask], issues


def check_sale_lines(
    raw: pd.DataFrame, store_ids: set[int], product_ids: set[int], promo_ids: set[int]
) -> CheckResult:
    t = "sale_line"

    def key(r):
        return f"sale_line_id={_int_or_none(r['sale_line_id'])}"

    issues: list[dict] = []
    df = raw.copy()
    content_cols = [c for c in df.columns if c != "batch_id"]

    dup = df.duplicated(subset=content_cols, keep="first") | df.duplicated(
        subset=["sale_line_id"], keep="first"
    )
    df, found = _reject(df, dup, t, "DUPLICATE", key, ["order_id", "sale_date"])
    issues += found

    required = [
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
    ]
    missing = df[required].isna().any(axis=1)
    df, found = _reject(df, missing, t, "MISSING_VALUE", key, [])
    issues += found

    fk = ~df["store_id"].isin(store_ids) | ~df["product_id"].isin(product_ids)
    fk |= df["promo_id"].notna() & ~df["promo_id"].isin(promo_ids)
    df, found = _reject(df, fk, t, "FK_VIOLATION", key, ["store_id", "product_id"])
    issues += found

    bad = (df["quantity"] <= 0) | (df["unit_price"] < 0) | (df["discount_amount"] < 0)
    bad |= ~df["operation_type"].isin(["sale", "return"])
    df, found = _reject(
        df, bad, t, "OUT_OF_RANGE", key, ["quantity", "unit_price", "discount_amount"]
    )
    issues += found

    over = df["discount_amount"] > (df["quantity"] * df["unit_price"]).round(2)
    df, found = _reject(
        df, over, t, "DISCOUNT_OUT_OF_RANGE", key, ["quantity", "unit_price", "discount_amount"]
    )
    issues += found
    return CheckResult(df.reset_index(drop=True), issues)


def check_costs(raw: pd.DataFrame, product_ids: set[int]) -> CheckResult:
    t = "product_cost"

    def key(r):
        return f"product_id={_int_or_none(r['product_id'])};valid_from={_jsonable(r['valid_from'])}"

    issues: list[dict] = []
    df = raw.copy()
    df, found = _reject(
        df, df.duplicated(["product_id", "valid_from"]), t, "DUPLICATE", key, ["unit_cost"]
    )
    issues += found
    df, found = _reject(
        df,
        df[["product_id", "valid_from", "unit_cost"]].isna().any(axis=1),
        t,
        "MISSING_VALUE",
        key,
        [],
    )
    issues += found
    df, found = _reject(df, ~df["product_id"].isin(product_ids), t, "FK_VIOLATION", key, [])
    issues += found
    df, found = _reject(
        df, df["unit_cost"] <= 0, t, "NON_POSITIVE_COST", key, ["unit_cost", "valid_from"]
    )
    issues += found
    return CheckResult(df.reset_index(drop=True), issues)


def check_prices(raw: pd.DataFrame, store_ids: set[int], product_ids: set[int]) -> CheckResult:
    t = "store_price"

    def key(r):
        return (
            f"store_id={_int_or_none(r['store_id'])};product_id={_int_or_none(r['product_id'])};"
            f"valid_from={_jsonable(r['valid_from'])}"
        )

    issues: list[dict] = []
    df = raw.copy()
    df, found = _reject(
        df, df.duplicated(["store_id", "product_id", "valid_from"]), t, "DUPLICATE", key, ["price"]
    )
    issues += found
    df, found = _reject(
        df,
        df[["store_id", "product_id", "valid_from", "price"]].isna().any(axis=1),
        t,
        "MISSING_VALUE",
        key,
        [],
    )
    issues += found
    fk = ~df["store_id"].isin(store_ids) | ~df["product_id"].isin(product_ids)
    df, found = _reject(df, fk, t, "FK_VIOLATION", key, [])
    issues += found
    df, found = _reject(df, df["price"] <= 0, t, "NON_POSITIVE_PRICE", key, ["price"])
    issues += found
    return CheckResult(df.reset_index(drop=True), issues)


def check_missing_prices(
    prices: pd.DataFrame, active_stores: Iterable[int], active_products: Iterable[int], as_of: date
) -> list[dict]:
    """Полнота прайса: у каждой активной пары «точка × товар» должна быть цена на дату."""
    have = set(
        map(
            tuple,
            prices.loc[prices["valid_from"] <= as_of, ["store_id", "product_id"]]
            .drop_duplicates()
            .astype(int)
            .to_numpy()
            .tolist(),
        )
    )
    return [
        _issue(
            "store_price",
            f"store_id={s};product_id={p}",
            "MISSING_PRICE",
            pd.Series({"store_id": s, "product_id": p}),
            as_of=as_of,
        )
        for s in sorted(active_stores)
        for p in sorted(active_products)
        if (s, p) not in have
    ]
