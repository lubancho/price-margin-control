"""Общие элементы интерфейса: подключение, роль, форматирование, карточки, оформление графиков.

Интерфейс только показывает данные и передаёт команды в модули pmc — расчётов здесь нет (ADR-05).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import psycopg
import streamlit as st

from pmc import repository as repo
from pmc.config import DEMO_NOTE
from pmc.db import connect

ROLES = ("Аналитик", "Руководитель сети", "Операционный менеджер")

PALETTE = {
    "accent": "#3E5C76",
    "accent2": "#7F9BB3",
    "crit": "#B3261E",
    "warn": "#B26A00",
    "ok": "#2E7D32",
    "info": "#5B6B7A",
    "grid": "#E1E6EB",
    "muted": "#8A97A3",
}
SEVERITY_LABEL = {"CRIT": "Критично", "WARN": "Предупреждение"}
STATUS_LABEL = {
    "PROPOSED": "Предложено",
    "NO_CHANGE": "Без изменений",
    "CONFLICT": "Конфликт",
    "BLOCKED": "Заблокировано",
    "ON_HOLD": "Отложено (промо)",
    "APPROVED": "Согласовано",
    "REJECTED": "Отклонено",
    "SUPERSEDED": "Заменено",
}
ALERT_STATUS_LABEL = {
    "NEW": "Новое",
    "IN_PROGRESS": "В работе",
    "RESOLVED": "Решено",
    "DISMISSED": "Отклонено",
}

pio.templates["pmc"] = go.layout.Template(
    layout=go.Layout(
        font={"family": "Inter, Segoe UI, sans-serif", "size": 13, "color": "#1F2933"},
        colorway=[PALETTE["accent"], PALETTE["accent2"], "#A3B8C9", PALETTE["warn"], PALETTE["ok"]],
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis={"gridcolor": PALETTE["grid"], "zeroline": False},
        yaxis={"gridcolor": PALETTE["grid"], "zeroline": False},
        margin={"l": 10, "r": 10, "t": 40, "b": 10},
        legend={"orientation": "h", "y": -0.18},
        hoverlabel={"bgcolor": "white"},
        separators=", ",
    )
)
pio.templates.default = "plotly_white+pmc"

_CSS = """
<style>
.block-container {padding-top: 1.6rem; padding-bottom: 1rem; max-width: 1500px;}
[data-testid="stMetric"] {background: #FFFFFF; border: 1px solid #E1E6EB; border-radius: 8px;
  padding: 10px 14px; min-height: 108px;}
