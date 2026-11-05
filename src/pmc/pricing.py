"""Алгоритм рекомендуемой цены (документ 04, раздел 4).

Простыми словами: для одной пары «точка × товар» берём себестоимость и правило,
считаем цену, при которой маржа равна целевой, округляем до шага витрины,
проверяем, что маржа не ниже минимальной и что цена не меняется слишком резко.
Если правила противоречат друг другу — не придумываем цену, а честно пишем «конфликт».

Все деньги — Decimal. float здесь не используется (NFR-04).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from enum import StrEnum

ONE = Decimal("1")
CENT = Decimal("0.01")


class RoundingMode(StrEnum):
    UP = "up"
    NEAREST = "nearest"
    DOWN = "down"


class Status(StrEnum):
    PROPOSED = "PROPOSED"
    NO_CHANGE = "NO_CHANGE"
    CONFLICT = "CONFLICT"
    BLOCKED = "BLOCKED"
    ON_HOLD = "ON_HOLD"


@dataclass(frozen=True)
class Rule:
    rule_id: int | None
    scope_type: str
    scope_id: int | None
    target_margin: Decimal
    min_margin: Decimal
    max_change: Decimal
    rounding_step: Decimal
    rounding_mode: RoundingMode = RoundingMode.UP
    allow_decrease: bool = False

    def errors(self) -> list[str]:
        """Проверка параметров правила. Пустой список — правило корректно."""
        errs = []
        if not (Decimal(0) <= self.target_margin < ONE):
            errs.append("Целевая маржа должна быть от 0 до 1, не включая 1 (100% невозможно)")
        if not (Decimal(0) <= self.min_margin):
            errs.append("Минимальная маржа не может быть отрицательной")
        if self.min_margin > self.target_margin:
            errs.append("Минимальная маржа не может быть больше целевой")
        if not (Decimal(0) < self.max_change <= ONE):
            errs.append("Допустимое изменение цены должно быть больше 0 и не больше 100%")
        if self.rounding_step <= 0:
            errs.append("Шаг округления должен быть больше нуля")
        if self.scope_type not in ("global", "store", "category", "product"):
            errs.append("Неизвестная область действия правила")
        return errs


@dataclass(frozen=True)
class Offer:
    """Товарное предложение на дату расчёта."""

    store_id: int
    product_id: int
    unit_cost: Decimal | None
    current_price: Decimal | None
    is_active: bool = True
    cost_invalid: bool = False  # последняя версия себестоимости отклонена проверкой качества
    in_promo: bool = False
    previous_cost: Decimal | None = None
    cost_valid_from: date | None = None
    price_valid_from: date | None = None


@dataclass
class Recommendation:
    status: Status
    reason_code: str
    reason_text: str
    recommended_price: Decimal | None = None
    base_price: Decimal | None = None
    flags: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)

    def change(self, current_price: Decimal | None) -> tuple[Decimal | None, Decimal | None]:
        """Изменение в рублях и долях. None, если считать не от чего."""
        if self.recommended_price is None or current_price is None or current_price <= 0:
            return None, None
        diff = self.recommended_price - current_price
        return diff, (diff / current_price).quantize(Decimal("0.0001"), ROUND_HALF_UP)


# --- Формулы ---------------------------------------------------------------------------------


def margin(price: Decimal | None, cost: Decimal | None) -> Decimal | None:
    """Маржа = (цена − себестоимость) / цена. None, если цены нет или она ≤ 0."""
    if price is None or cost is None or price <= 0:
        return None
    return (price - cost) / price


def markup(price: Decimal | None, cost: Decimal | None) -> Decimal | None:
    """Наценка = (цена − себестоимость) / себестоимость. None, если себестоимость ≤ 0."""
    if price is None or cost is None or cost <= 0:
        return None
    return (price - cost) / cost


def base_price(cost: Decimal, target_margin: Decimal) -> Decimal:
    """Цена, при которой маржа равна целевой: себестоимость / (1 − целевая маржа)."""
    if not (Decimal(0) <= target_margin < ONE):
        raise ValueError("Целевая маржа должна быть в диапазоне [0; 1)")
    return cost / (ONE - target_margin)


def round_to_step(value: Decimal, step: Decimal, mode: RoundingMode | str) -> Decimal:
    """Округление до кратного шагу: вверх, вниз или к ближайшему (половина — вверх)."""
    if step <= 0:
        raise ValueError("Шаг округления должен быть больше нуля")
    rounding = {
        RoundingMode.UP: ROUND_CEILING,
        RoundingMode.DOWN: ROUND_FLOOR,
        RoundingMode.NEAREST: ROUND_HALF_UP,
    }[RoundingMode(mode)]
    return ((value / step).quantize(ONE, rounding=rounding) * step).quantize(CENT)


def _pct(x: Decimal) -> str:
    return f"{(x * 100).quantize(Decimal('0.1'), ROUND_HALF_UP)}%".replace(".", ",")


def _rub(x: Decimal) -> str:
    return f"{x.quantize(CENT, ROUND_HALF_UP):,.2f} ₽".replace(",", " ").replace(".", ",")


# --- Алгоритм ---------------------------------------------------------------------------------


def recommend(offer: Offer, rule: Rule | None) -> Recommendation:
    c, p0 = offer.unit_cost, offer.current_price

    # Шаг 1. Проверка данных.
    if not offer.is_active:
        return Recommendation(
            Status.BLOCKED, "INACTIVE", "Товар или точка неактивны — расчёт не выполняется"
        )
    if offer.cost_invalid or c is None or c <= 0:
        return Recommendation(
            Status.BLOCKED,
            "INVALID_COST",
            "Себестоимость отсутствует или некорректна (≤ 0) — рассчитать цену нельзя",
        )
    if p0 is None or p0 <= 0:
        return Recommendation(
            Status.BLOCKED,
            "NO_CURRENT_PRICE",
            "Нет действующей цены в точке — невозможно проверить лимит изменения",
        )
    if rule is None or rule.errors():
        msg = "; ".join(rule.errors()) if rule else "Правило не найдено"
        return Recommendation(
            Status.BLOCKED, "INVALID_RULE", f"Правило ценообразования некорректно: {msg}"
        )

    step, mode = rule.rounding_step, rule.rounding_mode
    details: dict = {
        "target_margin": str(rule.target_margin),
        "min_margin": str(rule.min_margin),
        "max_change": str(rule.max_change),
        "rounding_step": str(step),
        "rounding_mode": str(mode),
    }
    flags: list[str] = []

    # Шаг 2. Базовая цена.
    p_base = base_price(c, rule.target_margin)
    # Шаг 3. Округление и минимально допустимая цена.
    p_r = round_to_step(p_base, step, mode)
    p_min = round_to_step(base_price(c, rule.min_margin), step, RoundingMode.UP)
    details.update(
        base_price=str(p_base.quantize(Decimal("0.0001"))), rounded=str(p_r), min_price=str(p_min)
    )

    # Шаг 4. Минимальная маржа: округление не может опустить цену ниже минимально допустимой.
    if p_r < p_min:
        p_r = p_min
        flags.append("ROUNDED_TO_MIN")

    # Шаг 4а. Снижение цены запрещено правилом.
    if p_r < p0 and not rule.allow_decrease:
        return Recommendation(
            Status.NO_CHANGE,
            "ABOVE_TARGET_DECREASE_DISABLED",
            f"Маржа {_pct(margin(p0, c))} не ниже целевой {_pct(rule.target_margin)}; "
            "снижение цены правилом не предусмотрено",
            recommended_price=p0,
            base_price=p_base,
            flags=flags,
            details=details,
        )

    # Шаг 5. Допустимое изменение цены.
    l_high = p0 * (ONE + rule.max_change)
    l_low = p0 * (ONE - rule.max_change)
    details.update(limit_high=str(l_high.quantize(CENT)), limit_low=str(l_low.quantize(CENT)))
    p_c = p_r
    if p_r > l_high:
        p_c = round_to_step(l_high, step, RoundingMode.DOWN)
        flags.append("LIMITED_UP")
        if p_c <= p0:
            return _conflict(
                "STEP_WIDER_THAN_LIMIT",
                f"Шаг округления {_rub(step)} больше допустимого изменения {_pct(rule.max_change)} — "
                "повысить цену в пределах лимита нельзя",
                p_base,
                flags,
                details,
            )
    elif p_r < l_low:
        p_c = round_to_step(l_low, step, RoundingMode.UP)
        flags.append("LIMITED_DOWN")
        if p_c >= p0:
            return _conflict(
                "STEP_WIDER_THAN_LIMIT",
                f"Шаг округления {_rub(step)} больше допустимого изменения {_pct(rule.max_change)} — "
                "снизить цену в пределах лимита нельзя",
                p_base,
                flags,
                details,
            )

    # Шаг 5а. Повторная проверка минимальной маржи после ограничения.
    m_c = margin(p_c, c)
    if m_c is not None and m_c < rule.min_margin:
        details["limited_price"] = str(p_c)
        return _conflict(
            "MIN_MARGIN_VS_MAX_CHANGE",
            f"Для минимальной маржи {_pct(rule.min_margin)} нужна цена не ниже {_rub(p_min)} "
            f"(+{_pct((p_min - p0) / p0)}), а лимит изменения {_pct(rule.max_change)} "
            f"позволяет не выше {_rub(p_c)}. Нужно решение вручную",
            p_base,
            flags,
            details,
        )

    # Шаг 6. Прочие ограничения: действующая промоакция.
    if offer.in_promo and p_c != p0:
        details["proposed_after_promo"] = str(p_c)
        return Recommendation(
            Status.ON_HOLD,
            "PROMO_ACTIVE",
            f"Товар участвует в промоакции; пересмотр цены (до {_rub(p_c)}) отложен до её окончания",
            recommended_price=None,
            base_price=p_base,
            flags=flags,
            details=details,
        )

    # Шаг 7. Итог.
    if p_c == p0:
        return Recommendation(
            Status.NO_CHANGE,
            "WITHIN_TARGET",
            f"Текущая цена соответствует правилам (маржа {_pct(margin(p0, c))})",
            recommended_price=p0,
            base_price=p_base,
            flags=flags,
            details=details,
        )

    if p_c < p0:
        code, text = (
            "MARGIN_ABOVE_TARGET",
            f"Маржа {_pct(margin(p0, c))} выше целевой; снижение разрешено правилом",
        )
    elif (
        offer.previous_cost is not None
        and c > offer.previous_cost
        and offer.cost_valid_from is not None
        and offer.price_valid_from is not None
        and offer.cost_valid_from > offer.price_valid_from
    ):
        code = "COST_INCREASE"
        text = (
            f"Себестоимость выросла с {_rub(offer.previous_cost)} до {_rub(c)} "
            f"после последнего изменения цены"
        )
    else:
        code = "MARGIN_BELOW_TARGET"
        text = f"Маржа {_pct(margin(p0, c))} ниже целевой {_pct(rule.target_margin)}"
    if "LIMITED_UP" in flags or "LIMITED_DOWN" in flags:
        text += (
            f"; изменение ограничено лимитом {_pct(rule.max_change)}, целевая маржа будет "
            "достигнута при следующих пересмотрах"
        )
    if "ROUNDED_TO_MIN" in flags:
        text += "; цена поднята до минимально допустимой после округления"
    return Recommendation(
        Status.PROPOSED,
        code,
        text,
        recommended_price=p_c,
        base_price=p_base,
        flags=flags,
        details=details,
    )


def _conflict(
    code: str, text: str, p_base: Decimal, flags: list[str], details: dict
) -> Recommendation:
    return Recommendation(
        Status.CONFLICT,
        code,
        text,
        recommended_price=None,
        base_price=p_base,
        flags=flags,
        details=details,
    )
