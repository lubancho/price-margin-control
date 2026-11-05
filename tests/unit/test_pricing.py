"""Модульные тесты алгоритма цены и формул (ТЗ §17; TC-01…TC-10, TC-18, TC-20, TC-21)."""

from datetime import date
from decimal import Decimal as Dec

import pytest

from pmc.pricing import (
    Offer,
    RoundingMode,
    Rule,
    Status,
    base_price,
    margin,
    markup,
    recommend,
    round_to_step,
)
from pmc.rules import RuleIndex


def rule(**kw) -> Rule:
    params = dict(
        rule_id=1,
        scope_type="global",
        scope_id=None,
        target_margin=Dec("0.45"),
        min_margin=Dec("0.35"),
        max_change=Dec("0.10"),
        rounding_step=Dec("10"),
        rounding_mode=RoundingMode.UP,
        allow_decrease=False,
    )
    params.update(kw)
    return Rule(**params)


def offer(cost="152.40", price="249", **kw) -> Offer:
    return Offer(
        store_id=1,
        product_id=1,
        unit_cost=None if cost is None else Dec(cost),
        current_price=None if price is None else Dec(price),
        **kw,
    )


# TC-01
def test_margin_formula():
    assert margin(Dec("200"), Dec("120")) == Dec("0.4")


# TC-02
def test_margin_differs_from_markup():
    assert margin(Dec("200"), Dec("100")) == Dec("0.5")
    assert markup(Dec("200"), Dec("100")) == Dec("1")
    m = markup(Dec("150"), Dec("100"))
    assert margin(Dec("150"), Dec("100")) == m / (1 + m)  # маржа = наценка / (1 + наценка)


def test_margin_and_markup_return_none_for_bad_denominators():
    assert margin(Dec("0"), Dec("10")) is None
    assert margin(None, Dec("10")) is None
    assert markup(Dec("100"), Dec("0")) is None
    assert markup(Dec("100"), Dec("-5")) is None


# TC-03
def test_zero_cost_is_blocked():
    rec = recommend(offer(cost="0"), rule())
    assert rec.status == Status.BLOCKED and rec.reason_code == "INVALID_COST"
    assert rec.recommended_price is None


# TC-04
def test_negative_cost_is_blocked():
    rec = recommend(offer(cost="-10"), rule())
    assert rec.status == Status.BLOCKED and rec.reason_code == "INVALID_COST"


def test_rejected_latest_cost_version_blocks_even_if_old_cost_exists():
    rec = recommend(offer(cost_invalid=True), rule())
    assert rec.reason_code == "INVALID_COST"


# TC-05
def test_missing_current_price_is_blocked():
    rec = recommend(offer(price=None), rule())
    assert rec.status == Status.BLOCKED and rec.reason_code == "NO_CURRENT_PRICE"


# TC-06
def test_target_margin_100_percent_is_rejected():
    r = rule(target_margin=Dec("1"), min_margin=Dec("0.3"))
    assert r.errors()
    rec = recommend(offer(), r)
    assert rec.status == Status.BLOCKED and rec.reason_code == "INVALID_RULE"
    with pytest.raises(ValueError):
        base_price(Dec("100"), Dec("1"))


# TC-07
@pytest.mark.parametrize(
    "kw",
    [
        {"target_margin": Dec("-0.1"), "min_margin": Dec("0")},
        {"target_margin": Dec("1.5"), "min_margin": Dec("0.3")},
        {"target_margin": Dec("0.3"), "min_margin": Dec("0.4")},
        {"min_margin": Dec("-0.1")},
        {"max_change": Dec("0")},
        {"max_change": Dec("1.5")},
        {"rounding_step": Dec("0")},
    ],
)
def test_rule_parameters_out_of_bounds(kw):
    r = rule(**kw)
    assert r.errors()
    assert recommend(offer(), r).reason_code == "INVALID_RULE"


# TC-08
@pytest.mark.parametrize(
    ("mode", "expected"),
    [(RoundingMode.UP, "280.00"), (RoundingMode.NEAREST, "280.00"), (RoundingMode.DOWN, "270.00")],
)
def test_rounding_modes(mode, expected):
    assert round_to_step(Dec("277.09"), Dec("10"), mode) == Dec(expected)


def test_rounding_half_goes_up_and_exact_values_stay():
    assert round_to_step(Dec("275"), Dec("10"), RoundingMode.NEAREST) == Dec("280")
    assert round_to_step(Dec("270"), Dec("10"), RoundingMode.UP) == Dec("270")
    assert round_to_step(Dec("64.99"), Dec("5"), RoundingMode.UP) == Dec("65")


