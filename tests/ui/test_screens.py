"""Проверка экранов через Streamlit AppTest (TC-24): каждый экран открывается без исключений
для каждой роли, ключевые элементы на месте."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

pytestmark = pytest.mark.ui

APP = Path(__file__).resolve().parents[2] / "app"
VIEWS = ["overview", "prices", "alerts", "scenarios", "history", "export"]
ROLES = ["Аналитик", "Руководитель сети", "Операционный менеджер"]


def run_view(view: str, role: str) -> AppTest:
    script = f"""
import sys
sys.path.insert(0, {str(APP)!r})
import streamlit as st
st.session_state.setdefault("role", {role!r})
st.session_state.setdefault("my_store", 3)
exec(open({str(APP / "views" / (view + ".py"))!r}, encoding="utf-8").read())
"""
    at = AppTest.from_string(script, default_timeout=120)
    at.run()
    return at


@pytest.fixture(scope="module", autouse=True)
def _run_once(loaded_db):
    from pmc.db import connect
    from pmc.run_service import start_run

    with connect(loaded_db) as c:
        start_run(c, actor="тест")


@pytest.mark.parametrize("view", VIEWS)
@pytest.mark.parametrize("role", ROLES)
def test_view_renders_without_errors(view, role):
    at = run_view(view, role)
    assert not at.exception, [e.value for e in at.exception]
    assert any("синтетические" in m.value for m in at.markdown), (
        "нет пометки о синтетических данных"
    )


def test_overview_has_six_cards():
    at = run_view("overview", "Аналитик")
    labels = [m.label for m in at.metric]
    assert labels == [
        "Валовая маржинальность",
        "Выручка",
        "Валовая прибыль",
        "Предложено изменить цен",
        "Нужно решение вручную",
        "Проблемы данных",
    ]
    assert all(m.value != "нет данных" for m in at.metric)


def test_overview_shows_attention_block_and_priority_table():
    at = run_view("overview", "Аналитик")
    assert any("Что требует внимания" in m.value for m in at.markdown)
    assert any("Критично" in m.value for m in at.markdown)
    assert len(at.dataframe) >= 1


def test_tables_show_no_data_instead_of_none():
    at = run_view("prices", "Аналитик")
    df = at.dataframe[0].value
    assert not df.isna().any().any(), "пустые значения должны быть подписаны"
    assert not (df.astype(str) == "None").any().any()
    assert (df == "нет данных").any().any()  # у заблокированных позиций нет маржи — подписано


def test_prices_buttons_depend_on_role():
    analyst = run_view("prices", "Аналитик")
    head = run_view("prices", "Руководитель сети")
    run_btn = lambda at: next(b for b in at.button if b.label == "Рассчитать")  # noqa: E731
    assert not run_btn(analyst).disabled and run_btn(head).disabled


def test_prices_filter_by_status():
    at = run_view("prices", "Аналитик")
    status = next(m for m in at.multiselect if m.label == "Статус")
    status.set_value(["CONFLICT"]).run()
    assert not at.exception
    assert any("Показано" in c.value for c in at.caption)


def test_scenario_shows_assumptions_and_validates_input():
    at = run_view("scenarios", "Аналитик")
    assert any("Допущения" in m.value and "не прогноз" in m.value for m in at.markdown)
    at.slider[1].set_value(80).run()  # минимальная маржа выше целевой
    assert any("не может быть больше" in e.value for e in at.error)


def test_export_offers_download():
    at = run_view("export", "Аналитик")
    assert not at.exception
    assert len(at.get("download_button")) == 1
