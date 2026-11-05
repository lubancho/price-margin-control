"""Командная строка: pmc <команда>.

pmc check-db                 проверить подключение к PostgreSQL
pmc migrate                  применить миграции
pmc generate --out data/     записать демо-данные в CSV (для просмотра)
pmc load [--reset]           сгенерировать и загрузить демо-данные в БД
pmc init-demo                migrate + load, если данных ещё нет (используется в Docker)
pmc run-pricing [--force]    выполнить расчёт рекомендуемых цен
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date
from pathlib import Path

from pmc.config import DEMO_SEED, get_settings
from pmc.logging_setup import setup_logging

log = logging.getLogger("pmc.cli")


def _wait_for_db(attempts: int = 30) -> str:
    from pmc.db import check_connection

    for i in range(1, attempts + 1):
        try:
            return check_connection()
        except Exception as exc:
            log.warning("БД недоступна (попытка %s/%s): %s", i, attempts, exc)
            time.sleep(2)
    raise SystemExit("Не удалось подключиться к PostgreSQL")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pmc", description="Контроль цен и маржинальности (демо)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check-db")
    sub.add_parser("migrate")
    g = sub.add_parser("generate")
    g.add_argument("--out", type=Path, default=Path("data"))
    g.add_argument("--seed", type=int, default=DEMO_SEED)
    ld = sub.add_parser("load")
    ld.add_argument("--reset", action="store_true", help="очистить данные перед загрузкой")
    ld.add_argument("--seed", type=int, default=DEMO_SEED)
    sub.add_parser("init-demo")
    rp = sub.add_parser("run-pricing")
    rp.add_argument("--as-of", type=date.fromisoformat, default=None)
    rp.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    try:
        setup_logging(get_settings().log_level)
    except RuntimeError:
        setup_logging("INFO")

    if args.cmd == "generate":
        from pmc.generator import build_demo, save_csv

        files = save_csv(build_demo(args.seed), args.out)
        log.info("Записано файлов: %s в %s", len(files), args.out)
        return 0

    from pmc.db import connect

    if args.cmd == "check-db":
        log.info("Подключение есть: %s", _wait_for_db())
        return 0
    if args.cmd == "migrate":
        from pmc.migrate import migrate

        _wait_for_db()
        with connect() as conn:
            migrate(conn)
        return 0
    if args.cmd in ("load", "init-demo"):
        from pmc.generator import build_demo
        from pmc.loader import is_loaded, load_demo, reset
        from pmc.migrate import migrate

        _wait_for_db()
        with connect() as conn:
            if args.cmd == "init-demo":
                migrate(conn)
            if is_loaded(conn):
                if args.cmd == "init-demo" or not args.reset:
                    log.info(
                        "Данные уже загружены — пропускаю (для перезагрузки: pmc load --reset)"
                    )
                    return 0
                reset(conn)
                conn.commit()
            seed = getattr(args, "seed", DEMO_SEED)
            load_demo(conn, build_demo(seed))
            if args.cmd == "init-demo":
                from pmc.run_service import start_run

                start_run(conn, as_of=None, actor="система")
        return 0
    if args.cmd == "run-pricing":
        from pmc.run_service import start_run

        with connect() as conn:
            result = start_run(conn, as_of=args.as_of, actor="аналитик (CLI)", force=args.force)
        log.info("Результат: %s", result)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