def test_rounding_down_never_breaks_min_margin():
    # Округление вниз дало бы 230 ₽ (маржа 33,7% < 35%) — цена поднимается до минимально допустимой.
    r = rule(
        target_margin=Dec("0.35"),
        min_margin=Dec("0.35"),
        rounding_mode=RoundingMode.DOWN,
        max_change=Dec("1"),
    )
    rec = recommend(offer(price="200"), r)
    assert rec.recommended_price == Dec("240.00")
    assert "ROUNDED_TO_MIN" in rec.flags


# TC-10 — пример из документа 04
def test_worked_example_change_limited_by_max_change():
    rec = recommend(offer(cost="152.40", price="249"), rule())
    assert rec.status == Status.PROPOSED
    assert rec.recommended_price == Dec("270.00")
    assert "LIMITED_UP" in rec.flags
    diff, pct = rec.change(Dec("249"))
    assert diff == Dec("21.00") and pct == Dec("0.0843")
    assert rec.base_price.quantize(Dec("0.01")) == Dec("277.09")


# TC-09 — пример конфликта из документа 04
def test_conflict_between_min_margin_and_max_change():
    rec = recommend(offer(cost="152.40", price="199"), rule())
    assert rec.status == Status.CONFLICT
    assert rec.reason_code == "MIN_MARGIN_VS_MAX_CHANGE"
    assert rec.recommended_price is None
    assert rec.details["limited_price"] == "210.00"


def test_conflict_when_step_is_wider_than_limit():
    rec = recommend(
        offer(cost="50", price="60"), rule(rounding_step=Dec("10"), max_change=Dec("0.10"))
    )
    assert rec.status == Status.CONFLICT and rec.reason_code == "STEP_WIDER_THAN_LIMIT"


def test_proposed_without_limit():
    # 100 / 0,55 = 181,82 → вверх до 190 ₽; лимит 180 × 1,1 = 198 ₽ не мешает.
    rec = recommend(offer(cost="100", price="180"), rule())
    assert rec.status == Status.PROPOSED and rec.recommended_price == Dec("190.00")
    assert rec.reason_code == "MARGIN_BELOW_TARGET" and rec.flags == []


def test_reason_cost_increase_after_last_price_change():
    o = offer(
        cost="100",
        price="180",
        previous_cost=Dec("90"),
        cost_valid_from=date(2026, 10, 1),
        price_valid_from=date(2026, 9, 1),
    )
    assert recommend(o, rule()).reason_code == "COST_INCREASE"


def test_no_change_when_price_matches_rule():
    rec = recommend(offer(cost="99", price="180"), rule())
    assert rec.status == Status.NO_CHANGE and rec.reason_code == "WITHIN_TARGET"


# TC-20
def test_decrease_disabled_by_default():
    rec = recommend(offer(cost="100", price="300"), rule())
    assert rec.status == Status.NO_CHANGE and rec.reason_code == "ABOVE_TARGET_DECREASE_DISABLED"


def test_decrease_allowed_is_limited():
    rec = recommend(offer(cost="100", price="300"), rule(allow_decrease=True))
    assert rec.status == Status.PROPOSED and rec.recommended_price == Dec("270.00")
    assert "LIMITED_DOWN" in rec.flags and rec.reason_code == "MARGIN_ABOVE_TARGET"


# TC-21
def test_promo_puts_recommendation_on_hold():
    rec = recommend(offer(cost="100", price="180", in_promo=True), rule())
    assert rec.status == Status.ON_HOLD and rec.recommended_price is None
    assert rec.details["proposed_after_promo"] == "190.00"


def test_inactive_offer_is_blocked():
    assert recommend(offer(is_active=False), rule()).reason_code == "INACTIVE"


def test_price_below_cost_gets_recommendation_or_conflict():
    rec = recommend(offer(cost="100", price="90"), rule())
    assert (
        rec.status == Status.CONFLICT
    )  # даже +10% (99 ₽ → 90 ₽ по шагу) не выводит в минимальную маржу


# TC-18
def test_rule_priority_product_then_category_then_store_then_global():
    g = rule(rule_id=1)
    s = rule(rule_id=2, scope_type="store", scope_id=1)
    c = rule(rule_id=3, scope_type="category", scope_id=5)
    p = rule(rule_id=4, scope_type="product", scope_id=7)
    idx = RuleIndex([g, s, c, p])
    assert idx.select(store_id=1, category_id=5, product_id=7).rule_id == 4
    assert idx.select(store_id=1, category_id=5, product_id=8).rule_id == 3
    assert idx.select(store_id=1, category_id=4, product_id=8).rule_id == 2
    assert idx.select(store_id=2, category_id=4, product_id=8).rule_id == 1
    assert RuleIndex([s]).select(2, 4, 8) is None


def test_no_rule_blocks():
    assert recommend(offer(), None).reason_code == "INVALID_RULE"
