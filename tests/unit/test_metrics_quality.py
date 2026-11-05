"""Метрики, проверки качества, сценарии, алерты, экспорт (TC-11, TC-16, TC-22 и др.)."""

from datetime import date
from decimal import Decimal as Dec

import pandas as pd
from openpyxl import load_workbook

from pmc import alerts, metrics, quality, scenarios
from pmc.export import to_csv_bytes, to_xlsx_bytes


def lines(rows):
    cols = [
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
    return pd.DataFrame(rows, columns=cols)


def line(
    i, qty=1, price=100.0, disc=0.0, op="sale", cancelled=False, product=1, day=date(2026, 10, 1)
):
    return (
        i,
        i,
        pd.Timestamp(day, tz="Asia/Omsk"),
        day,
        1,
        product,
        qty,
        price,
        disc,
        op,
        cancelled,
        None,
    )


# --- Метрики -----------------------------------------------------------------------------------


def test_safe_ratio_handles_zero_and_negative_denominator():
    assert metrics.safe_ratio(Dec(5), Dec(0)) is None
    assert metrics.safe_ratio(Dec(5), Dec(-1)) is None
    assert metrics.safe_ratio(None, Dec(1)) is None
    assert metrics.safe_ratio(Dec(1), Dec(4)) == Dec("0.25")


def test_metrics_from_sums_zero_revenue_gives_none_margin():
    m = metrics.from_sums(0, 0, 0, 0)
    assert m.revenue == 0 and m.gross_margin is None and m.avg_price is None


# TC-16
def test_returns_and_cancellations():
    df = lines(
        [
            line(1, qty=2, price=100.0),
            line(2, qty=1, price=100.0, op="return"),
            line(3, qty=5, price=100.0, cancelled=True),
        ]
    )
    costs = pd.DataFrame(
        {"product_id": [1], "valid_from": [date(2026, 1, 1)], "unit_cost": [Dec("60")]}
    )
    m = metrics.python_sales_totals(df, costs)
    assert m.net_qty == Dec(1)
    assert m.revenue == Dec("100.00")
    assert m.cogs == Dec("60")
    assert m.gross_profit == Dec("40.00")
    assert m.gross_margin == Dec("0.4")
    assert m.avg_price == Dec("100")


def test_discount_reduces_revenue_and_cost_as_of_sale_date():
    df = lines(
        [
            line(1, qty=1, price=100.0, disc=20.0, day=date(2026, 9, 1)),
            line(2, qty=1, price=100.0, day=date(2026, 10, 2)),
        ]
    )
    costs = pd.DataFrame(
        {
            "product_id": [1, 1],
            "valid_from": [date(2026, 1, 1), date(2026, 10, 1)],
            "unit_cost": [Dec("50"), Dec("70")],
        }
    )
    m = metrics.python_sales_totals(df, costs)
    assert m.revenue == Dec("180.00")
    assert m.cogs == Dec("120")  # 50 по первой версии + 70 по второй (BR-10)


def test_sales_without_cost_are_excluded_from_gross_profit_only():
    df = lines(
        [line(1, price=100.0, day=date(2025, 12, 1)), line(2, price=100.0, day=date(2026, 2, 1))]
    )
    costs = pd.DataFrame(
        {"product_id": [1], "valid_from": [date(2026, 1, 1)], "unit_cost": [Dec("60")]}
    )
    m = metrics.python_sales_totals(df, costs)
    assert m.revenue == Dec("200.00") and m.revenue_costed == Dec("100.00")
    assert m.lines_no_cost == 1 and m.cost_coverage == Dec("0.5")


def test_below_min_share_excludes_invalid_offers_from_denominator():
    offers = pd.DataFrame(
        {
            "is_active": [True, True, True, True, False],
            "unit_cost": [Dec(80), Dec(50), None, Dec(50), Dec(90)],
            "current_price": [Dec(100), Dec(100), Dec(100), None, Dec(100)],
            "min_margin": [Dec("0.3")] * 5,
            "cost_invalid": [False] * 5,
        }
    )
    r = metrics.below_min_share(offers)
    assert (r.below, r.valid, r.excluded) == (1, 2, 2)
    assert r.share == Dec("0.5")


def test_below_min_share_empty_is_none():
    empty = pd.DataFrame(
        columns=["is_active", "unit_cost", "current_price", "min_margin", "cost_invalid"]
    )
    empty["is_active"] = empty["is_active"].astype(bool)
    assert metrics.below_min_share(empty).share is None


# --- Качество данных ---------------------------------------------------------------------------


# TC-11
def test_duplicates_are_removed_and_logged():
    df = lines([line(1), line(1), line(2)])
    res = quality.check_sale_lines(df, {1}, {1}, set())
    assert len(res.valid) == 2 and res.count("DUPLICATE") == 1


def test_fk_range_and_discount_checks():
    df = lines(
        [
            line(1, product=999),
            line(2, qty=0),
            line(3, price=-1.0),
            line(4, qty=1, price=100.0, disc=150.0),
            line(5, op="refund"),
            line(6),
        ]
    )
    res = quality.check_sale_lines(df, {1}, {1}, set())
    assert res.count("FK_VIOLATION") == 1
    assert res.count("OUT_OF_RANGE") == 3
    assert res.count("DISCOUNT_OUT_OF_RANGE") == 1
    assert res.valid["sale_line_id"].tolist() == [6]


def test_missing_values_are_rejected():
    df = lines([line(1)])
    df.loc[0, "unit_price"] = None
    res = quality.check_sale_lines(df, {1}, {1}, set())
    assert res.count("MISSING_VALUE") == 1 and res.valid.empty


def test_cost_and_price_checks():
    costs = pd.DataFrame(
        {
            "product_id": [1, 2, 3, 3],
            "valid_from": [date(2026, 1, 1)] * 4,
            "unit_cost": [10.0, 0.0, -5.0, 7.0],
            "source": "x",
            "calc_version": 1,
        }
    )
    res = quality.check_costs(costs, {1, 2, 3})
    # товар 3: первая строка — себестоимость −5, вторая — дубль по ключу «товар + дата»
    assert res.count("NON_POSITIVE_COST") == 2 and res.count("DUPLICATE") == 1
    assert res.valid["product_id"].tolist() == [1]
    prices = pd.DataFrame(
        {
            "store_id": [1, 1, 9],
            "product_id": [1, 2, 1],
            "valid_from": [date(2026, 1, 1)] * 3,
            "price": [100.0, 0.0, 100.0],
        }
    )
    res = quality.check_prices(prices, {1}, {1, 2})
    assert res.count("NON_POSITIVE_PRICE") == 1 and res.count("FK_VIOLATION") == 1


def test_missing_price_completeness_check():
    prices = pd.DataFrame(
        {
            "store_id": [1, 1, 2],
            "product_id": [1, 2, 1],
            "valid_from": [date(2026, 1, 1), date(2026, 12, 1), date(2026, 1, 1)],
        }
    )
    issues = quality.check_missing_prices(prices, [1, 2], [1, 2], date(2026, 10, 31))
    assert sorted((i["store_id"], i["product_id"]) for i in issues) == [(1, 2), (2, 2)]


# --- Сценарии ---------------------------------------------------------------------------------


def scenario_rows():
    return pd.DataFrame(
        {
            "unit_cost": [Dec(60), Dec(60), None],
            "current_price": [Dec(100), Dec(100), Dec(100)],
            "recommended_price": [Dec(110), None, Dec(120)],
            "status": ["PROPOSED", "CONFLICT", "PROPOSED"],
            "q0": [Dec(10), Dec(10), Dec(10)],
            "category_name": ["A", "A", "B"],
        }
    )


def test_scenario_fixed_demand():
    sim = scenarios.simulate(scenario_rows(), scenarios.DemandAssumption("fixed"))
    t = scenarios.totals(sim).iloc[0]
    assert t["rev0"] == Dec(2000) and t["rev1"] == Dec(2100)
    assert t["gp0"] == Dec(800) and t["gp1"] == Dec(900)
    assert t["offers"] == 2 and t["changed"] == 1  # строка без себестоимости не входит в сценарий


def test_scenario_demand_coefficient():
    sim = scenarios.simulate(
        scenario_rows(), scenarios.DemandAssumption("coefficient", Dec("-0.5"))
    )
    assert sim.loc[0, "q1"] == Dec("9.5")  # +10% к цене × (−0,5) = −5% к количеству
    assert sim.loc[1, "q1"] == Dec(10)  # конфликт остаётся по текущей цене


def test_scenario_assumptions_always_mention_it_is_not_a_forecast():
    for mode in ("fixed", "coefficient"):
        text = " ".join(scenarios.DemandAssumption(mode).describe())
        assert "не прогноз" in text


# --- Алерты -------------------------------------------------------------------------------------


def offer_frame(**over):
    base = dict(
        store_id=1,
        product_id=1,
        product_name="Ролл",
        store_name="Точка",
        is_active=True,
        unit_cost=Dec(100),
        current_price=Dec(130),
        price_valid_from=date(2026, 9, 1),
        cost_valid_from=date(2026, 9, 1),
        min_margin=Dec("0.3"),
        network_median=Dec(130),
        status="PROPOSED",
        reason_code="X",
        reason_text="x",
        cost_invalid=False,
    )
    base.update(over)
    return pd.DataFrame([base])


def test_alert_rules():
    codes = lambda df: {a["alert_code"] for a in alerts.offer_alerts(df, 1)}  # noqa: E731
    assert codes(offer_frame(current_price=Dec(90))) >= {"PRICE_BELOW_COST"}
    assert codes(offer_frame(current_price=Dec(130))) == {"MARGIN_BELOW_MIN"}
    assert codes(offer_frame(current_price=Dec(200), network_median=Dec(150))) == {
        "PRICE_DEVIATION"
    }
    assert codes(
        offer_frame(current_price=Dec(200), network_median=Dec(200), status="CONFLICT")
    ) == {"RULE_CONFLICT"}
    assert codes(
        offer_frame(current_price=None, status="BLOCKED", reason_code="NO_CURRENT_PRICE")
    ) == {"PRICE_UNDETERMINED"}


def test_dedup_key_is_stable_and_rule_dependent_only_where_needed():
    k1 = alerts.dedup_key("MARGIN_BELOW_MIN", 1, 2, 1, "ctx")
    assert k1 == alerts.dedup_key("MARGIN_BELOW_MIN", 1, 2, 1, "ctx")
    assert k1 != alerts.dedup_key("MARGIN_BELOW_MIN", 1, 2, 2, "ctx")
    assert alerts.dedup_key("COST_JUMP", None, 2, 1, "d") == alerts.dedup_key(
        "COST_JUMP", None, 2, 2, "d"
    )


def test_cost_jump_and_margin_drop():
    ch = pd.DataFrame(
        {
            "product_id": [1, 2],
            "valid_from": [date(2026, 10, 1)] * 2,
            "unit_cost": [Dec(135), Dec(105)],
            "previous_cost": [Dec(100), Dec(100)],
        }
    )
    assert [a["product_id"] for a in alerts.cost_jump_alerts(ch, {1: "A"}, 1)] == [1]
    win = pd.DataFrame(
        {
            "store_id": [1, 1],
            "product_id": [1, 2],
            "rev_cur": [Dec(2000), Dec(500)],
            "cogs_cur": [Dec(1300), Dec(400)],
            "rev_prev": [Dec(2000), Dec(5000)],
            "cogs_prev": [Dec(1000), Dec(1000)],
        }
    )
    found = alerts.margin_drop_alerts(win, date(2026, 10, 31), 1)
    assert [a["product_id"] for a in found] == [1]  # второй — выручка окна ниже порога


# --- Экспорт ------------------------------------------------------------------------------------


def test_export_keeps_types(tmp_path):
    df = pd.DataFrame(
        {"Товар": ["Ролл"], "Цена": [Dec("249.00")], "Дата": [date(2026, 10, 31)], "Кол-во": [3]}
    )
    xlsx = tmp_path / "x.xlsx"
    xlsx.write_bytes(to_xlsx_bytes({"Данные": df}, {"Период": "тест"}))
    ws = load_workbook(xlsx)["Данные"]
    assert isinstance(ws["B2"].value, int | float) and ws["B2"].value == 249
    assert ws["B2"].number_format == "#,##0.00"
    assert ws["C2"].is_date
    assert isinstance(ws["D2"].value, int)
    assert "синтетические" in load_workbook(xlsx)["Описание"]["A1"].value
    csv = to_csv_bytes(df).decode("utf-8-sig")
    assert "249,0" in csv and csv.startswith("# Демонстрационные")
