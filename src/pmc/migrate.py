"""Раннер SQL-миграций (ADR-04).

Как работает:
1. Файлы migrations/NNN_name.sql применяются по порядку номеров.
2. Применённые файлы записываются в audit.schema_migration вместе с контрольной суммой.
3. Если уже применённый файл изменили, раннер останавливается с ошибкой:
   менять историю нельзя, нужно добавить новую миграцию.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import psycopg

from pmc.config import MIGRATIONS_DIR

log = logging.getLogger(__name__)

_BOOTSTRAP = """
CREATE SCHEMA IF NOT EXISTS audit;
CREATE TABLE IF NOT EXISTS audit.schema_migration (
    filename   varchar(200) PRIMARY KEY,
    checksum   char(64)     NOT NULL,
    applied_at timestamptz  NOT NULL DEFAULT now()
);
"""


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    return sorted(p for p in directory.glob("*.sql") if p.name[:3].isdigit())


def migrate(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Применяет недостающие миграции. Возвращает список применённых файлов."""
    applied_now: list[str] = []
    with conn.transaction():
        conn.execute(_BOOTSTRAP)
        # Блокировка, чтобы два процесса не применяли миграции одновременно.
        conn.execute("SELECT pg_advisory_xact_lock(20261105)")
        done = {
            r["filename"]: r["checksum"]
            for r in conn.execute(
                "SELECT filename, checksum FROM audit.schema_migration"
            ).fetchall()
        }
        for path in migration_files(directory):
            checksum = _checksum(path)
            if path.name in done:
                if done[path.name] != checksum:
                    raise RuntimeError(
                        f"Миграция {path.name} уже применена, но файл изменён. "
                        "Создайте новую миграцию вместо правки старой."
                    )
                continue
            log.info("Применяю миграцию %s", path.name)
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO audit.schema_migration (filename, checksum) VALUES (%s, %s)",
                (path.name, checksum),
            )
            applied_now.append(path.name)
    if not applied_now:
        log.info("Схема БД актуальна, новых миграций нет")
    return applied_now
