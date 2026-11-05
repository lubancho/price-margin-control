"""Запуск расчёта рекомендуемых цен и действия пользователей (UC-01, UC-02).

Последовательность запуска (диаграмма в документе 05):
1. Определить дату расчёта и действующую версию правил.
2. Посчитать «отпечаток» данных и ключ идемпотентности. Если такой запуск уже завершён —
   вернуть его, ничего не пересчитывая (FR-06).
3. В одной транзакции: рассчитать рекомендации, записать их, записать/обновить предупреждения,
   закрыть набор правил от изменений, записать событие в журнал.
4. При ошибке — откат и отдельная запись о неудачном запуске.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections import Counter
from datetime import date
from decimal import Decimal

import psycopg
from psycopg.types.json import Jsonb

from pmc import alerts as alert_rules
from pmc import repository as repo
from pmc.db import execute, fetch_all, fetch_one
from pmc.metrics import D
from pmc.pricing import Offer, Rule, recommend
from pmc.rules import RuleIndex, rule_from_row

log = logging.getLogger(__name__)

OPEN_STATUSES = ("PROPOSED", "CONFLICT", "ON_HOLD")


class RunError(RuntimeError):
    pass


def log_event(
    conn: psycopg.Connection,
    event_type: str,
    actor: str,
    message: str,
    *,
    run_id=None,
    severity: str = "INFO",
    payload: dict | None = None,
) -> None:
    execute(
        conn,
        "INSERT INTO audit.event_log (event_type, severity, actor_role, run_id, message, payload) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (event_type, severity, actor, run_id, message, Jsonb(payload or {})),
    )


def data_fingerprint(conn: psycopg.Connection) -> str:
    row = fetch_one(
        conn,
        """
        SELECT (SELECT count(*) || ':' || COALESCE(max(price_id), 0) FROM core.store_price) AS prices,
               (SELECT count(*) || ':' || COALESCE(max(cost_id), 0) FROM core.product_cost) AS costs,
               (SELECT count(*) FROM core.promo_item) AS promos,
               (SELECT count(*) || ':' || COALESCE(max(sale_line_id), 0) FROM core.sale_line) AS sales,
               (SELECT COALESCE(max(issue_id), 0) FROM audit.dq_issue) AS dq,
               (SELECT string_agg(store_id || ':' || is_active, ',' ORDER BY store_id) FROM core.store) AS st,
               (SELECT md5(string_agg(product_id || ':' || is_active, ',' ORDER BY product_id))
                  FROM core.product) AS pr
        """,
    )
    return "|".join(str(v) for v in row.values())


def idempotency_key(as_of: date, rule_version: int, fingerprint: str) -> str:
    return hashlib.sha256(f"{as_of}|{rule_version}|{fingerprint}".encode()).hexdigest()


def _offer(row) -> Offer:
    def dec(v):
        return None if v is None or (not isinstance(v, Decimal) and v != v) else D(v)

    return Offer(
        store_id=int(row.store_id),
        product_id=int(row.product_id),
        unit_cost=dec(row.unit_cost),
        current_price=dec(row.current_price),
        is_active=bool(row.is_active),
        cost_invalid=bool(row.cost_invalid),
        in_promo=bool(row.in_promo),
        previous_cost=dec(row.previous_cost),
        cost_valid_from=row.cost_valid_from,
        price_valid_from=row.price_valid_from,
    )


def compute_recommendations(offers, rule_index: RuleIndex):
    """Чистый расчёт без записи в БД: для каждого предложения — правило и рекомендация."""
    results = []
    for row in offers.itertuples(index=False):
        rule: Rule | None = rule_index.select(
            int(row.store_id), int(row.category_id), int(row.product_id)
        )
        offer = _offer(row)
        results.append((row, rule, offer, recommend(offer, rule)))
    return results


def start_run(
    conn: psycopg.Connection,
    as_of: date | None = None,
    actor: str = "аналитик",
    force: bool = False,
    alert_settings: alert_rules.AlertSettings | None = None,
) -> dict:
    settings = alert_settings or alert_rules.AlertSettings()
    period = repo.data_period(conn)
    if period is None:
        raise RunError("В базе нет продаж — сначала загрузите демо-данные")
    as_of = as_of or period[1]
    rule_set = repo.rule_set_for(conn, as_of)
    if rule_set is None:
        raise RunError(f"Нет набора правил, действующего на {as_of:%d.%m.%Y}")
    key = idempotency_key(as_of, rule_set["version"], data_fingerprint(conn))

    existing = fetch_one(
        conn,
        "SELECT run_id FROM calc.pricing_run WHERE idempotency_key = %s AND status = 'done' "
        "ORDER BY finished_at DESC LIMIT 1",
        (key,),
    )
    if existing and not force:
        log.info("Запуск с ключом %s… уже выполнен: %s", key[:8], existing["run_id"])
        return {
            "status": "exists",
            "run_id": existing["run_id"],
            **_summary(conn, existing["run_id"]),
        }

    run_id = uuid.uuid4()
    try:
        with conn.transaction():
            result = _execute_run(conn, run_id, as_of, rule_set, key, actor, force, settings)
    except Exception as exc:
        log.exception("Запуск %s завершился ошибкой", run_id)
        with conn.transaction():
            execute(
                conn,
                "INSERT INTO calc.pricing_run (run_id, as_of_date, rule_set_id, idempotency_key, status, "
                "triggered_by, is_forced, finished_at, error_text) VALUES (%s,%s,%s,%s,'failed',%s,%s,now(),%s)",
                (run_id, as_of, rule_set["rule_set_id"], key, actor, force, str(exc)),
            )
            log_event(
                conn,
                "RUN_FAILED",
                actor,
                f"Расчёт на {as_of:%d.%m.%Y} завершился ошибкой: {exc}",
                run_id=run_id,
                severity="CRIT",
            )
        raise RunError(str(exc)) from exc
    return result


def _execute_run(conn, run_id, as_of, rule_set, key, actor, force, settings) -> dict:
    rules = [rule_from_row(r) for r in repo.rules_of(conn, rule_set["rule_set_id"])]
    index = RuleIndex(rules)
    if not index.has_global():
        raise RunError(f"В наборе правил v{rule_set['version']} нет глобального правила")

    execute(
        conn,
        "INSERT INTO calc.pricing_run (run_id, as_of_date, rule_set_id, idempotency_key, status, triggered_by, "
        "is_forced) VALUES (%s,%s,%s,%s,'running',%s,%s)",
        (run_id, as_of, rule_set["rule_set_id"], key, actor, force),
    )
    execute(
        conn,
        "UPDATE calc.rule_set SET is_locked = true WHERE rule_set_id = %s",
        (rule_set["rule_set_id"],),
    )
    # Новые рекомендации заменяют открытые рекомендации прошлых запусков.
    superseded = execute(
        conn,
        "UPDATE calc.price_recommendation SET status = 'SUPERSEDED' WHERE status = ANY(%s) AND run_id <> %s",
        (list(OPEN_STATUSES), run_id),
    )

    offers = repo.offers_as_of(conn, as_of)
    results = compute_recommendations(offers, index)
    rec_rows = []
    for _row, rule, offer, rec in results:
        change_abs, change_pct = rec.change(offer.current_price)
        details = {**rec.details, "flags": rec.flags}
        # Если последняя версия себестоимости отклонена, актуальная себестоимость неизвестна:
        # старое значение не показываем как текущее, а сохраняем только в деталях.
        stored_cost = offer.unit_cost
        if offer.cost_invalid:
            details["last_valid_cost"] = None if offer.unit_cost is None else str(offer.unit_cost)
            stored_cost = None
        rec_rows.append(
            (
                run_id,
                offer.store_id,
                offer.product_id,
                rule.rule_id if rule else None,
                stored_cost,
                offer.current_price,
                rec.base_price,
                rec.recommended_price,
                change_abs,
                change_pct,
                rec.status.value,
                rec.reason_code,
                rec.reason_text,
                Jsonb(details),
            )
        )
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO calc.price_recommendation (run_id, store_id, product_id, rule_id, unit_cost, current_price, "
            "base_price, recommended_price, change_abs, change_pct, status, reason_code, reason_text, details) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            rec_rows,
        )

    # Предупреждения.
    enriched = offers.copy()
    enriched["status"] = [r[3].status.value for r in results]
    enriched["reason_code"] = [r[3].reason_code for r in results]
    enriched["reason_text"] = [r[3].reason_text for r in results]
    enriched["min_margin"] = [r[1].min_margin if r[1] else None for r in results]
    version = rule_set["version"]
    names = dict(zip(offers["product_id"], offers["product_name"], strict=False))
    candidates = (
        alert_rules.offer_alerts(enriched, version, settings)
        + alert_rules.invalid_cost_alerts(enriched, version)
        + alert_rules.dq_alerts(repo.critical_dq_issues(conn), set(names), version)
        + alert_rules.cost_jump_alerts(repo.cost_changes(conn, as_of), names, version, settings)
        + alert_rules.margin_drop_alerts(repo.margin_windows(conn, as_of), as_of, version, settings)
    )
    created = repeated = 0
    for a in candidates:
        row = fetch_one(
            conn,
            """
            INSERT INTO calc.alert (alert_code, severity, store_id, product_id, run_id, description, dedup_key)
            VALUES (%(alert_code)s, %(severity)s, %(store_id)s, %(product_id)s, %(run_id)s, %(description)s,
                    %(dedup_key)s)
            ON CONFLICT (dedup_key) WHERE status IN ('NEW', 'IN_PROGRESS')
            DO UPDATE SET last_seen_at = now(), occurrences = calc.alert.occurrences + 1,
                          run_id = EXCLUDED.run_id, description = EXCLUDED.description
            RETURNING (xmax = 0) AS inserted
            """,
            {**a, "run_id": run_id},
        )
        if row["inserted"]:
            created += 1
        else:
            repeated += 1

    counts = Counter(r[3].status.value for r in results)
    execute(
        conn,
        "UPDATE calc.pricing_run SET status = 'done', finished_at = now(), offers_total = %s WHERE run_id = %s",
        (len(results), run_id),
    )
    payload = {
        "as_of": as_of.isoformat(),
        "rule_version": version,
        "statuses": dict(counts),
        "alerts_new": created,
        "alerts_repeated": repeated,
        "superseded": superseded,
        "info": {
            "PRICE_CALCULATED": counts.get("PROPOSED", 0),
            "NO_CHANGE_NEEDED": counts.get("NO_CHANGE", 0),
        },
    }
    log_event(
        conn,
        "RUN_COMPLETED",
        actor,
        f"Пересчёт на {as_of:%d.%m.%Y} завершён (правила v{version}{', принудительно' if force else ''}): "
        f"предложено {counts.get('PROPOSED', 0)}, без изменений {counts.get('NO_CHANGE', 0)}, "
        f"конфликтов {counts.get('CONFLICT', 0)}, заблокировано {counts.get('BLOCKED', 0)}, "
        f"отложено {counts.get('ON_HOLD', 0)}; новых предупреждений {created}",
        run_id=run_id,
        payload=payload,
    )
    log.info("Запуск %s завершён: %s", run_id, payload)
    return {
        "status": "done",
        "run_id": run_id,
        "statuses": dict(counts),
        "alerts_new": created,
        "alerts_repeated": repeated,
        "offers_total": len(results),
    }


def _summary(conn, run_id) -> dict:
    rows = fetch_all(
        conn,
        "SELECT status, count(*) AS n FROM calc.price_recommendation WHERE run_id = %s "
        "GROUP BY status",
        (run_id,),
    )
    return {"statuses": {r["status"]: r["n"] for r in rows}}


# --- Решения пользователя ---------------------------------------------------------------------


def decide(
    conn: psycopg.Connection,
    recommendation_ids: list[int],
    approve: bool,
    actor: str,
    manual_price: Decimal | None = None,
    comment: str | None = None,
) -> int:
    """Согласовать или отклонить рекомендации (FR-10). Цены во внешних системах не меняются (BR-12).

    Для CONFLICT согласование возможно только с ценой, введённой вручную.
    """
    if not recommendation_ids:
        return 0
    with conn.transaction():
        recs = fetch_all(
            conn,
            "SELECT recommendation_id, status, current_price, store_id, product_id FROM calc.price_recommendation "
            "WHERE recommendation_id = ANY(%s) FOR UPDATE",
            (recommendation_ids,),
        )
        done = 0
        for r in recs:
            if r["status"] not in ("PROPOSED", "CONFLICT"):
                continue
            new_status = "APPROVED" if approve else "REJECTED"
            price_sql, params = "", []
            if approve and r["status"] == "CONFLICT":
                if manual_price is None or manual_price <= 0:
                    raise ValueError("Для конфликта нужно указать цену вручную (больше нуля)")
                diff = manual_price - r["current_price"] if r["current_price"] else None
                pct = (
                    (diff / r["current_price"]).quantize(Decimal("0.0001"))
                    if diff is not None
                    else None
                )
                price_sql = ", recommended_price = %s, change_abs = %s, change_pct = %s"
                params = [manual_price, diff, pct]
            execute(
                conn,
                f"UPDATE calc.price_recommendation SET status = %s, decided_by = %s, decided_at = now(), "
                f"decision_comment = %s{price_sql} WHERE recommendation_id = %s",
                [new_status, actor, comment, *params, r["recommendation_id"]],
            )
            log_event(
                conn,
                "RECOMMENDATION_APPROVED" if approve else "RECOMMENDATION_REJECTED",
                actor,
                f"Рекомендация #{r['recommendation_id']} (точка {r['store_id']}, товар {r['product_id']}) "
                f"{'согласована' if approve else 'отклонена'}"
                + (f" с ценой вручную {manual_price} ₽" if price_sql else "")
                + (f". Комментарий: {comment}" if comment else ""),
                payload={
                    "recommendation_id": r["recommendation_id"],
                    "previous_status": r["status"],
                },
            )
            done += 1
    return done


def set_alert_status(conn: psycopg.Connection, alert_id: int, status: str, actor: str) -> None:
    if status not in ("NEW", "IN_PROGRESS", "RESOLVED", "DISMISSED"):
        raise ValueError("Недопустимый статус предупреждения")
    with conn.transaction():
        row = fetch_one(
            conn,
            "SELECT status, alert_code FROM calc.alert WHERE alert_id = %s FOR UPDATE",
            (alert_id,),
        )
        if row is None:
            raise ValueError("Предупреждение не найдено")
        if row["status"] == status:
            return
        execute(
            conn,
            "UPDATE calc.alert SET status = %s, status_changed_by = %s, status_changed_at = now() "
            "WHERE alert_id = %s",
            (status, actor, alert_id),
        )
        log_event(
            conn,
            "ALERT_STATUS_CHANGED",
            actor,
            f"Предупреждение #{alert_id} ({row['alert_code']}): {row['status']} → {status}",
            payload={"alert_id": alert_id},
        )


def create_rule_set(
    conn: psycopg.Connection,
    rules: list[Rule],
    actor: str,
    effective_from: date,
    comment: str | None = None,
) -> int:
    """Новая версия набора правил (BR-09). Возвращает номер версии."""
    errors = [
        f"{r.scope_type}{'' if r.scope_id is None else ' ' + str(r.scope_id)}: {e}"
        for r in rules
        for e in r.errors()
    ]
    if not any(r.scope_type == "global" for r in rules):
        errors.append("В наборе должно быть глобальное правило")
    if errors:
        raise ValueError("; ".join(errors))
    with conn.transaction():
        version = fetch_one(conn, "SELECT COALESCE(max(version), 0) + 1 AS v FROM calc.rule_set")[
            "v"
        ]
        rs = fetch_one(
            conn,
            "INSERT INTO calc.rule_set (version, effective_from, created_by, comment) VALUES (%s,%s,%s,%s) "
            "RETURNING rule_set_id",
            (version, effective_from, actor, comment),
        )
        for r in rules:
            execute(
                conn,
                "INSERT INTO calc.pricing_rule (rule_set_id, scope_type, scope_id, target_margin, min_margin, "
                "max_change, rounding_step, rounding_mode, allow_decrease) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    rs["rule_set_id"],
                    r.scope_type,
                    r.scope_id,
                    r.target_margin,
                    r.min_margin,
                    r.max_change,
                    r.rounding_step,
                    r.rounding_mode.value,
                    r.allow_decrease,
                ),
            )
        log_event(
            conn,
            "RULE_SET_CREATED",
            actor,
            f"Создана версия правил v{version} (действует с {effective_from:%d.%m.%Y}, правил: {len(rules)})"
            + (f". {comment}" if comment else ""),
            payload={"version": version, "rules": len(rules)},
        )
    return version
