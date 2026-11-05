"""Правила предупреждений (документ 04, раздел 5) и ключ защиты от дублей.

Простыми словами: функции здесь только решают, ЧТО является проблемой, и формируют список
предупреждений. Запись в базу делает run_service: открытое предупреждение с тем же ключом
не создаётся второй раз, а только получает отметку «замечено снова» (BR-13).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal

import pandas as pd

from pmc.metrics import D, safe_ratio
from pmc.pricing import margin


@dataclass(frozen=True)
class AlertSettings:
    deviation_threshold: Decimal = Decimal("0.15")  # PRICE_DEVIATION: отклонение от медианы сети
    cost_jump_threshold: Decimal = Decimal("0.10")  # COST_JUMP: изменение себестоимости
    margin_drop_pp: Decimal = Decimal("0.05")  # MARGIN_DROP: падение маржи, доля (5 п. п.)
    margin_drop_min_revenue: Decimal = Decimal("1000")  # MARGIN_DROP: минимальная выручка окна


DEFAULT_SETTINGS = AlertSettings()

SEVERITY = {
    "PRICE_BELOW_COST": "CRIT",
    "MARGIN_BELOW_MIN": "CRIT",
    "INVALID_FIN_DATA": "CRIT",
    "PRICE_UNDETERMINED": "CRIT",
    "PRICE_DEVIATION": "WARN",
    "COST_JUMP": "WARN",
    "MARGIN_DROP": "WARN",
    "RULE_CONFLICT": "WARN",
}

TITLES = {
    "PRICE_BELOW_COST": "Цена ниже себестоимости",
    "MARGIN_BELOW_MIN": "Маржа ниже минимальной",
    "INVALID_FIN_DATA": "Некорректные финансовые данные",
    "PRICE_UNDETERMINED": "Невозможно определить цену",
    "PRICE_DEVIATION": "Сильное отклонение цены от сети",
    "COST_JUMP": "Существенное изменение себестоимости",
    "MARGIN_DROP": "Снижение маржинальности",
    "RULE_CONFLICT": "Конфликт ограничений",
}

# Предупреждения, которые зависят от правил: при новой версии правил это новое событие.
RULE_DEPENDENT = {"MARGIN_BELOW_MIN", "RULE_CONFLICT", "PRICE_UNDETERMINED"}


def dedup_key(
    code: str,
    store_id: int | None,
    product_id: int | None,
    rule_version: int | None,
    context: str = "",
) -> str:
    version = rule_version if code in RULE_DEPENDENT else ""
    raw = f"{code}|{store_id or ''}|{product_id or ''}|{version}|{context}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _alert(
    code: str, store_id, product_id, description: str, rule_version, context: str = ""
) -> dict:
    sid = None if store_id is None or pd.isna(store_id) else int(store_id)
    pid = None if product_id is None or pd.isna(product_id) else int(product_id)
    return {
        "alert_code": code,
        "severity": SEVERITY[code],
        "store_id": sid,
        "product_id": pid,
        "description": description,
        "dedup_key": dedup_key(code, sid, pid, rule_version, context),
    }


def _fmt_pct(x: Decimal) -> str:
    return f"{x * 100:.1f}%".replace(".", ",")


def _fmt_rub(x) -> str:
    return f"{D(x):,.2f} ₽".replace(",", " ").replace(".", ",")


def offer_alerts(
    offers: pd.DataFrame, rule_version: int, settings: AlertSettings = DEFAULT_SETTINGS
) -> list[dict]:
    """Предупреждения по ценам и себестоимости предложений.

    Ожидаемые колонки: store_id, product_id, product_name, store_name, is_active, unit_cost,
    current_price, price_valid_from, cost_valid_from, min_margin, network_median,
    status, reason_code, reason_text (результат расчёта рекомендации).
    """
    out: list[dict] = []
    for r in offers.itertuples(index=False):
        if not r.is_active:
            continue
        cost = None if r.unit_cost is None or pd.isna(r.unit_cost) else D(r.unit_cost)
        price = None if r.current_price is None or pd.isna(r.current_price) else D(r.current_price)
        ctx = f"{r.price_valid_from}|{r.cost_valid_from}"
        if r.status == "BLOCKED" and r.reason_code in ("NO_CURRENT_PRICE", "INVALID_RULE"):
            out.append(
                _alert("PRICE_UNDETERMINED", r.store_id, r.product_id, r.reason_text, rule_version)
            )
        if r.status == "CONFLICT":
            out.append(
                _alert("RULE_CONFLICT", r.store_id, r.product_id, r.reason_text, rule_version, ctx)
            )
        if (
            cost is None
            or cost <= 0
            or price is None
            or price <= 0
            or getattr(r, "cost_invalid", False)
        ):
            continue
        if price < cost:
            out.append(
                _alert(
                    "PRICE_BELOW_COST",
                    r.store_id,
                    r.product_id,
                    f"Цена {_fmt_rub(price)} ниже себестоимости {_fmt_rub(cost)}",
                    rule_version,
                    ctx,
                )
            )
        elif (
            r.min_margin is not None
            and not pd.isna(r.min_margin)
            and margin(price, cost) < D(r.min_margin)
        ):
            out.append(
                _alert(
                    "MARGIN_BELOW_MIN",
                    r.store_id,
                    r.product_id,
                    f"Маржа {_fmt_pct(margin(price, cost))} ниже минимальной {_fmt_pct(D(r.min_margin))} "
                    f"(цена {_fmt_rub(price)}, себестоимость {_fmt_rub(cost)})",
                    rule_version,
                    ctx,
                )
            )
        median = (
            None if r.network_median is None or pd.isna(r.network_median) else D(r.network_median)
        )
        dev = safe_ratio(price - median, median) if median is not None else None
        if dev is not None and abs(dev) > settings.deviation_threshold:
            word = "выше" if dev > 0 else "ниже"
            out.append(
                _alert(
                    "PRICE_DEVIATION",
                    r.store_id,
                    r.product_id,
                    f"Цена {_fmt_rub(price)} на {_fmt_pct(abs(dev))} {word} медианы сети {_fmt_rub(median)}",
                    rule_version,
                    str(r.price_valid_from),
                )
            )
    return out


def invalid_cost_alerts(offers: pd.DataFrame, rule_version: int) -> list[dict]:
    """Одно предупреждение на товар с некорректной или отсутствующей себестоимостью."""
    out = []
    bad = offers[offers["cost_invalid"] | offers["unit_cost"].isna()]
    for pid, grp in bad.groupby("product_id"):
        out.append(
            _alert(
                "INVALID_FIN_DATA",
                None,
                pid,
                f"«{grp['product_name'].iloc[0]}»: последняя версия себестоимости отсутствует или ≤ 0 — "
                f"расчёт цены заблокирован в {len(grp)} точках",
                rule_version,
                "cost",
            )
        )
    return out


def dq_alerts(dq: pd.DataFrame, valid_product_ids: set[int], rule_version: int) -> list[dict]:
    """Критичные проблемы качества строк продаж и цен → INVALID_FIN_DATA (себестоимость — отдельно)."""
    out = []
    for r in dq.itertuples(index=False):
        if r.source_table == "product_cost" or r.check_code == "MISSING_PRICE":
            continue  # покрыты invalid_cost_alerts и PRICE_UNDETERMINED
        pid = r.product_id if r.product_id in valid_product_ids else None
        out.append(
            _alert(
                "INVALID_FIN_DATA",
                r.store_id,
                pid,
                f"Строка {r.source_table} ({r.source_key}) отклонена проверкой {r.check_code}",
                rule_version,
                r.source_key,
            )
        )
    return out


def cost_jump_alerts(
    changes: pd.DataFrame,
    names: dict[int, str],
    rule_version: int,
    settings: AlertSettings = DEFAULT_SETTINGS,
) -> list[dict]:
    out = []
    for r in changes.itertuples(index=False):
        rel = safe_ratio(D(r.unit_cost) - D(r.previous_cost), D(r.previous_cost))
        if rel is not None and abs(rel) > settings.cost_jump_threshold:
            sign = "+" if rel > 0 else "−"
            out.append(
                _alert(
                    "COST_JUMP",
                    None,
                    r.product_id,
                    f"«{names.get(r.product_id, r.product_id)}»: себестоимость {_fmt_rub(r.previous_cost)} → "
                    f"{_fmt_rub(r.unit_cost)} ({sign}{_fmt_pct(abs(rel))}) с {r.valid_from:%d.%m.%Y}",
                    rule_version,
                    str(r.valid_from),
                )
            )
    return out


def margin_drop_alerts(
    windows: pd.DataFrame, as_of, rule_version: int, settings: AlertSettings = DEFAULT_SETTINGS
) -> list[dict]:
    out = []
    for r in windows.itertuples(index=False):
        rev_c, rev_p = D(r.rev_cur), D(r.rev_prev)
        if rev_c < settings.margin_drop_min_revenue or rev_p < settings.margin_drop_min_revenue:
            continue
        m_c = safe_ratio(rev_c - D(r.cogs_cur), rev_c)
        m_p = safe_ratio(rev_p - D(r.cogs_prev), rev_p)
        if m_c is None or m_p is None:
            continue
        if m_p - m_c > settings.margin_drop_pp:
            out.append(
                _alert(
                    "MARGIN_DROP",
                    r.store_id,
                    r.product_id,
                    f"Фактическая маржинальность за 14 дней {_fmt_pct(m_c)} против {_fmt_pct(m_p)} "
                    f"в предыдущие 14 дней (−{str(round((m_p - m_c) * 100, 1)).replace('.', ',')} п. п.)",
                    rule_version,
                    str(as_of),
                )
            )
    return out
