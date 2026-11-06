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
from streamlit.errors import StreamlitPageNotFoundError

from pmc import repository as repo
from pmc.config import DEMO_NOTE
from pmc.db import connect

ROLES = ("Аналитик", "Руководитель сети", "Операционный менеджер")

# Единая дизайн-система с демо-версией (docs/08_ux_redesign.md): фиолетовый — акцент и выбранное,
# красный / жёлтый / зелёный — только статусы и оценка «лучше / хуже для бизнеса».
PALETTE = {
    "accent": "#6A52EE",
    "accent2": "#A594FF",
    "accent3": "#D3C9FF",
    "crit": "#D93A52",
    "warn": "#E9A800",
    "ok": "#1FAE5B",
    "info": "#5D5590",
    "grid": "#E3DDFB",
    "muted": "#6C6599",
}
NA = "нет данных"
SEVERITY_LABEL = {"CRIT": "Критично", "WARN": "Требует внимания"}
STATUS_LABEL = {
    "PROPOSED": "Изменить цену",
    "NO_CHANGE": "Цена в норме",
    "CONFLICT": "Нужно решение вручную",
    "BLOCKED": "Не рассчитано: ошибка данных",
    "ON_HOLD": "Отложено: промоакция",
    "APPROVED": "Согласовано",
    "REJECTED": "Отклонено",
    "SUPERSEDED": "Заменено новым расчётом",
}
STATUS_HELP = {
    "PROPOSED": "Система рассчитала новую цену по правилам маржи. Решение принимает руководитель.",
    "NO_CHANGE": "Текущая цена соответствует правилам — менять не нужно.",
    "CONFLICT": "Правила противоречат друг другу: допустимой цены нет. Цену задаёт руководитель.",
    "BLOCKED": "В исходных данных ошибка — цену рассчитать нельзя.",
    "ON_HOLD": "Товар в промоакции — изменение перенесено до окончания акции.",
    "APPROVED": "Руководитель согласовал цену. Система не меняет цены в кассе.",
    "REJECTED": "Руководитель отклонил рекомендацию.",
    "SUPERSEDED": "Рекомендация заменена более новым расчётом.",
}
DQ_LABEL = {
    "DUPLICATE": "Дубли строк — исключены автоматически",
    "FK_VIOLATION": "Ссылка на несуществующую точку или товар",
    "DISCOUNT_OUT_OF_RANGE": "Скидка вне допустимого диапазона",
    "NON_POSITIVE_COST": "Себестоимость ≤ 0",
    "NON_POSITIVE_PRICE": "Цена ≤ 0",
    "MISSING_PRICE": "Нет цены",
    "MISSING_VALUE": "Пустое обязательное поле",
    "OUT_OF_RANGE": "Значение вне диапазона",
}
HELP = {
    "margin": "Валовая маржинальность = (выручка − себестоимость проданного) / выручка. "
    "Считается по продажам с известной себестоимостью на дату продажи.",
    "revenue": "Выручка = продажи за вычетом возвратов и скидок, без отменённых чеков.",
    "gp": "Валовая прибыль = выручка − себестоимость проданного (без аренды, зарплат и прочих расходов).",
    "proposed": "Позиции, для которых система рассчитала новую цену. Цены в кассе система не меняет.",
    "conflict": "Минимальная маржа требует цену выше лимита изменения — нужна цена вручную.",
    "dq": "Позиции, по которым цену рассчитать нельзя из-за ошибок данных.",
    "plan_margin": "Маржа при текущей цене и себестоимости на дату расчёта.",
    "after_margin": "Маржа при рекомендуемой цене и той же себестоимости.",
}
ALERT_STATUS_LABEL = {
    "NEW": "Новое",
    "IN_PROGRESS": "В работе",
    "RESOLVED": "Решено",
    "DISMISSED": "Отклонено",
}