[data-testid="stMetricLabel"] p {font-size: 0.82rem; color: #5B6B7A;}
[data-testid="stMetricValue"] {font-size: 1.55rem;}
.pmc-footer {color: #8A97A3; font-size: 0.78rem; border-top: 1px solid #E1E6EB; margin-top: 1.5rem;
  padding-top: 0.4rem;}
.pmc-box {background: #FFFFFF; border: 1px solid #E1E6EB; border-left: 4px solid #3E5C76;
  border-radius: 6px; padding: 10px 14px; margin: 4px 0 10px 0;}
.pmc-box.warn {border-left-color: #B26A00;}
.pmc-role {font-size: 0.75rem; color: #8A97A3;}
</style>
"""


# --- Подключение --------------------------------------------------------------------------------


@st.cache_resource(show_spinner=False)
def _connection() -> psycopg.Connection:
    return connect()


def get_conn() -> psycopg.Connection:
    conn = _connection()
    if conn.closed or conn.broken:
        _connection.clear()
        conn = _connection()
    return conn


def refresh_data() -> None:
    """Сбросить кэш после изменения данных (расчёт, решения, правила)."""
    st.cache_data.clear()


# --- Каркас страницы ---------------------------------------------------------------------------


def page_header(title: str, subtitle: str | None = None) -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
    st.title(title)
    if subtitle:
        st.caption(subtitle)


def footer() -> None:
    st.markdown(
        f"<div class='pmc-footer'>{DEMO_NOTE} Учебно-практический проект.</div>",
        unsafe_allow_html=True,
    )


def sidebar_role() -> str:
    with st.sidebar:
        role = st.selectbox("Роль", ROLES, key="role")
        st.markdown(
            "<div class='pmc-role'>⚠ Выбор роли только меняет набор кнопок и не является "
            "защищённым разграничением доступа.</div>",
            unsafe_allow_html=True,
        )
        if role == "Операционный менеджер":
            stores = cached_stores()
            st.selectbox(
                "Моя точка",
                stores["store_id"],
                key="my_store",
                format_func=dict(zip(stores["store_id"], stores["name"], strict=True)).get,
            )
        period = cached_period()
        if period:
            st.caption(f"Данные: {period[0]:%d.%m.%Y} — {period[1]:%d.%m.%Y}")
    return role


def my_store() -> int | None:
    return (
        st.session_state.get("my_store")
        if st.session_state.get("role") == "Операционный менеджер"
        else None
    )


def can(action: str) -> bool:
    role = st.session_state.get("role", ROLES[0])
    rights = {
        "run": ("Аналитик",),
        "rules": ("Аналитик",),
        "decide": ("Руководитель сети",),
        "alert_status": ROLES,
    }
    return role in rights[action]


def box(text: str, kind: str = "") -> None:
    st.markdown(f"<div class='pmc-box {kind}'>{text}</div>", unsafe_allow_html=True)


# --- Кэшированные данные -----------------------------------------------------------------------


@st.cache_data(show_spinner=False, ttl=600)
def cached_period() -> tuple[date, date] | None:
    return repo.data_period(get_conn())


@st.cache_data(show_spinner=False, ttl=600)
def cached_stores() -> pd.DataFrame:
    return repo.stores(get_conn())


@st.cache_data(show_spinner=False, ttl=600)
def cached_categories() -> pd.DataFrame:
    return repo.categories(get_conn())


@st.cache_data(show_spinner=False, ttl=600)
def cached_products() -> pd.DataFrame:
    return repo.products(get_conn())


def period_filter(key: str, default_days: int = 28) -> tuple[date, date]:
    period = cached_period()
    if period is None:
        st.warning("В базе нет данных. Выполните загрузку: `pmc init-demo`.")
        footer()
        st.stop()
    d0, d1 = period
    value = st.date_input(
        "Период",
        (max(d0, d1 - timedelta(days=default_days - 1)), d1),
        min_value=d0,
        max_value=d1,
        key=key,
        format="DD.MM.YYYY",
    )
    if isinstance(value, tuple) and len(value) == 2:
        return value[0], value[1]
    st.info("Выберите конечную дату периода")
    st.stop()


# --- Форматирование ----------------------------------------------------------------------------


def _num(x) -> float | None:
    if x is None:
        return None
    if isinstance(x, Decimal):
        return float(x)
    try:
        return None if pd.isna(x) else float(x)
    except TypeError:
        return None


def fmt_rub(x, digits: int = 0) -> str:
    v = _num(x)
    if v is None:
        return "н/д"
    if abs(v) >= 1_000_000:
        return f"{v / 1_000_000:,.1f} млн ₽".replace(",", " ").replace(".", ",")
    return f"{v:,.{digits}f} ₽".replace(",", " ").replace(".", ",")


def fmt_pct(x, digits: int = 1, signed: bool = False) -> str:
    v = _num(x)
    if v is None:
        return "н/д"
    s = f"{v * 100:+.{digits}f}%" if signed else f"{v * 100:.{digits}f}%"
    return s.replace(".", ",")


def fmt_pp(x, digits: int = 1) -> str:
    """Разница долей в процентных пунктах: 0,018 → «+1,8 п. п.»."""
    v = _num(x)
    return "н/д" if v is None else f"{v * 100:+.{digits}f}".replace(".", ",") + " п. п."


def fmt_int(x) -> str:
    v = _num(x)
    return "н/д" if v is None else f"{v:,.0f}".replace(",", " ")


def to_display(df: pd.DataFrame) -> pd.DataFrame:
    """Decimal → float для таблиц и графиков (только слой отображения)."""
    out = df.copy()
    for c in out.columns:
        if out[c].dtype == object and out[c].map(lambda v: isinstance(v, Decimal)).any():
            out[c] = out[c].map(lambda v: float(v) if isinstance(v, Decimal) else v).astype(float)
    return out
