"""Генератор: воспроизводимость, объёмы, манифест ошибок (TC-22, AC-01)."""

import hashlib

import pytest

from pmc.generator import build_demo, save_csv
from pmc.generator.reference import PRODUCTS, STORES


@pytest.fixture(scope="module")
def demo():
    return build_demo(20261105)


def _digest(folder):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.iterdir())}


def test_same_seed_gives_identical_files(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    save_csv(build_demo(7), a)
    save_csv(build_demo(7), b)
    assert _digest(a) == _digest(b)


def test_different_seed_gives_different_sales():
    assert not build_demo(1).sale_line.equals(build_demo(2).sale_line)


def test_volumes_match_specification(demo):
    assert len(demo.stores) == 15 and len(demo.products) == 150 and len(demo.categories) == 5
    assert demo.days == 90
    assert demo.sale_line["sale_date"].nunique() == 90
    assert len(demo.stock_daily) == 15 * 150 * 90


def test_reference_ids_are_fixed():
    assert [s.store_id for s in STORES] == list(range(1, 16))
    assert PRODUCTS[0].sku == "RL-0001" and PRODUCTS[-1].sku == "SN-0025"


def test_manifest_covers_all_error_types(demo):
    codes = {m["error_code"] for m in demo.manifest}
    assert codes == {f"E-0{i}" for i in range(1, 10)}


def test_stock_balance_holds(demo):
    s = demo.stock_daily
    diff = (s.opening_qty + s.produced_qty - s.sold_qty - s.written_off_qty - s.closing_qty).abs()
    assert diff.max() < 1e-6 and s.closing_qty.min() >= 0


def test_no_sales_on_closed_days_or_unavailable_products(demo):
    closed = demo.store_calendar[~demo.store_calendar["is_open"]]
    merged = demo.sale_line.merge(
        closed, left_on=["store_id", "sale_date"], right_on=["store_id", "calendar_date"]
    )
    assert merged.empty
    unavailable = demo.stock_daily[~demo.stock_daily["is_available"]]
    m2 = demo.sale_line.merge(
        unavailable,
        left_on=["store_id", "product_id", "sale_date"],
        right_on=["store_id", "product_id", "stock_date"],
    )
    assert m2.empty


def test_weekend_demand_is_higher(demo):
    s = demo.sale_line[demo.sale_line["operation_type"] == "sale"]
    by_dow = s.groupby(s["sale_date"].map(lambda d: d.weekday()))["order_id"].nunique()
    assert by_dow[5] > by_dow[0] * 1.2
