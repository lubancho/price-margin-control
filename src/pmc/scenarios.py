"""Сценарный анализ «до / после» (документ 04, раздел 6; FR-11).

Простыми словами: берём продажи предложения за последние 28 дней (q0) и считаем,
какой была бы выручка и валовая прибыль по текущим ценам и по рекомендуемым.
Спрос либо считаем неизменным, либо меняем по коэффициенту, который ЗАДАЁТ ПОЛЬЗОВАТЕЛЬ.
Это сценарий, а не прогноз: эластичность спроса по данным не оценивается.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

import pandas as pd

from pmc.metrics import D, safe_ratio
from pmc.pricing import Rule
from pmc.rules import RuleIndex

ZERO = Decimal(0)
INCLUDED = ("PROPOSED", "APPROVED")


@dataclass(frozen=True)
class DemandAssumption:
    mode: str = "fixed"  # fixed — q1 = q0; coefficient — q1 = q0 × (1 + e × Δp)
    coefficient: Decimal = Decimal("-0.5")
    base_days: int = 28

    def describe(self) -> list[str]:
        lines = [
            f"Базовое количество — чистые продажи предложения за {self.base_days} дней до даты расчёта."
        ]
        if self.mode == "fixed":
            lines.append(
                "Спрос фиксирован: количество продаж после изменения цены не меняется (q1 = q0)."
            )
        else:
            e = str(self.coefficient).replace(".", ",")
            lines.append(
                f"Спрос меняется по заданному вручную коэффициенту e = {e}: при росте цены на 1% "
                f"продажи меняются на {e}%. Коэффициент не оценён по данным."
            )
        lines += [
            "Себестоимость считается неизменной на уровне даты расчёта.",
            "Промоакции и скидки в сценарии не моделируются: используются цены прайса.",
            "Предложения со статусами «конфликт», «заблокировано», «отложено» остаются по текущей цене.",
            "Это сценарий, а не прогноз фактического результата.",
        ]
        return lines


def simulate(rows: pd.DataFrame, assumption: DemandAssumption) -> pd.DataFrame:
    """Ожидаемые колонки: unit_cost, current_price, recommended_price, status, q0.

    Возвращает копию с колонками new_price, q1, rev0, rev1, gp0, gp1, change_pct.
    Строки без себестоимости или текущей цены в расчёт не входят (in_scenario = False).
    """
    out = rows.copy()
    new_price, q1s, rev0, rev1, gp0, gp1, chg, inside = [], [], [], [], [], [], [], []
    for r in out.itertuples(index=False):
        cost = None if r.unit_cost is None or pd.isna(r.unit_cost) else D(r.unit_cost)
        p0 = None if r.current_price is None or pd.isna(r.current_price) else D(r.current_price)
        ok = cost is not None and cost > 0 and p0 is not None and p0 > 0
        inside.append(ok)
        if not ok:
            new_price.append(None)
            q1s.append(None)
            rev0.append(None)
            rev1.append(None)
            gp0.append(None)
            gp1.append(None)
            chg.append(None)
            continue
        rec = r.recommended_price
        p1 = D(rec) if r.status in INCLUDED and rec is not None and not pd.isna(rec) else p0
        dp = (p1 - p0) / p0
        q0 = D(r.q0)
        if assumption.mode == "fixed":  # noqa: SIM108 — так нагляднее для читателя
            q1 = q0
        else:
            q1 = max(ZERO, q0 * (1 + assumption.coefficient * dp))
        new_price.append(p1)
        q1s.append(q1)
        rev0.append(p0 * q0)
        rev1.append(p1 * q1)
        gp0.append((p0 - cost) * q0)
        gp1.append((p1 - cost) * q1)
        chg.append(dp)
    out["new_price"], out["q1"], out["rev0"], out["rev1"] = new_price, q1s, rev0, rev1
    out["gp0"], out["gp1"], out["change_pct"], out["in_scenario"] = gp0, gp1, chg, inside
    return out


def totals(sim: pd.DataFrame, by: str | None = None) -> pd.DataFrame:
    """Итоги сценария: выручка, валовая прибыль и маржинальность до и после."""
    s = sim[sim["in_scenario"]]

    def agg(g: pd.DataFrame) -> pd.Series:
        r0, r1 = sum(g["rev0"], ZERO), sum(g["rev1"], ZERO)
        p0, p1 = sum(g["gp0"], ZERO), sum(g["gp1"], ZERO)
        return pd.Series(
            {
                "rev0": r0,
                "rev1": r1,
                "gp0": p0,
                "gp1": p1,
                "margin0": safe_ratio(p0, r0),
                "margin1": safe_ratio(p1, r1),
                "changed": int((g["new_price"] != g["current_price"]).sum()),
                "offers": len(g),
            }
        )

    if by is None:
        return agg(s).to_frame().T
    return s.groupby(by, sort=True).apply(agg, include_groups=False).reset_index()


def override_rules(rules: list[Rule], global_params: dict, replace_all: bool) -> RuleIndex:
    """Правила для сценария «что если»: меняем глобальное правило; при replace_all — только оно и действует."""
    new_rules = []
    for r in rules:
        if r.scope_type == "global":
            new_rules.append(replace(r, **global_params))
        elif not replace_all:
            new_rules.append(r)
    return RuleIndex(new_rules)
