import io
from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from reconcileai.ingestion.column_mapper import missing_required, suggest_mapping
from reconcileai.ingestion.normalizer import (
    DateFormatError,
    normalize_bank,
    normalize_orders,
    parse_date,
    resolve_date_order,
)
from reconcileai.ingestion.readers import FileReadError, list_sheets, read_table
from reconcileai.models import OrderState, TxnKind


def _xlsx(frames: dict[str, pd.DataFrame]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, df in frames.items():
            df.to_excel(xw, sheet_name=name, index=False)
    return buf.getvalue()


def test_csv_utf8_bom_semicolon_vietnamese_headers(settings):
    text = "Mã đơn hàng;Tên khách hàng;Số tiền;Ngày đặt hàng;Trạng thái\nORD-1;Nguyễn Văn An;1.250.000;2026-11-20;Chờ thanh toán\n"
    t = read_table(b"\xef\xbb\xbf" + text.encode("utf-8"), "o.csv")
    assert t.encoding == "utf-8-sig" and t.delimiter == ";"
    m = suggest_mapping(list(t.frame.columns), "orders")
    assert m["order_id"] == "Mã đơn hàng" and m["amount"] == "Số tiền" and m["order_date"] == "Ngày đặt hàng"
    orders, rep = normalize_orders(t, m, settings)
    assert rep.ok and orders[0].amount == Decimal("1250000") and orders[0].customer == "Nguyễn Văn An"
    assert orders[0].customer_norm == "NGUYEN VAN AN"


def test_csv_legacy_vietnamese_encoding():
    data = "Order ID,Customer,Amount,Order Date\nO1,Trân An,100000,2026-11-01\n".encode("cp1258")
    t = read_table(data, "legacy.csv")
    assert t.encoding == "cp1258"
    assert any("Windows-1258" in w for w in t.warnings)
    assert t.frame.iloc[0]["Customer"] == "Trân An"


def test_bank_headers_english_and_vietnamese():
    en = suggest_mapping(["Transaction ID", "Transaction Date", "Description", "Credit", "Debit", "Balance"], "bank")
    vi = suggest_mapping(["Ngày giao dịch", "Mã giao dịch", "Nội dung", "Tiền vào", "Tiền ra", "Số dư"], "bank")
    for m in (en, vi):
        assert not missing_required(m, "bank")
        assert m["credit"] and m["debit"] and m["balance"]
    assert vi["credit"] == "Tiền vào" and vi["debit"] == "Tiền ra"


def test_xlsx_sheet_selection_and_dates(settings):
    data = _xlsx({
        "Notes": pd.DataFrame({"x": [1]}),
        "Orders": pd.DataFrame({"Order ID": ["A1"], "Customer": ["Lê Thu"], "Amount": [500000],
                                "Order Date": [pd.Timestamp("2026-11-03")]}),
    })
    assert list_sheets(data) == ["Notes", "Orders"]
    t = read_table(data, "o.xlsx", sheet="Orders")
    orders, rep = normalize_orders(t, suggest_mapping(list(t.frame.columns), "orders"), settings)
    assert rep.ok and orders[0].order_date == date(2026, 11, 3)
    with pytest.raises(FileReadError):
        read_table(data, "o.xlsx", sheet="Missing")


@pytest.mark.parametrize("data,name,msg", [
    (b"", "x.csv", "empty"),
    (b"not a zip at all", "x.xlsx", "corrupted"),
    (b"a,b\n", "x.csv", "no data rows"),
    (b"data", "x.pdf", "Unsupported"),
    (b"data", "x.xls", "Legacy"),
])
def test_unreadable_files_have_clear_errors(data, name, msg):
    with pytest.raises(FileReadError, match=msg):
        read_table(data, name)


def test_oversized_file_rejected():
    from reconcileai.ingestion import readers

    with pytest.raises(FileReadError, match="limit"):
        read_table(b"a" * (readers.MAX_FILE_BYTES + 1), "big.csv")


def test_empty_rows_skipped_and_reported(settings):
    t = read_table(b"Order ID,Customer,Amount,Order Date\nA,An,100,2026-11-01\n,,,\nB,Binh,200,2026-11-02\n", "o.csv")
    assert t.empty_rows_skipped == 1 and len(t.frame) == 2


def test_invalid_rows_rejected_with_reasons_and_no_silent_fix(settings):
    csv = ("Order ID,Customer,Amount,Order Date,Currency\n"
           "A,An,abc,2026-11-01,VND\n"        # invalid amount
           "B,Binh,-5000,2026-11-01,VND\n"    # negative
           "C,,1000,2026-11-01,VND\n"         # missing customer
           "D,Dung,1000,2026-02-31,VND\n"     # impossible date
           "E,Em,1000.5,2026-11-01,VND\n"     # VND has no decimals
           "F,Phuc,1000,2026-11-01,BTC\n"     # unsupported currency
           "G,Giang,1000,2026-11-01,VND\n")
    t = read_table(csv.encode(), "o.csv")
    orders, rep = normalize_orders(t, suggest_mapping(list(t.frame.columns), "orders"), settings)
    assert [o.order_id for o in orders] == ["G"]
    assert rep.rejected_rows == 6
    reasons = " ".join(e.message for e in rep.errors)
    for needle in ("not a number", "greater than zero", "missing", "day is out of range", "fractional part", "Unsupported currency"):
        assert needle in reasons or needle.lower() in reasons.lower()


def test_duplicate_rows_dropped_conflicting_ids_rejected(settings):
    csv = ("Order ID,Customer,Amount,Order Date\nA,An,100,2026-11-01\nA,An,100,2026-11-01\nA,An,999,2026-11-01\n")
    t = read_table(csv.encode(), "o.csv")
    orders, rep = normalize_orders(t, suggest_mapping(list(t.frame.columns), "orders"), settings)
    assert len(orders) == 1 and rep.duplicate_rows_dropped == 1
    assert any("different values" in e.message for e in rep.errors)


def test_ambiguous_dates_block_import_until_format_chosen(settings):
    csv = "Order ID,Customer,Amount,Order Date\nA,An,100,03/04/2026\nB,Binh,100,05/06/2026\n"
    t = read_table(csv.encode(), "o.csv")
    m = suggest_mapping(list(t.frame.columns), "orders")
    orders, rep = normalize_orders(t, m, settings, "auto")
    assert not orders and rep.blocking and "choose the date format" in rep.blocking[0]
    orders, rep = normalize_orders(t, m, settings, "DD/MM/YYYY")
    assert orders[0].order_date == date(2026, 4, 3)
    orders, rep = normalize_orders(t, m, settings, "MM/DD/YYYY")
    assert orders[0].order_date == date(2026, 3, 4)


def test_date_order_resolved_from_column_evidence():
    assert resolve_date_order(["03/04/2026", "25/04/2026"]) == "DMY"
    assert resolve_date_order(["03/04/2026", "04/25/2026"]) == "MDY"
    assert resolve_date_order(["2026-04-03"]) is None
    with pytest.raises(DateFormatError):
        resolve_date_order(["25/04/2026", "04/25/2026"])
    assert parse_date("2026-11-20 10:31:00", None) == date(2026, 11, 20)


def test_bank_normalization_kinds_and_signed_amounts(settings):
    csv = ("Transaction ID,Transaction Date,Description,Amount\n"
           "T1,2026-11-01,NGUYEN VAN AN CK ORD-1,1.250.000\n"
           "T2,2026-11-01,PHI SMS BANKING,-11.000\n"
           "T3,2026-11-02,HOAN TIEN KH TRAN BINH,-500.000\n"
           "T4,2026-11-02,CHUYEN TIEN NOI BO TU TK CONG TY,20.000.000\n"
           "T5,2026-11-03,TT NCC BAO BI,-3.000.000\n")
    t = read_table(csv.encode(), "b.csv")
    m = suggest_mapping(list(t.frame.columns), "bank")
    assert m["amount"] == "Amount"
    txns, rep = normalize_bank(t, m, settings)
    kinds = {x.txn_id: x.kind for x in txns}
    assert kinds == {"T1": TxnKind.CREDIT, "T2": TxnKind.FEE, "T3": TxnKind.REFUND, "T4": TxnKind.INTERNAL,
                     "T5": TxnKind.DEBIT}
    assert txns[1].debit == Decimal("11000") and txns[1].credit == 0


def test_bank_row_with_both_credit_and_debit_rejected(settings):
    csv = "Transaction ID,Transaction Date,Description,Credit,Debit\nT1,2026-11-01,X,100,100\nT2,2026-11-01,Y,,\n"
    t = read_table(csv.encode(), "b.csv")
    txns, rep = normalize_bank(t, suggest_mapping(list(t.frame.columns), "bank"), settings)
    assert not txns and rep.rejected_rows == 2


def test_order_status_mapping(settings):
    csv = "Order ID,Customer,Amount,Order Date,Status\nA,An,1,2026-11-01,Đã hủy\nB,Bi,1,2026-11-01,Đã hoàn tiền\nC,Cu,1,2026-11-01,Awaiting payment\n"
    t = read_table(csv.encode(), "o.csv")
    orders, _ = normalize_orders(t, suggest_mapping(list(t.frame.columns), "orders"), settings)
    assert [o.state for o in orders] == [OrderState.CANCELLED, OrderState.REFUNDED, OrderState.OPEN]


def test_missing_required_mapping_blocks(settings):
    t = read_table(b"Foo,Bar\n1,2\n", "o.csv")
    m = suggest_mapping(list(t.frame.columns), "orders")
    orders, rep = normalize_orders(t, m, settings)
    assert not orders and "Map the required fields" in rep.blocking[0]
