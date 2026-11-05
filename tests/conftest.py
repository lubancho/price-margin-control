"""Общие фикстуры.

Интеграционные тесты работают с отдельной базой из TEST_DATABASE_URL.
Чтобы случайно не стереть рабочие данные, имя базы обязано заканчиваться на «_test».
"""

from __future__ import annotations

import os

import pytest

from pmc.config import DEMO_SEED


def _test_url() -> str | None:
    return os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(scope="session")
def db_url() -> str:
    url = _test_url()
    if not url:
        pytest.skip("TEST_DATABASE_URL не задан — интеграционные тесты пропущены")
    if not url.rsplit("/", 1)[-1].split("?")[0].endswith("_test"):
        pytest.fail("Имя тестовой базы должно заканчиваться на _test (защита от удаления данных)")
    os.environ["DATABASE_URL"] = url  # для кода, который сам читает настройки (экраны Streamlit)
    return url


@pytest.fixture(scope="session")
def demo_data():
    from pmc.generator import build_demo

    return build_demo(DEMO_SEED)


@pytest.fixture(scope="session")
def loaded_db(db_url, demo_data) -> str:
    """Чистая схема + миграции + демо-данные. Выполняется один раз за сессию."""
    from pmc.db import connect
    from pmc.loader import load_demo
    from pmc.migrate import migrate

    with connect(db_url) as conn:
        conn.execute("DROP SCHEMA IF EXISTS raw, core, calc, audit CASCADE")
        migrate(conn)
        load_demo(conn, demo_data)
    return db_url


@pytest.fixture
def conn(loaded_db):
    from pmc.db import connect

    c = connect(loaded_db)
    yield c
    c.close()