pio.templates["pmc"] = go.layout.Template(
    layout=go.Layout(
        font={"family": "Manrope, Segoe UI, sans-serif", "size": 13, "color": "#1E1745"},
        colorway=[
            PALETTE["accent"],
            PALETTE["accent2"],
            "#EC6A9C",
            "#F2A43A",
            "#2FB9A5",
            "#4F8CF0",
        ],
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
.block-container {padding-top: 4rem; padding-bottom: 1rem; max-width: 1500px;}
h1 {letter-spacing: -0.02em;}
[data-testid="stSidebar"] {background: #4536A8;}
[data-testid="stSidebarNav"] span, [data-testid="stSidebar"] .stMarkdown p,
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p, [data-testid="stSidebar"] [data-testid="stCaptionContainer"],
[data-testid="stSidebar"] .pmc-role, [data-testid="stSidebar"] strong {color: #FFFFFF !important;}
[data-testid="stSidebarNav"] a[aria-current="page"] {background: rgba(255,255,255,.18);}
[data-testid="stMetric"] {background: #FFFFFF; border: 1px solid #E3DDFB; border-radius: 16px;
  padding: 12px 16px; min-height: 112px; box-shadow: 0 8px 24px rgba(70,50,170,.07);}
[data-testid="stMetricLabel"] p {font-size: 0.8rem; color: #544C86; font-weight: 700;}
[data-testid="stMetricValue"] {font-size: 1.6rem; font-weight: 800;}
.ux-lvl {display:flex; gap:10px; align-items:center; margin:1.4rem 0 .4rem;}
.ux-lvl b {font-size:1.15rem;}
.ux-lv {font-size:.68rem; font-weight:800; letter-spacing:.1em; text-transform:uppercase; color:#6A52EE;
  background:#EFEBFF; padding:2px 9px; border-radius:999px;}
.ux-attn {background:#FFFFFF; border:1px solid #E3DDFB; border-left:5px solid var(--ac,#8B75FF);
  border-radius:12px; padding:10px 14px; margin:0 0 4px;}
.ux-attn .t {font-weight:800;} .ux-attn .w {font-size:.88rem; color:#2E2660;}
.ux-pill {display:inline-block; padding:1px 9px; border-radius:999px; font-size:.72rem; font-weight:800;
  margin-right:6px;}
.ux-pill.crit {background:#D93A52; color:#fff;} .ux-pill.warn {background:#FCD34D; color:#4A3500;}
.ux-pill.info {background:#EFEBFF; color:#6A52EE;} .ux-pill.ok {background:#4ADE80; color:#073B1D;}
.ux-scope {background:#FFF4D4; border:1px solid #F0CF6A; border-radius:10px; padding:6px 12px;
  font-size:.86rem; margin:4px 0 10px;}
.pmc-footer {color: #6C6599; font-size: 0.78rem; border-top: 1px solid #E3DDFB; margin-top: 1.5rem;
  padding-top: 0.4rem;}
.pmc-box {background: #FFFFFF; border: 1px solid #E3DDFB; border-left: 4px solid #6A52EE;
  border-radius: 10px; padding: 10px 14px; margin: 4px 0 10px 0;}
.pmc-box.warn {border-left-color: #E9A800; background:#FFFBEA;}
.pmc-box.crit {border-left-color: #D93A52; background:#FDF0F2;}
.pmc-role {font-size: 0.75rem; opacity: .85;}
.ux-sep{color:#8a83b8;font-weight:700}
.ux-here{font-weight:700;color:#1e1745;font-size:14px}
[data-testid="stPageLink"] a{padding:0 !important}
[data-testid="stPageLink"] p{font-weight:700;color:#6a52ee;font-size:14px}
[data-testid="stVerticalBlockBorderWrapper"]:has([data-testid="stMetric"]) button[kind="tertiary"] p{color:#6a52ee;font-weight:800}
[class*="st-key-tile_"] {background:#FFFFFF;border-radius:18px !important;border-top:4px solid #8b75ff !important;box-shadow:0 10px 30px rgba(69,54,168,.08);}
[class*="st-key-tile_"] [data-testid="stMetric"] {border:0 !important;box-shadow:none !important;background:transparent !important;padding:0 !important;}
[class*="st-key-tile_"] button p {color:#6a52ee;font-weight:800}
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


def role_bar() -> str:
    """Роль — в верхней строке страницы: в боковой панели только разделы."""
    c1, c2, c3 = st.columns([2, 2, 3], vertical_alignment="bottom")
    role = c1.selectbox(
        "Роль",
        ROLES,
        key="role",
        help="Выбор роли только меняет набор кнопок и не является защищённым "
        "разграничением доступа.",
    )
    if role == "Операционный менеджер":
        stores = cached_stores()
        c2.selectbox(
            "Моя точка",
            stores["store_id"],
            key="my_store",
            format_func=dict(zip(stores["store_id"], stores["name"], strict=True)).get,
        )
    period = cached_period()
    note = "Роль меняет только набор кнопок — это не защита доступа."
    if period:
        note += f" Данные: {period[0]:%d.%m.%Y} — {period[1]:%d.%m.%Y}."
    c3.caption(note)
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


LEVELS = {"crit": "Критично", "warn": "Требует внимания", "info": "Информация"}
_AC = {"crit": "#D93A52", "warn": "#E9A800", "info": "#8B75FF"}


def level(tag: str, title: str, hint: str | None = None) -> None:
    """Заголовок уровня иерархии: «Состояние», «Внимание», «Подробно», «Методика»."""
    st.markdown(
        f"<div class='ux-lvl'><span class='ux-lv'>{tag}</span><b>{title}</b></div>",
        unsafe_allow_html=True,
    )
    if hint:
        st.caption(hint)


def scope_note(text: str) -> None:
    """Плашка об области действия показателя (например, «рассчитано по всей сети»)."""
    st.markdown(f"<div class='ux-scope'>ⓘ {text}</div>", unsafe_allow_html=True)


def attention(items: list[dict], key: str) -> None:
    """Блок «Что требует внимания»: уровень, вывод, объяснение, число и переход к деталям.

    items: {lvl: crit|warn|info, title, why, value, page?, state?: dict}
    """
    if not items:
        st.success("Проблем, требующих внимания, не найдено.")
        return
    for i, it in enumerate(items):
        c1, c2 = st.columns([8.6, 1.4], vertical_alignment="center")
        c1.markdown(
            f"<div class='ux-attn' style='--ac:{_AC[it['lvl']]}'>"
            f"<span class='ux-pill {it['lvl']}'>{LEVELS[it['lvl']]}</span>"
            f"<span class='t'>{it['title']}</span> · <b>{it['value']}</b>"
            f"<div class='w'>{it['why']}</div></div>",
            unsafe_allow_html=True,
        )
        if it.get("page") and c2.button("Открыть →", key=f"{key}_{i}", width="stretch"):
            for k, v in (it.get("state") or {}).items():
                st.session_state[k] = v
            st.switch_page(it["page"])


def styled(df: pd.DataFrame, formats: dict[str, str]) -> pd.DataFrame:
    """Таблица в русском формате; пустые значения — «нет данных», а не None и не ноль.

    formats: колонка → rub | rub2 | pct | spct | pp | int | num1. Отформатированные колонки
    становятся текстом (Streamlit не умеет подписывать пустые числовые ячейки), поэтому
    выравнивание по правому краю задаёт num_cfg().
    """
    fn = {
        "rub": lambda v: fmt_rub(v, full=True),
        "rub2": lambda v: fmt_rub(v, 2, full=True),
        "pct": fmt_pct,
        "spct": lambda v: fmt_pct(v, signed=True),
        "pp": fmt_pp,
        "int": fmt_int,
        "num1": lambda v: fmt_num(v, 1),
    }
    view = df.copy()
    for c, k in formats.items():
        if c in view.columns and k in fn:
            view[c] = view[c].map(fn[k])
    for c in view.columns:
        if view[c].dtype == object:
            view[c] = view[c].map(
                lambda v: NA if v is None or (isinstance(v, float) and pd.isna(v)) else v
            )
    return view


def num_cfg(formats: dict[str, str]) -> dict:
    """Выравнивание чисел по правому краю для колонок, отформатированных styled()."""
    return {c: st.column_config.TextColumn(alignment="right") for c in formats}


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


def fmt_rub(x, digits: int = 0, full: bool = False) -> str:
    v = _num(x)
    if v is None:
        return NA
    if abs(v) >= 1_000_000 and not full:
        return f"{v / 1_000_000:,.1f} млн ₽".replace(",", " ").replace(".", ",")
    return f"{v:,.{digits}f} ₽".replace(",", " ").replace(".", ",")


def fmt_pct(x, digits: int = 1, signed: bool = False) -> str:
    v = _num(x)
    if v is None:
        return NA
    s = f"{v * 100:+.{digits}f}%" if signed else f"{v * 100:.{digits}f}%"
    return s.replace(".", ",")


def fmt_pp(x, digits: int = 1) -> str:
    """Разница долей в процентных пунктах: 0,018 → «+1,8 п. п.»."""
    v = _num(x)
    # ASCII-минус: по нему st.metric определяет направление и цвет изменения
    return NA if v is None else f"{v * 100:+.{digits}f}".replace(".", ",") + " п. п."


def fmt_int(x) -> str:
    v = _num(x)
    return NA if v is None else f"{v:,.0f}".replace(",", " ")


def fmt_num(x, digits: int = 1) -> str:
    v = _num(x)
    return NA if v is None else f"{v:,.{digits}f}".replace(",", " ").replace(".", ",")


def to_display(df: pd.DataFrame) -> pd.DataFrame:
    """Decimal → float для таблиц и графиков (только слой отображения)."""
    out = df.copy()
    for c in out.columns:
        if out[c].dtype == object and out[c].map(lambda v: isinstance(v, Decimal)).any():
            out[c] = out[c].map(lambda v: float(v) if isinstance(v, Decimal) else v).astype(float)
    return out


# --- Путь вглубь: сводка → разбор → карточка (без меню в боковой панели) -----------------------
def door(
    col,
    label: str,
    value: str,
    *,
    delta: str | None = None,
    note: str = "",
    action: str = "Открыть",
    page: str | None = None,
    state: dict | None = None,
    key: str,
    help: str | None = None,
    delta_color: str = "normal",
) -> None:
    """Плитка сводки — «дверь» в разбор показателя: число, сравнение и переход на следующий уровень."""
    with col.container(border=True, key=f"tile_{key}"):
        st.metric(label, value, delta, help=help, delta_color=delta_color)
        st.caption(note or " ")
        if page and st.button(f"{action} ›", key=key, type="tertiary"):
            for k, v in (state or {}).items():
                st.session_state[k] = v
            st.switch_page(page)


def link(page: str, label: str) -> None:
    """Ссылка на страницу; вне приложения (например, в тестах отдельной страницы) — просто текст."""
    try:
        st.page_link(page, label=label)
    except StreamlitPageNotFoundError:
        st.markdown(label)


def crumbs(*trail: tuple[str, str | None]) -> None:
    """«Хлебные крошки»: где находится пользователь и как вернуться на уровень выше."""
    with st.container(horizontal=True, gap="small", vertical_alignment="center"):
        for label, page in trail:
            if page:
                link(page, label)
                st.markdown("<span class='ux-sep'>›</span>", unsafe_allow_html=True)
            else:
                st.markdown(f"<span class='ux-here'>{label}</span>", unsafe_allow_html=True)
