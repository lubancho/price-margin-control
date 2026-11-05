"""Выбор правила ценообразования для предложения (BR-08).

Приоритет: правило товара → правило категории → правило точки → правило сети.
Берётся самое конкретное из существующих.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from pmc.pricing import RoundingMode, Rule

PRIORITY = ("product", "category", "store", "global")


class RuleIndex:
    def __init__(self, rules: Iterable[Rule]):
        self._by_key: dict[tuple[str, int | None], Rule] = {}
        for r in rules:
            self._by_key[(r.scope_type, r.scope_id)] = r

    def all(self) -> list[Rule]:
        return list(self._by_key.values())

    def has_global(self) -> bool:
        return ("global", None) in self._by_key

    def select(self, store_id: int, category_id: int, product_id: int) -> Rule | None:
        for scope, sid in (
            ("product", product_id),
            ("category", category_id),
            ("store", store_id),
            ("global", None),
        ):
            rule = self._by_key.get((scope, sid))
            if rule is not None:
                return rule
        return None


def rule_from_row(row: dict) -> Rule:
    return Rule(
        rule_id=row.get("rule_id"),
        scope_type=row["scope_type"],
        scope_id=row.get("scope_id"),
        target_margin=Decimal(str(row["target_margin"])),
        min_margin=Decimal(str(row["min_margin"])),
        max_change=Decimal(str(row["max_change"])),
        rounding_step=Decimal(str(row["rounding_step"])),
        rounding_mode=RoundingMode(row["rounding_mode"]),
        allow_decrease=bool(row["allow_decrease"]),
    )
