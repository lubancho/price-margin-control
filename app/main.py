"""Точка входа веб-интерфейса: streamlit run app/main.py"""

import streamlit as st

st.set_page_config(page_title="Контроль цен и маржинальности", page_icon="📊", layout="wide")

from components.ui import sidebar_role  # noqa: E402 — set_page_config должен быть первым вызовом

pages = [
    st.Page("views/overview.py", title="Главная", icon=":material/dashboard:", default=True),
    st.Page("views/prices.py", title="Цены и рекомендации", icon=":material/price_change:"),
    st.Page("views/alerts.py", title="Предупреждения", icon=":material/warning:"),
    st.Page("views/scenarios.py", title="Что будет, если изменить цены?", icon=":material/tune:"),
    st.Page("views/history.py", title="Методика и история", icon=":material/menu_book:"),
    st.Page("views/export.py", title="Экспорт", icon=":material/download:"),
]
nav = st.navigation(pages)
with st.sidebar:
    st.markdown("**Контроль цен и маржинальности**  \nдемо-версия на синтетических данных")
sidebar_role()
nav.run()
