"""Точка входа веб-интерфейса: streamlit run app/main.py"""

import streamlit as st

st.set_page_config(page_title="Контроль цен и маржинальности", page_icon="📊", layout="wide")

from components.ui import role_bar  # noqa: E402 — set_page_config должен быть первым вызовом

# Одна страница — один вопрос. Боковая панель: «Главное», «Что сделать», «Разбор» и «Справка»;
# технические материалы — только в «Справке».
pages = {
    "Главное": [
        st.Page("views/overview.py", title="Обзор", icon=":material/home:", default=True),
    ],
    "Что сделать": [
        st.Page("views/prices.py", title="Цены к пересмотру", icon=":material/sell:"),
        st.Page("views/alerts.py", title="Ошибки в данных и правилах", icon=":material/warning:"),
    ],
    "Разбор": [
        st.Page("views/margin.py", title="Где падает маржа", icon=":material/bar_chart:"),
        st.Page("views/scenarios.py", title="Что если изменить правило", icon=":material/tune:"),
    ],
    "Справка": [
        st.Page(
            "views/history.py", title="Методика, правила и история", icon=":material/menu_book:"
        ),
        st.Page("views/export.py", title="Экспорт", icon=":material/download:"),
    ],
}
nav = st.navigation(pages, position="hidden")  # меню нет: переходы — со сводки вглубь
# Верхняя строка: название (переход на сводку), выбор роли и «Справка».
top = st.columns([2.2, 6, 1], vertical_alignment="center")
top[0].page_link("views/overview.py", label="**Цены и маржа**", icon=":material/home:")
with top[1]:
    role_bar()
top[2].page_link("views/history.py", label="Справка")
nav.run()
