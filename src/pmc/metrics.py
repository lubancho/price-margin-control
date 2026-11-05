"""Финансовые метрики М-01…М-07 (документ 04, раздел 2).

Правила для всех функций:
- деньги — Decimal;
- если знаменатель 0, отрицательный или неизвестен — результат None («н/д»), а не 0;
- маржинальность группы считается как отношение сумм, а не как среднее процентов.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd

from pmc.pricing import margin

ZERO = Decimal(0)
CENT = Decimal("0.01")


def D(x) -> Decimal:
    if x is None or (not isinstance(x, Decimal) and pd.isna(x)):
        return ZERO
    return x if isinstance(x, Decimal) else Decimal(str(x))


def safe_ratio(num: Decimal | None, den: Decimal | None) -> Decimal | None:
    """num / den; None, если что-то неизвестно или знаменатель ≤ 0."""
    if num is None or den is None or den <= 0:
        return None
    return num / den


@dataclass(frozen=True)
class SalesMetrics:
    revenue: Decimal  # М-01
    revenue_costed: Decimal
    cogs: Decimal
    gross_profit: Decimal  # М-02 (по строкам с известной себестоимостью)
    gross_margin: Decimal | None  # М-03, доля
    net_qty: Decimal
    avg_price: Decimal | None  # М-05
    lines_no_cost: int
    cost_coverage: Decimal | None  # доля выручки, по которой посчитана валовая прибыль
    returns_exceed_sales: bool


def from_sums(revenue, revenue_costed, cogs, net_qty, lines_no_cost=0) -> SalesMetrics:
    revenue, revenue_costed, cogs, net_qty = D(revenue), D(revenue_costed), D(cogs), D(net_qty)
    gp = revenue_costed - cogs
    return SalesMetrics(
        revenue=revenue,
        revenue_costed=revenue_costed,
        cogs=cogs,
        gross_profit=gp,
        gross_margin=safe_ratio(gp, revenue_costed),
        net_qty=net_qty,
        avg_price=safe_ratio(revenue, net_qty),
        lines_no_cost=int(lines_no_cost or 0),
        cost_coverage=safe_ratio(revenue_costed, revenue),
        returns_exceed_sales=revenue < 0,
    )


def enrich(agg: pd.DataFrame) -> pd.DataFrame:
    """Добавляет к результату repository.sales_aggregates расчётные метрики по каждой строке."""
    out = agg.copy()
    if out.empty:
        for c in ("gross_profit", "gross_margin", "avg_price"):
            out[c] = pd.Series(dtype=object)
        return out
    rows = [
        from_sums(r.revenue, r.revenue_costed, r.cogs, r.net_qty, r.lines_no_cost)
        for r in out.itertuples(index=False)
    ]
    out["gross_profit"] = [m.gross_profit for m in rows]
    out["gross_margin"] = [m.gross_margin for m in rows]
    out["avg_price"] = [m.avg_price for m in rows]
    return out


def markup_pct(price: Decimal | None, cost: Decimal | None) -> Decimal | None:
    """М-04, доля (для отображения ×100)."""
    if price is None or cost is None or cost <= 0:
        return None
    return (price - cost) / cost


def offer_margin(price: Decimal | None, cost: Decimal | None) -> Decimal | None:
    """М-07: плановая маржа предложения (доля)."""
    if cost is None or cost <= 0:
        return None
    return margin(price, cost)


@dataclass(frozen=True)
class BelowMinShare:
    below: int
    valid: int
    excluded: int
    share: Decimal | None  # М-06


def below_min_share(offers: pd.DataFrame) -> BelowMinShare:
    """М-06. Ожидаемые колонки: is_active, unit_cost, current_price, min_margin, cost_invalid."""
    active = offers[offers["is_active"]]
    valid_mask = (
        active.apply(
            lambda r: (
                (not r.get("cost_invalid", False))
                and r["unit_cost"] is not None
                and not pd.isna(r["unit_cost"])
                and D(r["unit_cost"]) > 0
                and r["current_price"] is not None
                and not pd.isna(r["current_price"])
                and D(r["current_price"]) > 0
                and r["min_margin"] is not None
                and not pd.isna(r["min_margin"])
            ),
            axis=1,
        )
        if len(active)
        else pd.Series(dtype=bool)
    )
    valid = active[valid_mask] if len(active) else active
    below = sum(
        1
        for r in valid.itertuples(index=False)
        if margin(D(r.current_price), D(r.unit_cost)) < D(r.min_margin)
    )
    n_valid = len(valid)
    return BelowMinShare(
        below, n_valid, len(active) - n_valid, safe_ratio(Decimal(below), Decimal(n_valid))
    )


# --- Независимый расчёт в Python для сверки с SQL (TC-15) -------------------------------------


def python_sales_totals(lines: pd.DataFrame, costs: pd.DataFrame) -> SalesMetrics:
    """Считает М-01…М-05 из сырых строк продаж и версий себестоимости без SQL-представлений.

    Нужна только для проверки: если SQL и этот код дают разные суммы — где-то ошибка.
    """
    if lines.empty:
        return from_sums(ZERO, ZERO, ZERO, ZERO)
    df = lines.copy()
    sign = df.apply(
        lambda r: 0 if r["is_cancelled"] else (-1 if r["operation_type"] == "return" else 1), axis=1
    )
    df["net_qty"] = [Decimal(s) * D(q) for s, q in zip(sign, df["quantity"], strict=True)]
    df["net_revenue"] = [
        (Decimal(s) * (D(q) * D(p) - D(d))).quantize(CENT, ROUND_HALF_UP)
        for s, q, p, d in zip(
            sign, df["quantity"], df["unit_price"], df["discount_amount"], strict=True
        )
    ]
    c = costs.sort_values("valid_from")
    df = df.sort_values("sale_date")
    df["sale_date_ts"] = pd.to_datetime(df["sale_date"])
    c = c.assign(valid_from_ts=pd.to_datetime(c["valid_from"]))
    merged = pd.merge_asof(
        df,
        c[["product_id", "valid_from_ts", "unit_cost"]],
        left_on="sale_date_ts",
        right_on="valid_from_ts",
        by="product_id",
        direction="backward",
    )
    has_cost = merged["unit_cost"].notna()
    revenue = sum(merged["net_revenue"], ZERO)
    revenue_costed = sum(merged.loc[has_cost, "net_revenue"], ZERO)
    cogs = sum(
        (
            q * D(u)
            for q, u in zip(
                merged.loc[has_cost, "net_qty"], merged.loc[has_cost, "unit_cost"], strict=True
            )
        ),
        ZERO,
    )
    net_qty = sum(merged["net_qty"], ZERO)
    no_cost = int((~has_cost & ~merged["is_cancelled"]).sum())
    return from_sums(revenue, revenue_costed, cogs, net_qty, no_cost)
