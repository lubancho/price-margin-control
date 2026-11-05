"""Сборка интерактивной демо-версии дашборда для GitHub Pages.

Запуск (нужна база с загруженными данными и выполненным расчётом — `pmc init-demo`):
    uv run python scripts/build_static_demo.py --out site

Что делает: выгружает из базы компактные данные (продажи по дням × точкам × категориям, рекомендации
последнего расчёта, правила, истории цен, предупреждения, журнал) в site/data.js и копирует рядом
страницу, стили, код дашборда и библиотеку графиков ECharts. Показатели, фильтры и сценарии считаются
в браузере; при стандартных правилах браузерный пересчёт цен сверяется с результатом Python
(функция window.checkPricing на странице).
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

from pmc import repository as repo
from pmc.alerts import TITLES
from pmc.config import DEMO_NOTE
from pmc.db import connect, fetch_df
from pmc.pricing import margin

WEB = Path(__file__).parent / "web"


def _v(x):
    if x is None:
        return None
    if isinstance(x, Decimal):
        return round(float(x), 4)
    if isinstance(x, datetime | pd.Timestamp):
        return x.strftime("%Y-%m-%d %H:%M")
    if isinstance(x, date):
        return x.isoformat()
    if isinstance(x, float):
        return None if pd.isna(x) else round(x, 4)
    if hasattr(x, "item"):
        return x.item()
    return x


def rows(df: pd.DataFrame, cols: list[str]) -> list[list]:
    return [[_v(v) for v in r] for r in df[cols].itertuples(index=False, name=None)]


def collect(conn) -> dict:
    d0, d1 = repo.data_period(conn)
    run = repo.last_done_run(conn)
    as_of = run["as_of_date"]
    stores = repo.stores(conn)
    cats = repo.categories(conn)
    days = [d.isoformat() for d in pd.date_range(d0, d1).date]
    s_idx = {int(s): i for i, s in enumerate(stores["store_id"])}
    c_idx = {int(c): i for i, c in enumerate(cats["category_id"])}
    d_idx = {d: i for i, d in enumerate(days)}

    cube = fetch_df(
        conn,
        """
        SELECT sale_date, store_id, category_id,
               sum(net_revenue) AS rev,
               COALESCE(sum(net_revenue) FILTER (WHERE unit_cost IS NOT NULL), 0) AS rev_c,
               COALESCE(sum(net_cogs), 0) AS cogs,
               sum(net_quantity) AS qty
        FROM core.v_sale_fact GROUP BY 1, 2, 3 ORDER BY 1, 2, 3""",
    )
    cube_rows = [
        [
            d_idx[r.sale_date.isoformat()],
            s_idx[int(r.store_id)],
            c_idx[int(r.category_id)],
            round(float(r.rev), 2),
            round(float(r.rev_c), 2),
            round(float(r.cogs), 2),
            round(float(r.qty), 3),
        ]
        for r in cube.itertuples(index=False)
    ]

    recs = repo.recommendations(conn, run["run_id"])
    offers = repo.offers_as_of(conn, as_of)[
        [
            "store_id",
            "product_id",
            "cost_invalid",
            "in_promo",
            "previous_cost",
            "cost_valid_from",
            "price_valid_from",
            "is_active",
            "q0",
            "network_median",
        ]
    ]
    o = recs.merge(offers, on=["store_id", "product_id"], how="left")
    o["plan_margin"] = [
        margin(p, c) if c is not None and not pd.isna(c) and c > 0 else None
        for p, c in zip(o["current_price"], o["unit_cost"], strict=True)
    ]
    o["cost_newer"] = [
        bool(a and b and a > b)
        for a, b in zip(o["cost_valid_from"], o["price_valid_from"], strict=True)
    ]
    o["si"] = o["store_id"].map(s_idx)
    o["ci"] = o["category_id"].map(c_idx)
    offer_cols = [
        "si",
        "product_id",
        "sku",
        "product_name",
        "ci",
        "unit_cost",
        "current_price",
        "previous_cost",
        "cost_newer",
        "in_promo",
        "cost_invalid",
        "is_active",
        "q0",
        "status",
        "recommended_price",
        "change_abs",
        "change_pct",
        "reason_code",
        "reason_text",
        "plan_margin",
        "min_margin",
        "target_margin",
        "rule_scope",
        "network_median",
    ]

    rules = [
        {
            "scope": r["scope_type"],
            "id": r["scope_id"],
            "tm": float(r["target_margin"]),
            "mm": float(r["min_margin"]),
            "mc": float(r["max_change"]),
            "step": float(r["rounding_step"]),
            "mode": r["rounding_mode"],
            "dec": bool(r["allow_decrease"]),
        }
        for r in repo.rules_of(conn, run["rule_set_id"])
    ]

    ph = fetch_df(
        conn,
        "SELECT store_id, product_id, valid_from, price FROM core.store_price ORDER BY 1, 2, 3",
    )
    ch = fetch_df(
        conn, "SELECT product_id, valid_from, unit_cost FROM core.product_cost ORDER BY 1, 2"
    )

    al = repo.alerts(conn)
    al["title"] = al["alert_code"].map(TITLES)
    al["si"] = al["store_id"].map(lambda v: None if pd.isna(v) else s_idx[int(v)])
    alert_cols = [
        "alert_id",
        "severity",
        "alert_code",
        "title",
        "si",
        "store_name",
        "product_id",
        "product_name",
        "description",
        "first_seen_at",
        "occurrences",
        "status",
    ]
    runs = repo.runs(conn)
    runs["run_id"] = runs["run_id"].str[:8]

    return {
        "meta": {
            "note": DEMO_NOTE,
            "as_of": as_of.isoformat(),
            "rule_version": run["rule_version"],
            "run_id": str(run["run_id"])[:8],
            "built": datetime.now().strftime("%d.%m.%Y %H:%M"),
            "python_counts": recs["status"].value_counts().to_dict(),
        },
        "days": days,
        "stores": rows(stores, ["store_id", "name"]),
        "cats": rows(cats, ["category_id", "name"]),
        "cube": cube_rows,
        "offer_cols": offer_cols,
        "offers": rows(o, offer_cols),
        "rules": rules,
        "price_hist": {
            f"{s}|{p}": rows(g, ["valid_from", "price"])
            for (s, p), g in ph.groupby(["store_id", "product_id"])
        },
        "cost_hist": {
            str(p): rows(g, ["valid_from", "unit_cost"]) for p, g in ch.groupby("product_id")
        },
        "alert_cols": alert_cols,
        "alerts": rows(al, alert_cols),
        "dq": rows(repo.dq_summary(conn), ["check_code", "severity", "issues"]),
        "runs": rows(
            runs,
            [
                "run_id",
                "started_at",
                "as_of_date",
                "rule_version",
                "status",
                "triggered_by",
                "offers_total",
            ],
        ),
        "rule_sets": rows(
            repo.rule_sets(conn),
            ["version", "effective_from", "is_locked", "created_by", "comment", "rules"],
        ),
        "events": rows(repo.events(conn, 300), ["event_at", "event_type", "actor_role", "message"]),
    }


def build(out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        data = collect(conn)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    (out / "data.js").write_text(f"window.DATA={payload};\n", encoding="utf-8")
    for name in ("index.html", "common.css", "common.js", "app.js"):
        shutil.copy(WEB / name, out / name)
    shutil.copy(WEB / "vendor" / "echarts.min.js", out / "echarts.min.js")
    (out / ".nojekyll").touch()
    size = sum(p.stat().st_size for p in out.iterdir() if p.is_file()) / 1e6
    print(f"Собрано: {out / 'index.html'} (всего {size:.1f} МБ)")
    return out / "index.html"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("site"))
    build(ap.parse_args().out)
