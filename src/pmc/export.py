"""Выгрузка таблиц в CSV и Excel с корректными типами (FR-13).

CSV: кодировка UTF-8 с BOM, разделитель «;», десятичная запятая — так файл правильно
открывается в русском Excel. Даты — в формате ГГГГ-ММ-ДД.
XLSX: числа записываются числами, даты — датами; первый лист «Описание» содержит
пометку о синтетических данных и параметры отбора.
"""

from __future__ import annotations

import io
from datetime import date, datetime
from decimal import Decimal

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from pmc.config import DEMO_NOTE


def _plain(df: pd.DataFrame) -> pd.DataFrame:
    """Decimal → float только на выходе; даты с часовым поясом → без пояса (Excel их не понимает)."""
    out = df.copy()
    for c in out.columns:
        s = out[c]
        if s.dtype == object and s.map(lambda v: isinstance(v, Decimal)).any():
            out[c] = s.map(lambda v: float(v) if isinstance(v, Decimal) else v)
        elif isinstance(s.dtype, pd.DatetimeTZDtype):
            out[c] = s.dt.tz_localize(None)
        elif (
            s.dtype == object
            and s.map(lambda v: isinstance(v, datetime) and v.tzinfo is not None).any()
        ):
            out[c] = s.map(lambda v: v.replace(tzinfo=None) if isinstance(v, datetime) else v)
    return out


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    buf = io.StringIO()
    buf.write(f"# {DEMO_NOTE}\n")
    _plain(df).to_csv(buf, sep=";", decimal=",", index=False, date_format="%Y-%m-%d %H:%M:%S")
    return buf.getvalue().encode("utf-8-sig")


def to_xlsx_bytes(sheets: dict[str, pd.DataFrame], description: dict[str, str]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Описание"
    ws["A1"] = DEMO_NOTE
    ws["A1"].font = Font(bold=True)
    for i, (k, v) in enumerate(description.items(), start=3):
        ws.cell(row=i, column=1, value=k).font = Font(bold=True)
        ws.cell(row=i, column=2, value=v)
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 80

    for name, df in sheets.items():
        sheet = wb.create_sheet(title=name[:31])
        plain = _plain(df)
        sheet.append(list(plain.columns))
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        for row in plain.itertuples(index=False):
            sheet.append([_cell(v) for v in row])
        for j, col in enumerate(plain.columns, start=1):
            width = max([len(str(col))] + [len(str(v)) for v in plain[col].head(200)])
            sheet.column_dimensions[get_column_letter(j)].width = min(max(10, width + 2), 60)
            if (
                pd.api.types.is_float_dtype(plain[col])
                or plain[col].map(lambda v: isinstance(v, float)).any()
            ):
                for cell in sheet.iter_cols(min_col=j, max_col=j, min_row=2):
                    for c in cell:
                        c.number_format = "#,##0.00"
            if plain[col].map(lambda v: isinstance(v, date)).any():
                for cell in sheet.iter_cols(min_col=j, max_col=j, min_row=2):
                    for c in cell:
                        c.number_format = "DD.MM.YYYY"
        sheet.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _cell(v):
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    if v is pd.NA or v is pd.NaT:
        return None
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    if isinstance(v, dict | list):
        return str(v)
    if hasattr(v, "item") and not isinstance(v, str):
        return v.item()
    if not isinstance(v, int | float | str | bool | date | datetime):
        return str(v)
    return v
