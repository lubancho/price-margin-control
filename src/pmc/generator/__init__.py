"""Генератор демонстрационных данных.

build_demo(seed) — справочники + факты + заведомые ошибки.
save_csv(data, folder) — запись наборов в CSV для просмотра и проверки воспроизводимости.
"""

from __future__ import annotations

import json
from pathlib import Path

from pmc.generator.errors import inject_errors
from pmc.generator.facts import DemoData, generate

FRAMES = (
    "stores",
    "categories",
    "products",
    "product_cost",
    "store_price",
    "promo",
    "promo_item",
    "store_calendar",
    "sale_line",
    "stock_daily",
    "write_off",
)


def build_demo(seed: int, with_errors: bool = True) -> DemoData:
    data = generate(seed)
    if with_errors:
        inject_errors(data)
    return data


def save_csv(data: DemoData, folder: Path) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for name in FRAMES:
        path = folder / f"{name}.csv"
        getattr(data, name).to_csv(path, index=False, lineterminator="\n")
        written.append(path)
    meta = {"seed": data.seed, "start": data.start.isoformat(), "days": data.days}
    (folder / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    manifest = folder / "error_manifest.json"
    manifest.write_text(json.dumps(data.manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    written += [folder / "meta.json", manifest]
    return written


__all__ = ["FRAMES", "DemoData", "build_demo", "save_csv"]
