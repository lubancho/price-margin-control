"""Генератор синтетических фактов: цены, себестоимость, промо, продажи, остатки, списания.

ВАЖНО: файл одинаковый в обоих проектах. Все закономерности придуманы для демонстрации
и не описывают реальную компанию.

Что учтено (ТЗ §10):
- разная популярность товаров (логнормальные веса) и разные предпочтения точек;
- день недели (пятница–суббота выше), плавный сезонный тренд, сезонность напитков;
- разная проходимость точек и санитарные дни;
- промоакции со скидкой и ростом спроса;
- дни, когда товара не было в продаже (такие дни отмечены, продаж в них нет);
- возвраты и отмены.

Детерминированность: один numpy.random.Generator с фиксированным seed,
порядок вызовов фиксирован, поэтому одинаковый seed даёт одинаковые данные.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from pmc.generator.reference import CATEGORIES, PRODUCTS, STORES

OMSK_TZ = timezone(timedelta(hours=6))
DEFAULT_START = date(2026, 8, 3)
DEFAULT_DAYS = 90

# Доля категории в строках чеков (до сезонных поправок).
_CATEGORY_SHARE = {1: 0.42, 2: 0.18, 3: 0.12, 4: 0.14, 5: 0.14}
# Понедельник … воскресенье.
_DOW = np.array([0.85, 0.85, 0.90, 0.95, 1.20, 1.35, 1.15])
_HOURS = np.arange(10, 23)
_HOUR_W = np.array([2, 4, 9, 8, 5, 4, 4, 6, 9, 10, 8, 5, 2], dtype=float)
_BASE_ORDERS = 105.0


@dataclass
class DemoData:
    start: date
    days: int
    seed: int
    stores: pd.DataFrame
    categories: pd.DataFrame
    products: pd.DataFrame
    product_cost: pd.DataFrame
    store_price: pd.DataFrame
    promo: pd.DataFrame
    promo_item: pd.DataFrame
    store_calendar: pd.DataFrame
    sale_line: pd.DataFrame
    stock_daily: pd.DataFrame
    write_off: pd.DataFrame
    manifest: list[dict] = field(default_factory=list)

    @property
    def end(self) -> date:
        return self.start + timedelta(days=self.days - 1)


def _ceil_to(x: np.ndarray | float, step: float) -> np.ndarray:
    return np.ceil(np.asarray(x) / step - 1e-9) * step


def _reference_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    stores = pd.DataFrame(
        [
            {
                "store_id": s.store_id,
                "code": s.code,
                "name": s.name,
                "region": s.region,
                "city": s.city,
                "is_active": True,
                "opened_on": s.opened_on,
            }
            for s in STORES
        ]
    )
    categories = pd.DataFrame(
        [{"category_id": c.category_id, "name": c.name, "parent_id": None} for c in CATEGORIES]
    )
    products = pd.DataFrame(
        [
            {
                "product_id": p.product_id,
                "sku": p.sku,
                "name": p.name,
                "category_id": p.category_id,
                "sale_unit": p.sale_unit,
                "is_fresh": p.is_fresh,
                "is_active": True,
            }
            for p in PRODUCTS
        ]
    )
    return stores, categories, products


def generate(seed: int, start: date = DEFAULT_START, days: int = DEFAULT_DAYS) -> DemoData:
    rng = np.random.default_rng(seed)
    stores, categories, products = _reference_frames()
    n_s, n_p = len(stores), len(products)
    dates = [start + timedelta(days=i) for i in range(days)]
    cat_of = products["category_id"].to_numpy()
    fresh = products["is_fresh"].to_numpy()
    traffic = np.array([s.traffic for s in STORES])

    # --- Себестоимость: базовая + 3 версии с дрейфом -------------------------------------
    cat_range = {c.category_id: c.cost_range for c in CATEGORIES}
    lo = np.array([cat_range[c][0] for c in cat_of])
    hi = np.array([cat_range[c][1] for c in cat_of])
    cost0 = np.round(rng.uniform(lo, hi), 2)
    cost_rows = []
    cost_now = np.zeros((n_p, days))
    for p in range(n_p):
        pid = p + 1
        c = cost0[p]
        first = start - timedelta(days=30)
        cost_rows.append((pid, first, round(float(c), 2), "техкарта", 1))
        change_days = np.sort(rng.choice(np.arange(15, days - 3), size=3, replace=False))
        cur = np.full(days, c)
        for v, d in enumerate(change_days, start=2):
            drift = rng.uniform(0.0, 0.06) if fresh[p] else rng.uniform(-0.03, 0.04)
            c = round(float(c * (1 + drift)), 2)
            cost_rows.append((pid, start + timedelta(days=int(d)), c, "техкарта", v))
            cur[d:] = c
        cost_now[p] = cur
    product_cost = pd.DataFrame(
        cost_rows, columns=["product_id", "valid_from", "unit_cost", "source", "calc_version"]
    )

    # --- Цены: сетевая цена × индекс точки, затем пересмотры --------------------------------
    margin0 = rng.uniform(0.36, 0.58, n_p)
    net_price0 = _ceil_to(cost0 / (1 - margin0), 10)
    store_index = np.where(np.isin(np.arange(1, n_s + 1), [3, 4]), 1.05, 1.0)
    store_index = np.where(np.isin(np.arange(1, n_s + 1), [14, 15]), 0.97, store_index)
    price = np.zeros((n_s, n_p, days))
    price_rows = []
    price_start = start - timedelta(days=30)
    for s in range(n_s):
        for p in range(n_p):
            v = float(_ceil_to(net_price0[p] * store_index[s], 10))
            price[s, p, :] = v
            price_rows.append((s + 1, p + 1, price_start, v))
    # Сетевые пересмотры цен для 35 товаров.
    for p in rng.choice(n_p, size=35, replace=False):
        d = int(rng.integers(25, days - 10))
        factor = rng.uniform(1.03, 1.10)
        for s in range(n_s):
            v = float(_ceil_to(price[s, p, d - 1] * factor, 10))
            price[s, p, d:] = v
            price_rows.append((s + 1, int(p) + 1, start + timedelta(days=d), v))
    # Локальные изменения цен в отдельных точках.
    for _ in range(75):
        s, p = int(rng.integers(n_s)), int(rng.integers(n_p))
        d = int(rng.integers(5, days - 5))
        v = float(_ceil_to(price[s, p, d - 1] * rng.uniform(0.95, 1.08), 10))
        if v == price[s, p, d - 1] or any(
            r[:3] == (s + 1, p + 1, start + timedelta(days=d)) for r in price_rows
        ):
            continue
        price[s, p, d:] = v
        price_rows.append((s + 1, p + 1, start + timedelta(days=d), v))
    store_price = (
        pd.DataFrame(price_rows, columns=["store_id", "product_id", "valid_from", "price"])
        .sort_values(["store_id", "product_id", "valid_from"])
        .reset_index(drop=True)
    )
    # Итоговый массив цен строим из строк истории, чтобы продажи и прайс совпадали точно.
    for (s1, p1), grp in store_price.groupby(["store_id", "product_id"], sort=False):
        for vf, v in zip(grp["valid_from"], grp["price"], strict=True):
            d0 = max(0, (vf - start).days)
            price[s1 - 1, p1 - 1, d0:] = v

    # --- Промоакции ------------------------------------------------------------------------
    by_cat = {c.category_id: products.index[cat_of == c.category_id].to_numpy() for c in CATEGORIES}
    low_margin = {c: idx[margin0[idx] < 0.42] for c, idx in by_cat.items()}
    promo_defs = [
        (1, "Ролл недели", 9, 15, 0.20, list(rng.choice(by_cat[1], 3, replace=False)), None),
        (
            2,
            "Наборы для компании",
            30,
            43,
            0.15,
            list(rng.choice(by_cat[3], 2, replace=False)),
            None,
        ),
        (
            3,
            "Летний лимонад",
            3,
            20,
            0.25,
            list(rng.choice(by_cat[4], 2, replace=False)),
            [1, 2, 3, 4, 5],
        ),
        (4, "Осенние роллы", 66, 73, 0.20, list(rng.choice(by_cat[1], 4, replace=False)), None),
        (
            # Акция, действующая на дату расчёта: товары с невысокой маржой, чтобы было видно
            # правило «пересмотр цены откладывается до окончания акции».
            5,
            "Ноябрьская подборка",
            days - 6,
            days + 7,
            0.15,
            [int(rng.choice(low_margin[c])) for c in (1, 2, 5)],
            None,
        ),
    ]
    promo_rows, promo_item_rows = [], []
    promo_pct = np.zeros((n_s, n_p, days))
    promo_id_arr = np.zeros((n_s, n_p, days), dtype=int)
    for pid_, name, d0, d1, pct, items, st_list in promo_defs:
        promo_rows.append((pid_, name, start + timedelta(days=d0), start + timedelta(days=d1), pct))
        st_idx = [s - 1 for s in st_list] if st_list else list(range(n_s))
        for p in items:
            if st_list:
                for s in st_list:
                    promo_item_rows.append((pid_, int(p) + 1, s))
            else:
                promo_item_rows.append((pid_, int(p) + 1, None))
            for s in st_idx:
                promo_pct[s, p, d0 : min(d1, days - 1) + 1] = pct
                promo_id_arr[s, p, d0 : min(d1, days - 1) + 1] = pid_
    promo = pd.DataFrame(
        promo_rows, columns=["promo_id", "name", "date_from", "date_to", "discount_pct"]
    )
    promo_item = pd.DataFrame(promo_item_rows, columns=["promo_id", "product_id", "store_id"])
    promo_item["store_id"] = promo_item["store_id"].astype("Int64")

    # --- Календарь и доступность -----------------------------------------------------------
    is_open = np.ones((n_s, days), dtype=bool)
    cal_rows = []
    for s in range(n_s):
        closed_days = set(
            rng.choice(np.arange(days), size=1 + int(s >= 13), replace=False).tolist()
        )
        for d in range(days):
            if d in closed_days:
                is_open[s, d] = False
            cal_rows.append(
                (
                    s + 1,
                    dates[d],
                    d not in closed_days,
                    "санитарный день" if d in closed_days else None,
                )
            )
    store_calendar = pd.DataFrame(
        cal_rows, columns=["store_id", "calendar_date", "is_open", "note"]
    )
    unavailable = rng.random((n_s, n_p, days)) < 0.02  # товара не было в продаже

    # --- Продажи ---------------------------------------------------------------------------
    popularity = rng.lognormal(0.0, 0.9, n_p)
    store_pref = rng.lognormal(0.0, 0.25, (n_s, n_p))
    cat_weight = np.array([_CATEGORY_SHARE[c] for c in cat_of])
    cat_sum = {c: popularity[cat_of == c].sum() for c in _CATEGORY_SHARE}
    base_w = cat_weight * popularity / np.array([cat_sum[c] for c in cat_of])
    trend = np.linspace(1.04, 0.96, days)
    drinks_season = np.linspace(1.35, 0.75, days)
    hour_p = _HOUR_W / _HOUR_W.sum()

    cols: dict[str, list] = {
        k: []
        for k in (
            "order_id",
            "sold_at",
            "sale_date",
            "store_id",
            "product_id",
            "quantity",
            "unit_price",
            "discount_amount",
            "promo_id",
        )
    }
    order_seq = 1
    for d in range(days):
        dow = _DOW[dates[d].weekday()]
        season_w = np.where(cat_of == 4, drinks_season[d], 1.0)
        for s in range(n_s):
            if not is_open[s, d]:
                continue
            n_orders = rng.poisson(_BASE_ORDERS * traffic[s] * dow * trend[d])
            if n_orders == 0:
                continue
            lines_per = 1 + rng.poisson(1.3, n_orders)
            n_lines = int(lines_per.sum())
            w = base_w * store_pref[s] * season_w * np.where(promo_pct[s, :, d] > 0, 1.6, 1.0)
            w = np.where(unavailable[s, :, d], 0.0, w)
            prod_idx = rng.choice(n_p, size=n_lines, p=w / w.sum())
            qty = rng.choice([1, 2, 3], size=n_lines, p=[0.85, 0.12, 0.03])
            hours = rng.choice(_HOURS, size=n_orders, p=hour_p)
            minutes = rng.integers(0, 60, n_orders)
            order_ids = np.arange(order_seq, order_seq + n_orders)
            order_seq += n_orders
            line_order = np.repeat(np.arange(n_orders), lines_per)
            base_dt = datetime(dates[d].year, dates[d].month, dates[d].day, tzinfo=OMSK_TZ)
            order_dt = [
                base_dt + timedelta(hours=int(h), minutes=int(m))
                for h, m in zip(hours, minutes, strict=True)
            ]
            unit_price = price[s, prod_idx, d]
            pct = promo_pct[s, prod_idx, d]
            cols["order_id"].append(order_ids[line_order])
            cols["sold_at"].extend(order_dt[i] for i in line_order)
            cols["sale_date"].extend([dates[d]] * n_lines)
            cols["store_id"].append(np.full(n_lines, s + 1))
            cols["product_id"].append(prod_idx + 1)
            cols["quantity"].append(qty)
            cols["unit_price"].append(unit_price)
            cols["discount_amount"].append(np.round(qty * unit_price * pct, 2))
            cols["promo_id"].append(promo_id_arr[s, prod_idx, d])

    sales = pd.DataFrame(
        {
            "order_id": np.concatenate(cols["order_id"]),
            "sold_at": cols["sold_at"],
            "sale_date": cols["sale_date"],
            "store_id": np.concatenate(cols["store_id"]),
            "product_id": np.concatenate(cols["product_id"]),
            "quantity": np.concatenate(cols["quantity"]).astype(float),
            "unit_price": np.concatenate(cols["unit_price"]),
            "discount_amount": np.concatenate(cols["discount_amount"]),
            "promo_id": np.concatenate(cols["promo_id"]),
        }
    )
    sales["promo_id"] = sales["promo_id"].where(sales["promo_id"] > 0).astype("Int64")
    sales["operation_type"] = "sale"
    # Отмена: 1% чеков отменяется целиком.
    cancelled_orders = (
        sales["order_id"].drop_duplicates().sample(frac=0.01, random_state=rng.integers(2**31))
    )
    sales["is_cancelled"] = sales["order_id"].isin(cancelled_orders)
    # Возврат: 0,4% неотменённых строк возвращают в тот же день.
    candidates = sales.index[~sales["is_cancelled"]]
    ret_idx = rng.choice(candidates, size=int(len(candidates) * 0.004), replace=False)
    returns = sales.loc[ret_idx].copy()
    returns["operation_type"] = "return"
    sales["sold_at"] = pd.to_datetime(sales["sold_at"])
    returns["sold_at"] = pd.to_datetime(returns["sold_at"])
    delay = pd.to_timedelta(rng.integers(20, 120, len(returns)), unit="m")
    end_of_day = returns["sold_at"].dt.normalize() + pd.to_timedelta(23 * 60 + 59, unit="m")
    moved = returns["sold_at"] + delay
    returns["sold_at"] = moved.where(moved <= end_of_day, end_of_day)
    sale_line = pd.concat([sales, returns], ignore_index=True)
    sale_line = sale_line.sort_values(
        ["sold_at", "order_id", "operation_type"], kind="stable"
    ).reset_index(drop=True)
    sale_line.insert(0, "sale_line_id", np.arange(1, len(sale_line) + 1))

    stock_daily, write_off = _stock_and_write_offs(
        rng, sale_line, unavailable, is_open, fresh, dates, n_s, n_p
    )

    return DemoData(
        start=start,
        days=days,
        seed=seed,
        stores=stores,
        categories=categories,
        products=products,
        product_cost=product_cost,
        store_price=store_price,
        promo=promo,
        promo_item=promo_item,
        store_calendar=store_calendar,
        sale_line=sale_line,
        stock_daily=stock_daily,
        write_off=write_off,
    )


def _stock_and_write_offs(
    rng: np.random.Generator,
    sale_line: pd.DataFrame,
    unavailable: np.ndarray,
    is_open: np.ndarray,
    fresh: np.ndarray,
    dates: list[date],
    n_s: int,
    n_p: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Остатки и списания.

    Свежая продукция (роллы, суши, наборы) живёт один день: остаток на конец дня списывается
    с причиной «истёк срок». Напитки и закуски хранятся, пополняются раз в несколько дней.
    Возвращённый товар возвращается на полку и сразу списывается с причиной «возврат».
    """
    days = len(dates)
    day_index = {d: i for i, d in enumerate(dates)}
    live = sale_line[~sale_line["is_cancelled"]]
    s_idx = live["store_id"].to_numpy() - 1
    p_idx = live["product_id"].to_numpy() - 1
    d_idx = live["sale_date"].map(day_index).to_numpy()
    qty = live["quantity"].to_numpy()
    is_ret = (live["operation_type"] == "return").to_numpy()
    gross = np.zeros((n_s, n_p, days))
    ret = np.zeros((n_s, n_p, days))
    np.add.at(gross, (s_idx[~is_ret], p_idx[~is_ret], d_idx[~is_ret]), qty[~is_ret])
    np.add.at(ret, (s_idx[is_ret], p_idx[is_ret], d_idx[is_ret]), qty[is_ret])

    opening = np.zeros_like(gross)
    produced = np.zeros_like(gross)
    expired = np.zeros_like(gross)
    damaged = np.zeros_like(gross)

    # Свежая продукция живёт один день: всё, что не продано, списывается.
    f = fresh.astype(bool)
    # Выпуск = продажи + небольшой случайный запас (в среднем 10% и ещё ~0,15 шт.); иногда «впритык».
    extra = rng.poisson(gross[:, f, :] * 0.10 + 0.15)
    sold_out_day = rng.random((n_s, int(f.sum()), days)) < 0.10
    fresh_prod = np.where(sold_out_day, gross[:, f, :], gross[:, f, :] + extra)
    # Немного производим и в дни без продаж (кроме дней недоступности и закрытия).
    idle = (gross[:, f, :] == 0) & ~unavailable[:, f, :] & is_open[:, None, :]
    fresh_prod = np.where(idle & (rng.random(fresh_prod.shape) < 0.25), 1, fresh_prod)
    fresh_prod = np.where(unavailable[:, f, :] | ~is_open[:, None, :], 0, fresh_prod)
    leftover = fresh_prod - gross[:, f, :]
    dmg = np.minimum(leftover, rng.binomial(fresh_prod.astype(int), 0.004))
    produced[:, f, :] = fresh_prod
    damaged[:, f, :] = dmg
    expired[:, f, :] = leftover - dmg

    # Хранимая продукция: пополнение до целевого уровня раз в 3 дня, иногда истекает срок.
    nf = np.where(~f)[0]
    avg = gross[:, nf, :].mean(axis=2)
    target = np.ceil(avg * rng.uniform(2.5, 4.0, avg.shape)) + 2
    for si in range(n_s):
        for j, p in enumerate(nf):
            stock = float(target[si, j])
            for d in range(days):
                opening[si, p, d] = stock
                prod = 0.0
                if (
                    is_open[si, d]
                    and not unavailable[si, p, d]
                    and (d % 3 == si % 3 or stock < gross[si, p, d])
                ):
                    prod = max(0.0, float(target[si, j]) - stock)
                if stock + prod < gross[si, p, d]:
                    prod += gross[si, p, d] - stock - prod
                stock = stock + prod - gross[si, p, d]
                exp = float(min(stock, rng.integers(1, 4))) if rng.random() < 0.015 else 0.0
                stock -= exp
                produced[si, p, d] = prod
                expired[si, p, d] = exp

    sold_net = gross - ret
    written = expired + damaged + ret
    closing = opening + produced - sold_net - written

    s_g, p_g, d_g = np.meshgrid(np.arange(n_s), np.arange(n_p), np.arange(days), indexing="ij")
    stock_daily = pd.DataFrame(
        {
            "store_id": (s_g + 1).ravel(),
            "product_id": (p_g + 1).ravel(),
            "stock_date": [dates[i] for i in d_g.ravel()],
            "opening_qty": opening.ravel(),
            "produced_qty": produced.ravel(),
            "sold_qty": sold_net.ravel(),
            "written_off_qty": written.ravel(),
            "closing_qty": np.round(closing.ravel(), 3),
            "is_available": (~unavailable & is_open[:, None, :]).ravel(),
        }
    )

    parts = []
    for reason, arr in (("expired", expired), ("damaged", damaged), ("return", ret)):
        nz = np.nonzero(arr > 0)
        parts.append(
            pd.DataFrame(
                {
                    "store_id": nz[0] + 1,
                    "product_id": nz[1] + 1,
                    "write_off_date": [dates[i] for i in nz[2]],
                    "quantity": arr[nz],
                    "reason": reason,
                }
            )
        )
    write_off = (
        pd.concat(parts, ignore_index=True)
        .sort_values(["write_off_date", "store_id", "product_id", "reason"])
        .reset_index(drop=True)
    )
    write_off.insert(0, "write_off_id", np.arange(1, len(write_off) + 1))
    return stock_daily, write_off
