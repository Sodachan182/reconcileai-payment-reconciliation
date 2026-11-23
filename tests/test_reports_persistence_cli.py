import io
import json
import sqlite3
import zipfile
from decimal import Decimal

import pytest
from openpyxl import load_workbook
from typer.testing import CliRunner

from helpers import order, txn
from reconcileai.cli import app
from reconcileai.models import ReviewDecision
from reconcileai.persistence.database import Workspace
from reconcileai.pipeline import load_pair
from reconcileai.reconciliation.service import reconcile
from reconcileai.reports.exporters import export_csv_zip, export_json, export_xlsx
from reconcileai.reports.tables import build_tables, safe_text

runner = CliRunner()


def _state(settings):
    orders = [order("ORD2026-00001", "=HYPERLINK(\"http://x\")", 1250000),
              order("ORD2026-00002", "Tran Thi Lan", 700000)]
    txns = [txn("T1", "2026-11-02", "@SUM(1+1) ORD2026-00001", 1250000),
            txn("T2", "2026-11-03", "+cmd|' /C calc'!A0", 50000)]
    return reconcile(orders, txns, settings)


def test_safe_text():
    assert safe_text("=1+1") == "'=1+1" and safe_text("-5") == "'-5" and safe_text("ok") == "ok"
    assert safe_text(5) == 5


def test_xlsx_report_structure_values_and_formula_safety(settings):
    rs = _state(settings)
    wb = load_workbook(io.BytesIO(export_xlsx(rs)))
    assert wb.sheetnames == ["Summary", "Matched", "Unmatched Orders", "Unmatched Transactions", "Amount Mismatches",
                             "Duplicate Candidates", "Review Log", "Configuration", "Integrity"]
    m = wb["Matched"]
    headers = [c.value for c in m[1]]
    row = {h: c.value for h, c in zip(headers, m[2], strict=True)}
    assert row["Customer"].startswith("'=")                 # formula neutralised
    assert row["Bank description"].startswith("'@")
    assert row["Allocated (VND)"] == 1250000                # exact numeric value, unchanged
    assert m["D2"].number_format == "#,##0"
    assert m.freeze_panes == "B2" and m.auto_filter.ref
    for ws in wb.worksheets:
        for r in ws.iter_rows(values_only=True):
            for v in r:
                assert not (isinstance(v, str) and v[:1] in "=+-@"), (ws.title, v)
    summary = {r[0]: r[1] for r in wb["Summary"].iter_rows(min_row=2, values_only=True)}
    assert summary["Unresolved order amount"] == 700000
    assert summary["Unmatched incoming amount"] == 50000


def test_csv_zip_and_json(settings):
    rs = _state(settings)
    with zipfile.ZipFile(io.BytesIO(export_csv_zip(rs))) as zf:
        assert "summary.csv" in zf.namelist() and "matched.csv" in zf.namelist()
        assert "'=HYPERLINK" in zf.read("matched.csv").decode("utf-8-sig")
    data = json.loads(export_json(rs))
    assert data["currency"] == "VND"
    assert data["matched"][0]["Allocated (VND)"] == "1250000"   # exact decimal string


def test_report_numbers_equal_state_numbers(demo_files, settings):
    root, _ = demo_files
    rs = reconcile(*load_pair(root / "data" / "orders.xlsx", root / "data" / "bank_statement.xlsx", settings)[:2], settings)
    t = build_tables(rs)
    assert sum(t.matched["Allocated (VND)"], Decimal(0)) == rs.summary.allocated_incoming
    assert sum(t.unmatched_orders["Outstanding (VND)"], Decimal(0)) == rs.summary.unresolved_total
    assert sum(t.unmatched_transactions["Unallocated (VND)"], Decimal(0)) == rs.summary.unallocated_incoming


# --- persistence ---------------------------------------------------------------------------

