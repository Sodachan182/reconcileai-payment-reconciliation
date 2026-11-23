"""Turn mapped raw tables into validated :class:`Order` / :class:`BankTransaction` records."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

import pandas as pd

from reconcileai.config import Settings
from reconcileai.ingestion.column_mapper import missing_required
from reconcileai.ingestion.readers import RawTable
from reconcileai.models import BankTransaction, Order, OrderState, TxnKind
from reconcileai.money import SUPPORTED_CURRENCIES, AmountError, is_blank, parse_amount
from reconcileai.text import compact, name_tokens, normalize_text

DATE_FORMATS = ("auto", "YYYY-MM-DD", "DD/MM/YYYY", "MM/DD/YYYY")

_ISO = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T].*)?$")
_DMY = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})(?:[ T].*)?$")


class DateFormatError(ValueError):
    pass


@dataclass
class RowIssue:
    row: int
    field: str
    message: str
    value: str = ""


@dataclass
class ImportReport:
    kind: str
    source_name: str
    total_rows: int = 0
    valid_rows: int = 0
    errors: list[RowIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    duplicate_rows_dropped: int = 0
    blocking: list[str] = field(default_factory=list)   # file-level problems

    @property
    def ok(self) -> bool:
        return not self.blocking and self.valid_rows > 0

    @property
    def rejected_rows(self) -> int:
        return len({e.row for e in self.errors})


def _cell(value: object) -> object:
    if isinstance(value, str):
        return value.strip()
    return value


def resolve_date_order(values: list[object]) -> str | None:
    """Decide day-first vs month-first for slash dates using the whole column.

    Returns ``"DMY"``, ``"MDY"``, or ``None`` if no slash dates appear.
    Raises :class:`DateFormatError` when the column is ambiguous or contradictory.
    """
    day_first = month_first = slash_seen = False
    for v in values:
        if not isinstance(v, str):
            continue
        m = _DMY.match(v.strip())
        if not m:
            continue
        slash_seen = True
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12 >= b:
            day_first = True
        elif b > 12 >= a:
            month_first = True
    if not slash_seen:
        return None
    if day_first and month_first:
        raise DateFormatError("dates mix day-first and month-first order")
    if day_first:
        return "DMY"
    if month_first:
        return "MDY"
    raise DateFormatError(
        "dates like 03/04/2026 could be day-first or month-first; choose the date format"
    )


def parse_date(value: object, order: str | None) -> date:
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            raise ValueError("empty date")
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if is_blank(value):
        raise ValueError("empty date")
    s = str(value).strip()
    m = _ISO.match(s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _DMY.match(s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if order == "MDY":
            return date(y, a, b)
        if order == "DMY":
            return date(y, b, a)
        raise ValueError(f"ambiguous date '{s}'")
    raise ValueError(f"'{s}' is not a recognised date")


def _date_order_for(fmt: str, values: list[object]) -> str | None:
    if fmt == "DD/MM/YYYY":
        return "DMY"
    if fmt == "MM/DD/YYYY":
        return "MDY"
    if fmt == "YYYY-MM-DD":
        return None
    return resolve_date_order(values)


def _order_state(raw: str) -> OrderState:
    n = normalize_text(raw)
    if any(k in n for k in ("HUY", "CANCEL", "VOID")):
        return OrderState.CANCELLED
    if any(k in n for k in ("HOAN TIEN", "REFUND", "DA HOAN")):
        return OrderState.REFUNDED
    return OrderState.OPEN


_FEE = (" PHI ", " FEE ", " FEES ", "SMS BANKING", "PHI QUAN LY", "PHI DICH VU", " CHARGE ")
_REFUND_OUT = ("HOAN TIEN", "REFUND", "TRA LAI KH", "HOAN TRA KH")
_INTERNAL = ("NOI BO", "INTERNAL", "CHUYEN VE TK", "TK CONG TY", "OWN ACCOUNT")
_REVERSAL = ("REVERSAL", "HOAN TRA GD", "HUY GD", "TRA SOAT")


def classify_txn(description_norm: str, credit: Decimal, debit: Decimal) -> TxnKind:
    d = " " + description_norm + " "
    if credit > 0:
        if any(k in d for k in _INTERNAL):
            return TxnKind.INTERNAL
        if any(k in d for k in _REVERSAL):
            return TxnKind.REVERSAL
        return TxnKind.CREDIT
    if any(k in d for k in _FEE):
        return TxnKind.FEE
    if any(k in d for k in _REFUND_OUT):
        return TxnKind.REFUND
    if any(k in d for k in _INTERNAL):
        return TxnKind.INTERNAL
    return TxnKind.DEBIT


def _currency(row: pd.Series, mapping: dict, settings: Settings) -> str:
    col = mapping.get("currency")
    if not col or is_blank(row[col]):
        return settings.currency
    cur = normalize_text(row[col])  # "VNĐ" -> "VND"
    if cur in ("DONG", "D"):
        cur = "VND"
    if cur not in SUPPORTED_CURRENCIES:
        raise AmountError(f"Unsupported currency '{row[col]}'")
    return cur


def _record_fingerprint(record) -> tuple:
    d = {k: getattr(record, k) for k in record.__slots__ if k != "source_row"}
    return tuple(sorted((k, str(v)) for k, v in d.items()))


def normalize_orders(
    table: RawTable, mapping: dict[str, str | None], settings: Settings, date_format: str = "auto"
) -> tuple[list[Order], ImportReport]:
    report = ImportReport(kind="orders", source_name=table.source_name, total_rows=len(table.frame))
    report.warnings.extend(table.warnings)
    missing = missing_required(mapping, "orders")
    if missing:
        report.blocking.append("Map the required fields: " + ", ".join(missing))
        return [], report
    frame = table.frame
    try:
        order = _date_order_for(date_format, [_cell(v) for v in frame[mapping["order_date"]]])
    except DateFormatError as exc:
        report.blocking.append(f"Order date column: {exc}.")
        return [], report
    if order and date_format == "auto":
        report.warnings.append(
            f"Order dates read as {'day/month/year' if order == 'DMY' else 'month/day/year'} "
            "(determined from dates whose day is above 12)."
        )

    by_id: dict[str, Order] = {}
    for row_no, row in frame.iterrows():
        row = row.map(_cell)
        errs: list[RowIssue] = []
        oid = "" if is_blank(row[mapping["order_id"]]) else str(row[mapping["order_id"]]).strip()
        if not oid:
            errs.append(RowIssue(row_no, "Order ID", "missing"))
        customer = "" if is_blank(row[mapping["customer"]]) else str(row[mapping["customer"]]).strip()
        if not customer:
            errs.append(RowIssue(row_no, "Customer", "missing"))
        try:
            currency = _currency(row, mapping, settings)
        except AmountError as exc:
            errs.append(RowIssue(row_no, "Currency", str(exc), str(row[mapping["currency"]])))
            currency = settings.currency
        amount = None
        try:
            amount = parse_amount(row[mapping["amount"]], currency)
            if amount <= 0:
                errs.append(RowIssue(row_no, "Amount", "must be greater than zero", str(row[mapping["amount"]])))
        except AmountError as exc:
            errs.append(RowIssue(row_no, "Amount", str(exc), str(row[mapping["amount"]])))
        try:
            odate = parse_date(row[mapping["order_date"]], order)
        except ValueError as exc:
            errs.append(RowIssue(row_no, "Order date", str(exc), str(row[mapping["order_date"]])))
            odate = None
        if errs:
            report.errors.extend(errs)
            continue
        reference = ""
        if mapping.get("reference") and not is_blank(row[mapping["reference"]]):
            reference = str(row[mapping["reference"]]).strip()
        raw_status = ""
        if mapping.get("status") and not is_blank(row[mapping["status"]]):
            raw_status = str(row[mapping["status"]]).strip()
        rec = Order(
            order_id=oid, customer=customer, amount=amount, currency=currency,
            order_date=odate, reference=reference, raw_status=raw_status,
            state=_order_state(raw_status), source_row=int(row_no),
            customer_norm=normalize_text(customer), key=compact(oid),
            reference_key=compact(reference) if reference else "",
        )
        prior = by_id.get(oid)
        if prior is not None:
            if _record_fingerprint(prior) == _record_fingerprint(rec):
                report.duplicate_rows_dropped += 1
                report.warnings.append(f"Row {row_no}: exact duplicate of order {oid} (row {prior.source_row}); dropped.")
            else:
                report.errors.append(RowIssue(row_no, "Order ID", f"duplicate ID {oid} with different values (first at row {prior.source_row})", oid))
            continue
        by_id[oid] = rec

    report.valid_rows = len(by_id)
    if not by_id and not report.blocking:
        report.blocking.append("No valid order rows were found.")
    return list(by_id.values()), report


def normalize_bank(
    table: RawTable, mapping: dict[str, str | None], settings: Settings, date_format: str = "auto"
) -> tuple[list[BankTransaction], ImportReport]:
    report = ImportReport(kind="bank", source_name=table.source_name, total_rows=len(table.frame))
    report.warnings.extend(table.warnings)
    missing = missing_required(mapping, "bank")
    if missing:
        report.blocking.append("Map the required fields: " + ", ".join(missing))
        return [], report
    frame = table.frame
    try:
        order = _date_order_for(date_format, [_cell(v) for v in frame[mapping["txn_date"]]])
    except DateFormatError as exc:
        report.blocking.append(f"Transaction date column: {exc}.")
        return [], report
    if order and date_format == "auto":
        report.warnings.append(
            f"Transaction dates read as {'day/month/year' if order == 'DMY' else 'month/day/year'} "
            "(determined from dates whose day is above 12)."
        )

    by_id: dict[str, BankTransaction] = {}
    for row_no, row in frame.iterrows():
        row = row.map(_cell)
        errs: list[RowIssue] = []
        tid = "" if is_blank(row[mapping["txn_id"]]) else str(row[mapping["txn_id"]]).strip()
        if not tid:
            errs.append(RowIssue(row_no, "Transaction ID", "missing"))
        desc = "" if is_blank(row[mapping["description"]]) else str(row[mapping["description"]])
        try:
            currency = _currency(row, mapping, settings)
        except AmountError as exc:
            errs.append(RowIssue(row_no, "Currency", str(exc)))
            currency = settings.currency
        credit = debit = Decimal(0)
        try:
            if mapping.get("credit"):
                cv = row[mapping["credit"]]
                credit = Decimal(0) if is_blank(cv) else parse_amount(cv, currency)
                dcol = mapping.get("debit")
                dv = row[dcol] if dcol else None
                debit = Decimal(0) if is_blank(dv) else parse_amount(dv, currency)
                if credit < 0 or debit < 0:
                    errs.append(RowIssue(row_no, "Amount", "credit/debit columns must not be negative"))
                if credit > 0 and debit > 0:
                    errs.append(RowIssue(row_no, "Amount", "row has both credit and debit"))
            else:
                signed = parse_amount(row[mapping["amount"]], currency)
                credit, debit = (signed, Decimal(0)) if signed >= 0 else (Decimal(0), -signed)
            if credit == 0 and debit == 0 and not errs:
                errs.append(RowIssue(row_no, "Amount", "zero amount"))
        except AmountError as exc:
            errs.append(RowIssue(row_no, "Amount", str(exc)))
        try:
            tdate = parse_date(row[mapping["txn_date"]], order)
        except ValueError as exc:
            errs.append(RowIssue(row_no, "Transaction date", str(exc), str(row[mapping["txn_date"]])))
            tdate = None
        balance = None
        if mapping.get("balance") and not is_blank(row[mapping["balance"]]):
            try:
                balance = parse_amount(row[mapping["balance"]], currency)
            except AmountError:
                report.warnings.append(f"Row {row_no}: balance '{row[mapping['balance']]}' ignored (not a number).")
        if errs:
            report.errors.extend(errs)
            continue
        dnorm = normalize_text(desc)
        rec = BankTransaction(
            txn_id=tid, txn_date=tdate, description=desc, credit=credit, debit=debit,
            currency=currency, balance=balance, source_row=int(row_no),
            kind=classify_txn(dnorm, credit, debit), description_norm=dnorm,
            name_text=name_tokens(desc),
        )
        prior = by_id.get(tid)
        if prior is not None:
            if _record_fingerprint(prior) == _record_fingerprint(rec):
                report.duplicate_rows_dropped += 1
                report.warnings.append(f"Row {row_no}: exact duplicate of transaction {tid} (row {prior.source_row}); dropped.")
            else:
                report.errors.append(RowIssue(row_no, "Transaction ID", f"duplicate ID {tid} with different values (first at row {prior.source_row})", tid))
            continue
        by_id[tid] = rec

    report.valid_rows = len(by_id)
    if not by_id and not report.blocking:
        report.blocking.append("No valid bank rows were found.")
    return list(by_id.values()), report
