"""Интеграционные тесты с PostgreSQL (TC-04, TC-06, TC-11…TC-15, TC-17, TC-19, TC-23, AC-06…AC-09).

Тесты выполняются в одном порядке и используют общую базу. Каждый тест проверяет
изменения относительно состояния до своего действия, а не абсолютные числа.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal as Dec

import psycopg
import pytest

from pmc import metrics
from pmc import repository as repo
from pmc.db import fetch_all, fetch_one
from pmc.migrate import migrate
from pmc.pricing import RoundingMode, Rule
from pmc.run_service import create_rule_set, decide, set_alert_status, start_run

pytestmark = pytest.mark.integration


def count(conn, sql, params=None) -> int:
    return fetch_one(conn, sql, params)["n"]


def test_migrations_are_idempotent(conn):
    assert migrate(conn) == []


# TC-11
def test_duplicates_do_not_reach_core(conn):
    assert (
        count(conn, "SELECT count(*) - count(DISTINCT sale_line_id) AS n FROM core.sale_line") == 0
    )
    assert (
        count(conn, "SELECT count(*) AS n FROM audit.dq_issue WHERE check_code='DUPLICATE'") == 50
    )
    raw_dups = count(conn, "SELECT count(*) - count(DISTINCT sale_line_id) AS n FROM raw.sale_line")
    assert raw_dups == 50


# TC-04, TC-06
def test_database_constraints_reject_bad_finance(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO core.product_cost (product_id, valid_from, unit_cost, source, calc_version) "
            "VALUES (1, '2030-01-01', -10, 'тест', 99)"
        )
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO calc.rule_set (version, effective_from, created_by) VALUES (900, '2030-01-01', 't')"
        )
        conn.execute(
            "INSERT INTO calc.pricing_rule (rule_set_id, scope_type, target_margin, min_margin, max_change, "
            "rounding_step, rounding_mode) SELECT rule_set_id, 'global', 1, 0.3, 0.1, 10, 'up' "
            "FROM calc.rule_set WHERE version = 900"
        )
    conn.execute("DELETE FROM calc.rule_set WHERE version = 900")


# TC-23 / AC-02
def test_every_planted_error_is_detected(conn, demo_data):
    found_dq = {
        (r["check_code"], r["source_key"], r["store_id"], r["product_id"])
        for r in fetch_all(
            conn, "SELECT check_code, source_key, store_id, product_id FROM audit.dq_issue"
        )
    }
    start_run(conn, actor="тест")
    alerts = {
        (r["alert_code"], r["store_id"], r["product_id"])
        for r in fetch_all(conn, "SELECT alert_code, store_id, product_id FROM calc.alert")
    }
    missed = []
    for m in demo_data.manifest:
        if m["detect_kind"] == "dq":
            ok = any(
                code == m["detect_as"]
                and (
                    ("sale_line_id" in m and key == f"sale_line_id={m['sale_line_id']}")
                    or (
                        "sale_line_id" not in m
                        and pid == m.get("product_id")
                        and (m.get("store_id") is None or sid == m["store_id"])
                    )
                )
                for code, key, sid, pid in found_dq
            )
        else:
            ok = (m["detect_as"], m.get("store_id"), m.get("product_id")) in alerts
        if not ok:
            missed.append(m)
    assert missed == []


# TC-15 / AC-03
def test_sql_and_python_totals_match_to_the_kopeck(conn):
    d0, d1 = repo.data_period(conn)
    sql = repo.sales_aggregates(conn, d0, d1).iloc[0]
    sql_m = metrics.from_sums(
        sql.revenue, sql.revenue_costed, sql.cogs, sql.net_qty, sql.lines_no_cost
    )
    py_m = metrics.python_sales_totals(
        repo.sale_lines_for_check(conn, d0, d1), repo.product_costs(conn)
    )
    assert sql_m.revenue == py_m.revenue
    assert sql_m.cogs.quantize(Dec("0.01")) == py_m.cogs.quantize(Dec("0.01"))
    assert sql_m.net_qty == py_m.net_qty
    assert sql_m.lines_no_cost == py_m.lines_no_cost


def test_breakdowns_add_up_to_network_total(conn):
    d0, d1 = repo.data_period(conn)
    total = repo.sales_aggregates(conn, d0, d1).iloc[0]
    for grain in ("store", "category", "day"):
        part = repo.sales_aggregates(conn, d0, d1, grain)
        assert sum(part["revenue"]) == total.revenue
        assert sum(part["cogs"]) == total.cogs


# TC-17
def test_cost_is_taken_as_of_sale_date(conn):
    row = fetch_one(
        conn,
        """
        SELECT f.product_id, f.sale_date, f.unit_cost,
               (SELECT unit_cost FROM core.product_cost c WHERE c.product_id = f.product_id
                  AND c.valid_from <= f.sale_date ORDER BY valid_from DESC LIMIT 1) AS expected
        FROM core.v_sale_fact f
        WHERE f.product_id IN (SELECT product_id FROM core.product_cost GROUP BY 1 HAVING count(*) > 2)
        ORDER BY f.sale_line_id DESC LIMIT 1""",
    )
    assert row["unit_cost"] == row["expected"]
    mismatches = count(
        conn,
        """
        SELECT count(*) AS n FROM core.v_sale_fact f JOIN core.v_product_cost_range r
          ON r.product_id = f.product_id AND f.sale_date BETWEEN r.valid_from AND r.valid_to
        WHERE f.unit_cost <> r.unit_cost""",
    )
    assert mismatches == 0


# TC-12 / AC-06
def test_repeated_run_creates_nothing_new(conn):
    first = start_run(conn, actor="тест")
    before = {
        t: count(conn, f"SELECT count(*) AS n FROM {t}")
        for t in ("calc.pricing_run", "calc.price_recommendation", "calc.alert")
    }
    again = start_run(conn, actor="тест")
    after = {
        t: count(conn, f"SELECT count(*) AS n FROM {t}")
        for t in ("calc.pricing_run", "calc.price_recommendation", "calc.alert")
    }
    assert again["status"] == "exists" and again["run_id"] == first["run_id"]
    assert before == after


# AC-07, TC-13
def test_forced_run_supersedes_and_does_not_duplicate_open_alerts(conn):
    prev = repo.last_done_run(conn)
    alerts_before = count(conn, "SELECT count(*) AS n FROM calc.alert")
    res = start_run(conn, actor="тест", force=True)
    assert res["status"] == "done" and res["alerts_new"] == 0 and res["alerts_repeated"] > 0
    assert count(conn, "SELECT count(*) AS n FROM calc.alert") == alerts_before
    open_old = count(
        conn,
        "SELECT count(*) AS n FROM calc.price_recommendation WHERE run_id = %s "
        "AND status IN ('PROPOSED','CONFLICT','ON_HOLD')",
        (prev["run_id"],),
    )
    assert open_old == 0
    assert count(conn, "SELECT max(occurrences) AS n FROM calc.alert") >= 2


def test_run_statuses_cover_all_branches(conn):
    run = repo.last_done_run(conn)
    statuses = {
        r["status"]
        for r in fetch_all(
            conn,
            "SELECT DISTINCT status FROM calc.price_recommendation WHERE run_id = %s",
            (run["run_id"],),
        )
    }
    assert {"PROPOSED", "NO_CHANGE", "CONFLICT", "BLOCKED", "ON_HOLD"} <= statuses


# TC-14 / AC-09
def test_decisions_are_saved_in_history(conn):
    run = repo.last_done_run(conn)
    recs = repo.recommendations(conn, run["run_id"])
    proposed = recs[recs["status"] == "PROPOSED"]["recommendation_id"].tolist()[:2]
    conflict = recs[recs["status"] == "CONFLICT"]["recommendation_id"].tolist()[:1]
    assert decide(conn, proposed[:1], approve=True, actor="руководитель") == 1
    assert (
        decide(conn, proposed[1:], approve=False, actor="руководитель", comment="ждём поставщика")
        == 1
    )
    with pytest.raises(ValueError):
        decide(conn, conflict, approve=True, actor="руководитель")
    assert decide(conn, conflict, approve=True, actor="руководитель", manual_price=Dec("300")) == 1
    rows = fetch_all(
        conn,
        "SELECT status, decided_by FROM calc.price_recommendation WHERE recommendation_id = ANY(%s)",
        (proposed + conflict,),
    )
    assert sorted(r["status"] for r in rows) == ["APPROVED", "APPROVED", "REJECTED"]
    events = fetch_all(
        conn, "SELECT event_type FROM audit.event_log ORDER BY event_id DESC LIMIT 3"
    )
    assert {e["event_type"] for e in events} == {
        "RECOMMENDATION_APPROVED",
        "RECOMMENDATION_REJECTED",
    }
    assert (
        count(conn, "SELECT count(*) AS n FROM audit.event_log WHERE event_type='RUN_COMPLETED'")
        >= 2
    )
    # Повторное решение по уже согласованной рекомендации не применяется.
    assert decide(conn, proposed[:1], approve=False, actor="руководитель") == 0


def test_event_log_is_append_only(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("DELETE FROM audit.event_log")


# TC-19 / AC-08
def test_used_rule_set_is_locked(conn):
    rs = repo.latest_rule_set(conn)
    assert rs["is_locked"]
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "UPDATE calc.pricing_rule SET target_margin = 0.5 WHERE rule_set_id = %s",
            (rs["rule_set_id"],),
        )


def test_new_rule_set_version_changes_results(conn):
    d1 = repo.data_period(conn)[1]
    rules = [
        Rule(
            None,
            "global",
            None,
            Dec("0.50"),
            Dec("0.30"),
            Dec("0.20"),
            Dec("10"),
            RoundingMode.UP,
            False,
        )
    ]
    with pytest.raises(ValueError):
        create_rule_set(
            conn,
            [Rule(None, "category", 1, Dec("0.5"), Dec("0.3"), Dec("0.2"), Dec("10"))],
            "аналитик",
            d1,
        )
    version = create_rule_set(conn, rules, "аналитик", d1, "тест")
    res = start_run(conn, as_of=d1, actor="тест")
    assert res["status"] == "done"
    assert repo.last_done_run(conn)["rule_version"] == version


def test_alert_status_change_is_logged(conn):
    alert = fetch_one(conn, "SELECT alert_id FROM calc.alert WHERE status='NEW' LIMIT 1")
    set_alert_status(conn, alert["alert_id"], "IN_PROGRESS", "операционный менеджер")
    assert (
        fetch_one(conn, "SELECT status FROM calc.alert WHERE alert_id=%s", (alert["alert_id"],))[
            "status"
        ]
        == "IN_PROGRESS"
    )
    last = fetch_one(conn, "SELECT event_type FROM audit.event_log ORDER BY event_id DESC LIMIT 1")
    assert last["event_type"] == "ALERT_STATUS_CHANGED"


def test_run_without_global_rule_fails_cleanly(conn):
    d = date(2030, 1, 1)
    conn.execute(
        "INSERT INTO calc.rule_set (version, effective_from, created_by) VALUES (999, %s, 'тест')",
        (d,),
    )
    conn.execute(
        "INSERT INTO calc.pricing_rule (rule_set_id, scope_type, scope_id, target_margin, min_margin, "
        "max_change, rounding_step, rounding_mode) SELECT rule_set_id, 'category', 1, 0.4, 0.3, 0.1, 10, 'up' "
        "FROM calc.rule_set WHERE version = 999"
    )
    from pmc.run_service import RunError

    with pytest.raises(RunError):
        start_run(conn, as_of=d, actor="тест")
    failed = fetch_one(conn, "SELECT status FROM calc.pricing_run ORDER BY started_at DESC LIMIT 1")
    assert failed["status"] == "failed"
    assert (
        count(
            conn,
            "SELECT count(*) AS n FROM calc.price_recommendation r JOIN calc.pricing_run p "
            "USING (run_id) WHERE p.status = 'failed'",
        )
        == 0
    )
