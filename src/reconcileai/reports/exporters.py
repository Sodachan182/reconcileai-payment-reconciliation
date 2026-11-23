"""Excel (XLSX), CSV and JSON exporters. All run the integrity check first."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from reconcileai.money import precision
from reconcileai.reconciliation.service import ReconciliationState
from reconcileai.reports.tables import (
    ReportTables,
    build_tables,
    decimal_to_str,
    is_money_column,
    safe_text,
    sanitize_frame,
)

HEADER_FILL = PatternFill("solid", fgColor="1F2630")
HEADER_FONT = Font(bold=True, color="F0F2F5")
DANGER_FONT = Font(color="B23B3B")
WARN_FILL = PatternFill("solid", fgColor="FBF1DC")


def export_xlsx(rs: ReconciliationState, tables: ReportTables | None = None) -> bytes:
    tables = tables or build_tables(rs)
    cur = tables.currency
    num_fmt = "#,##0" if precision(cur) == 0 else "#,##0." + "0" * precision(cur)
    wb = Workbook()
    wb.remove(wb.active)
    for name, df in tables.sheets().items():
        ws = wb.create_sheet(name)
        df = sanitize_frame(df)
        if df.empty:
            ws.append(["No rows"])
            continue
        ws.append(list(df.columns))
        for row in df.itertuples(index=False):
            ws.append([v if not isinstance(v, str) else safe_text(v) for v in row])
        for idx, col in enumerate(df.columns, start=1):
            letter = get_column_letter(idx)
            head = ws.cell(row=1, column=idx)
            head.fill, head.font = HEADER_FILL, HEADER_FONT
            head.alignment = Alignment(vertical="center")
            values = df[col].tolist()
            money = is_money_column(col, cur) or (name == "Summary" and col == "Value")
            for r, v in enumerate(values, start=2):
                cell = ws.cell(row=r, column=idx)
                if isinstance(v, (int, Decimal)) and not isinstance(v, bool) and money:
                    cell.number_format = num_fmt
                    cell.alignment = Alignment(horizontal="right")
                elif isinstance(v, date):
                    cell.number_format = "yyyy-mm-dd"
            width = max([len(str(col))] + [len(str(v)) for v in values[:500]]) + 2
            ws.column_dimensions[letter].width = min(max(width, 10), 60)
            if col.startswith("Difference"):
                rng = f"{letter}2:{letter}{len(values) + 1}"
                ws.conditional_formatting.add(rng, CellIsRule(operator="lessThan", formula=["0"], font=DANGER_FONT))
                ws.conditional_formatting.add(rng, CellIsRule(operator="greaterThan", formula=["0"], fill=WARN_FILL))
            if col == "Status" and name in ("Unmatched Orders", "Unmatched Transactions"):
                rng = f"{letter}2:{letter}{len(values) + 1}"
                ws.conditional_formatting.add(
                    rng, FormulaRule(formula=[f'OR({letter}2="Amount mismatch",{letter}2="Duplicate candidate")'],
                                     fill=WARN_FILL))
        ws.freeze_panes = "B2" if name not in ("Summary", "Configuration") else "A2"
        ws.auto_filter.ref = ws.dimensions
    ws = wb.create_sheet("Integrity")
    ws.append(list(tables.integrity.columns))
    for row in tables.integrity.itertuples(index=False):
        ws.append([safe_text(v) for v in row])
    for idx in range(1, 4):
        ws.cell(row=1, column=idx).fill, ws.cell(row=1, column=idx).font = HEADER_FILL, HEADER_FONT
        ws.column_dimensions[get_column_letter(idx)].width = (34, 10, 90)[idx - 1]
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_csv_zip(rs: ReconciliationState, tables: ReportTables | None = None) -> bytes:
    """One CSV per sheet (UTF-8 with BOM for Excel), zipped."""
    tables = tables or build_tables(rs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, df in {**tables.sheets(), "Integrity": tables.integrity}.items():
            df = sanitize_frame(df).map(decimal_to_str) if not df.empty else df
            zf.writestr(name.lower().replace(" ", "_") + ".csv", df.to_csv(index=False).encode("utf-8-sig"))
    return buf.getvalue()


def export_json(rs: ReconciliationState, tables: ReportTables | None = None) -> bytes:
    """Machine-readable report. Amounts are strings to keep exact decimal values."""
    tables = tables or build_tables(rs)

    def enc(v):
        if isinstance(v, Decimal):
            return str(v)
        if isinstance(v, date):
            return v.isoformat()
        return v

    payload = {
        name.lower().replace(" ", "_"): [{k: enc(v) for k, v in row.items()} for row in df.to_dict("records")]
        for name, df in {**tables.sheets(), "Integrity": tables.integrity}.items()
    }
    payload["currency"] = tables.currency
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