def test_workspace_import_idempotent_conflicts_and_audit(tmp_path, demo_files, settings):
    root, _ = demo_files
    ws = Workspace(tmp_path / "w.db")
    o, b, lo, lb = load_pair(root / "data" / "orders.xlsx", root / "data" / "bank_statement.xlsx", settings)
    r1 = ws.import_orders(o, lo.report)
    r2 = ws.import_orders(o, lo.report)
    assert r1["added"] == 300 and r2["added"] == 0 and r2["unchanged"] == 300
    changed = [order(o[0].order_id, o[0].customer, o[0].amount + 1, o[0].order_date.isoformat())]
    r3 = ws.import_orders(changed, lo.report)
    assert r3["conflicting"] == [o[0].order_id] and len(ws.orders()) == 300
    assert ws.orders()[0].amount == sorted(o, key=lambda x: (x.order_date, x.order_id))[0].amount
    ws.import_bank(b, lb.report)
    loaded_o, loaded_b = ws.orders(), ws.transactions()
    assert {x.order_id: x.amount for x in loaded_o} == {x.order_id: x.amount for x in o}
    rs = reconcile(loaded_o, loaded_b, settings)
    run_id = ws.save_run(rs)
    assert run_id == 1 and ws.runs()[0]["integrity_ok"] == 1
    events = [e["event"] for e in ws.audit_log()]
    assert "matching_run" in events and "import_orders" in events


def test_decisions_append_only_and_round_trip(tmp_path, settings):
    ws = Workspace(tmp_path / "w.db")
    ws.import_orders([order("O1", "An", 100)], type("R", (), {"source_name": "x", "total_rows": 1,
                                                               "rejected_rows": 0, "warnings": [], "errors": []})())
    d = ws.add_decision(ReviewDecision("C-1", "confirm", "ok"))
    ws.add_decision(ReviewDecision("C-1", "undo"))
    assert [x.action for x in ws.decisions()] == ["confirm", "undo"] and d.decision_id == 1
    with pytest.raises(sqlite3.DatabaseError):
        ws.conn.execute("UPDATE decisions SET action='reject'")
    with pytest.raises(sqlite3.DatabaseError):
        ws.conn.execute("DELETE FROM decisions")
    with pytest.raises(ValueError):
        ws.add_decision(ReviewDecision("C-1", "approve"))
    ws.clear()
    assert ws.counts() == {"orders": 0, "transactions": 0, "decisions": 0, "runs": 0}
    assert ws.audit_log()[0]["event"] == "workspace_cleared"


def test_failed_transaction_leaves_no_partial_import(tmp_path):
    ws = Workspace(tmp_path / "w.db")
    bad = [order("O1", "An", 100), order("O1", "An", 100)]  # duplicate PK inside one import
    rep = type("R", (), {"source_name": "x", "total_rows": 2, "rejected_rows": 0, "warnings": [], "errors": []})()
    with pytest.raises(sqlite3.IntegrityError):
        ws.import_orders(bad, rep)
    assert ws.counts()["orders"] == 0 and not ws.import_sessions()


# --- CLI -------------------------------------------------------------------------------------

def test_cli_demo_validate_run_evaluate(tmp_path):
    r = runner.invoke(app, ["demo", "--out", str(tmp_path), "--orders", "120"])
    assert r.exit_code == 0, r.output
    data = tmp_path / "data"
    r = runner.invoke(app, ["validate", str(data / "bank_statement.csv")])
    assert r.exit_code == 0 and "Bank:" in r.output
    out = tmp_path / "reports"
    r = runner.invoke(app, ["run", "--orders", str(data / "orders.xlsx"), "--bank", str(data / "bank_statement.xlsx"),
                            "--output", str(out), "--formats", "xlsx,csv,json", "--db", str(tmp_path / "w.db")])
    assert r.exit_code == 0, r.output
    assert "checks passed" in r.output and (out / "reconciliation_report.xlsx").exists()
    r = runner.invoke(app, ["evaluate", str(tmp_path / "ground_truth.json"), "--min-precision", "0.95",
                            "--output", str(tmp_path / "m.json")])
    assert r.exit_code == 0, r.output
    assert json.loads((tmp_path / "m.json").read_text())["automatic"]["precision"] >= 0.95


def test_cli_validate_invalid_file_exit_code(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("Order ID,Customer,Amount,Order Date\nA,An,abc,2026-11-01\n", encoding="utf-8")
    assert runner.invoke(app, ["validate", str(p), "--kind", "orders"]).exit_code == 1
    p2 = tmp_path / "broken.xlsx"
    p2.write_bytes(b"garbage")
    r = runner.invoke(app, ["validate", str(p2)])
    assert r.exit_code == 1 and "corrupted" in r.output
