"""Заведомые ошибки в демо-данных (ТЗ §10) и их манифест.

Простыми словами: мы специально портим часть данных и записываем, что именно испортили.
Тест потом проверяет, что система нашла каждую ошибку из манифеста (AC-02, TC-23).

Ошибки вносятся только в «сырые» таблицы: продажи, цены, себестоимость.
"""

from __future__ import annotations

import math
from datetime import timedelta

import numpy as np
import pandas as pd

from pmc.generator.facts import DemoData

UNKNOWN_PRODUCT_ID = 999


def _pick_offers(rng, n_stores, products, k, used_pairs):
    picks = []
    while len(picks) < k:
        s = int(rng.integers(1, n_stores + 1))
        p = int(rng.choice(products))
        if (s, p) not in used_pairs:
            used_pairs.add((s, p))
            picks.append((s, p))
    return picks


def inject_errors(data: DemoData) -> None:
    """Вносит ошибки E-01…E-09 в data на месте и заполняет data.manifest."""
    rng = np.random.default_rng(data.seed + 1)  # отдельный поток, чтобы не влиять на сами данные
    end = data.end
    manifest: list[dict] = []
    promo_products = set(data.promo_item["product_id"].tolist())
    candidates = [p for p in data.products["product_id"].tolist() if p not in promo_products]
    chosen = rng.choice(candidates, size=9 + 20, replace=False).tolist()
    zero_cost, neg_cost, jump_cost = chosen[:3], chosen[3:5], chosen[5:9]
    offer_products = chosen[9:]
    n_stores = len(data.stores)

    cost = data.product_cost
    latest_cost = cost.sort_values("valid_from").groupby("product_id").last()

    def add_cost(pid: int, valid_from, unit_cost: float) -> None:
        version = int(cost.loc[cost["product_id"] == pid, "calc_version"].max()) + 1
        cost.loc[len(cost)] = [pid, valid_from, unit_cost, "техкарта", version]

    # E-02, E-03: нулевая и отрицательная себестоимость в последней версии.
    for pid in zero_cost:
        add_cost(pid, end - timedelta(days=5), 0.0)
        manifest.append(_m("E-02", "product_cost", "NON_POSITIVE_COST", product_id=pid))
    for pid in neg_cost:
        add_cost(pid, end - timedelta(days=4), -12.5)
        manifest.append(_m("E-03", "product_cost", "NON_POSITIVE_COST", product_id=pid))
    # E-06: скачок себестоимости более 30%.
    for pid in jump_cost:
        new = round(float(latest_cost.loc[pid, "unit_cost"]) * 1.35, 2)
        add_cost(pid, end - timedelta(days=12), new)
        manifest.append(_m("E-06", "product_cost", "COST_JUMP", product_id=pid, kind="alert"))
    data.product_cost = cost.sort_values(["product_id", "valid_from"]).reset_index(drop=True)

    price = data.store_price
    used: set[tuple[int, int]] = set()
    # E-04: у предложения нет ни одной цены.
    for s, p in _pick_offers(rng, n_stores, offer_products, 5, used):
        price = price[~((price["store_id"] == s) & (price["product_id"] == p))]
        manifest.append(_m("E-04", "store_price", "MISSING_PRICE", store_id=s, product_id=p))
    # E-05: цена ниже себестоимости.
    cost_end = data.product_cost.sort_values("valid_from").groupby("product_id").last()["unit_cost"]
    new_rows = []
    for s, p in _pick_offers(rng, n_stores, offer_products, 6, used):
        c = float(cost_end.loc[p])
        new_rows.append((s, p, end - timedelta(days=10), math.floor(c * 0.9 / 10) * 10))
        manifest.append(
            _m("E-05", "store_price", "PRICE_BELOW_COST", store_id=s, product_id=p, kind="alert")
        )
    # E-09: цена в точке заметно выше, чем в остальной сети.
    for s, p in _pick_offers(rng, n_stores, offer_products, 5, used):
        cur = price[(price["store_id"] == s) & (price["product_id"] == p)].sort_values("valid_from")
        base = float(cur["price"].iloc[-1])
        new_rows.append((s, p, end - timedelta(days=15), math.ceil(base * 1.4 / 10) * 10))
        manifest.append(
            _m("E-09", "store_price", "PRICE_DEVIATION", store_id=s, product_id=p, kind="alert")
        )
    for s, p, vf, _price in new_rows:
        # Новая цена должна быть последней для предложения.
        price = price[
            ~((price["store_id"] == s) & (price["product_id"] == p) & (price["valid_from"] >= vf))
        ]
    price = pd.concat(
        [price, pd.DataFrame(new_rows, columns=["store_id", "product_id", "valid_from", "price"])],
        ignore_index=True,
    )
    data.store_price = price.sort_values(["store_id", "product_id", "valid_from"]).reset_index(
        drop=True
    )

    sales = data.sale_line
    normal = sales.index[(sales["operation_type"] == "sale") & ~sales["is_cancelled"]]
    picked = rng.choice(normal, size=50 + 10 + 8, replace=False)
    dup_idx, unk_idx, disc_idx = picked[:50], picked[50:60], picked[60:]
    # E-08: скидка больше суммы строки.
    sales.loc[disc_idx, "discount_amount"] = (
        sales.loc[disc_idx, "quantity"] * sales.loc[disc_idx, "unit_price"] * 1.5
    ).round(2)
    for i in disc_idx:
        manifest.append(
            _m(
                "E-08",
                "sale_line",
                "DISCOUNT_OUT_OF_RANGE",
                sale_line_id=int(sales.at[i, "sale_line_id"]),
            )
        )
    # E-07: продажа несуществующего товара (новые строки).
    unknown = sales.loc[unk_idx].copy()
    unknown["product_id"] = UNKNOWN_PRODUCT_ID
    unknown["sale_line_id"] = np.arange(len(sales) + 1, len(sales) + 1 + len(unknown))
    for sid in unknown["sale_line_id"]:
        manifest.append(_m("E-07", "sale_line", "FK_VIOLATION", sale_line_id=int(sid)))
    # E-01: полные дубли строк (тот же идентификатор и содержимое).
    dups = sales.loc[dup_idx].copy()
    for sid in dups["sale_line_id"]:
        manifest.append(_m("E-01", "sale_line", "DUPLICATE", sale_line_id=int(sid)))
    data.sale_line = pd.concat([sales, unknown, dups], ignore_index=True)
    data.manifest = manifest


def _m(code: str, table: str, check: str, kind: str = "dq", **keys) -> dict:
    return {"error_code": code, "table": table, "detect_as": check, "detect_kind": kind, **keys}
