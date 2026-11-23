"""Command-line interface. Uses the same pipeline, engine and exporters as the UI.

Exit codes: 0 success · 1 validation/import error · 2 integrity check failed ·
3 evaluation below the required precision.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import typer

from reconcileai.config import Settings
from reconcileai.ingestion.readers import FileReadError
from reconcileai.money import format_amount
from reconcileai.pipeline import load_bank, load_orders, load_pair

app = typer.Typer(add_completion=False, help="ReconcileAI — local-first payment reconciliation.",
                  no_args_is_help=True)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Vietnamese text on Windows consoles


def _settings(config: Path | None) -> Settings:
    if config is None:
        return Settings()
    return Settings.model_validate_json(config.read_text(encoding="utf-8"))


def _print_report(src, label: str) -> bool:
    r = src.report
    typer.echo(f"{label}: {r.source_name} — {r.total_rows} rows, {r.valid_rows} valid, "
               f"{r.rejected_rows} rejected, {r.duplicate_rows_dropped} exact duplicates dropped")
    for b in r.blocking:
        typer.echo(f"  ERROR {b}")
    for e in r.errors[:15]:
        typer.echo(f"  row {e.row}: {e.field} {e.message}" + (f" ('{e.value}')" if e.value else ""))
    if len(r.errors) > 15:
        typer.echo(f"  … {len(r.errors) - 15} more row errors")
    for w in r.warnings[:5]:
        typer.echo(f"  note: {w}")
    return r.ok


@app.command()
def validate(
    file: Path = typer.Argument(..., exists=True, readable=True, help="Orders or bank statement (.xlsx/.csv)"),
    kind: str = typer.Option("auto", help="orders | bank | auto (guess from headers)"),
    sheet: str | None = typer.Option(None, help="Worksheet name for .xlsx"),
    date_format: str = typer.Option("auto", help="auto | YYYY-MM-DD | DD/MM/YYYY | MM/DD/YYYY"),
) -> None:
    """Validate a file and print the import report."""
    settings = Settings()
    try:
        if kind == "auto":
            o = load_orders(file, settings, sheet=sheet, date_format=date_format)
            b = load_bank(file, settings, sheet=sheet, date_format=date_format)
            src, label = (o, "Orders") if o.report.valid_rows >= b.report.valid_rows else (b, "Bank")
        elif kind == "orders":
            src, label = load_orders(file, settings, sheet=sheet, date_format=date_format), "Orders"
        else:
            src, label = load_bank(file, settings, sheet=sheet, date_format=date_format), "Bank"
    except FileReadError as exc:
        typer.echo(f"ERROR {exc}")
        raise typer.Exit(1) from exc
    mapped = {k: v for k, v in src.mapping.items() if v}
    typer.echo("Column mapping: " + ", ".join(f"{k}←'{v}'" for k, v in mapped.items()))
    ok = _print_report(src, label)
    raise typer.Exit(0 if ok and not src.report.errors else 1)


@app.command()
def run(
    orders: Path = typer.Option(..., exists=True, help="Orders file (.xlsx/.csv)"),
    bank: Path = typer.Option(..., exists=True, help="Bank statement (.xlsx/.csv)"),
    output: Path = typer.Option(Path("reports"), help="Output directory"),
    config: Path | None = typer.Option(None, exists=True, help="Settings JSON"),
    formats: str = typer.Option("xlsx,json", help="Comma list of xlsx, csv, json"),
    db: Path | None = typer.Option(None, help="Also persist the run to this SQLite workspace"),
) -> None:
    """Import both files, reconcile, verify integrity and write reports."""
    from reconcileai.reconciliation.service import reconcile
    from reconcileai.reports.exporters import export_csv_zip, export_json, export_xlsx
    from reconcileai.reports.tables import build_tables

    settings = _settings(config)
    try:
        o, b, lo, lb = load_pair(orders, bank, settings)
    except FileReadError as exc:
        typer.echo(f"ERROR {exc}")
        raise typer.Exit(1) from exc
    ok_o, ok_b = _print_report(lo, "Orders"), _print_report(lb, "Bank")
    if not (ok_o and ok_b):
        raise typer.Exit(1)
    t0 = time.perf_counter()
    rs = reconcile(o, b, settings)
    elapsed = time.perf_counter() - t0
    s = rs.summary
    cur = s.currency
    typer.echo("")
    typer.echo(f"Reconciled in {elapsed:.2f}s  (input fingerprint {rs.match.input_hash})")
    typer.echo(f"  Expected receivable   {format_amount(s.expected_total, cur):>18} {cur}  ({s.open_orders} orders)")
    typer.echo(f"  Applied payments      {format_amount(s.applied_total, cur):>18} {cur}")
    typer.echo(f"  Unresolved            {format_amount(s.unresolved_total, cur):>18} {cur}")
    typer.echo(f"  Incoming credits      {format_amount(s.incoming_total, cur):>18} {cur}  ({s.incoming_count} lines)")
    typer.echo(f"  Unmatched incoming    {format_amount(s.unallocated_incoming, cur):>18} {cur}")
    typer.echo("  Order statuses: " + ", ".join(f"{k} {v}" for k, v in sorted(s.status_counts.items())))
    failed = [i for i in rs.invariants if not i.passed]
    typer.echo(f"  Integrity: {'all ' + str(len(rs.invariants)) + ' checks passed' if not failed else 'FAILED'}")
    for i in failed:
        typer.echo(f"    FAIL {i.name}: {i.detail}")
    if failed:
        raise typer.Exit(2)
    if db:
        from reconcileai.persistence.database import Workspace

        ws = Workspace(db)
        ws.import_orders(o, lo.report)
        ws.import_bank(b, lb.report)
        typer.echo(f"  Saved run #{ws.save_run(rs)} to {db}")
    output.mkdir(parents=True, exist_ok=True)
    tables = build_tables(rs)
    stem = "reconciliation_report"
    writers = {"xlsx": (export_xlsx, ".xlsx"), "csv": (export_csv_zip, "_csv.zip"), "json": (export_json, ".json")}
    for f in [x.strip() for x in formats.split(",") if x.strip()]:
        if f not in writers:
            typer.echo(f"ERROR unknown format '{f}'")
            raise typer.Exit(1)
        fn, suffix = writers[f]
        path = output / f"{stem}{suffix}"
        path.write_bytes(fn(rs, tables))
        typer.echo(f"  Wrote {path}")


@app.command()
def demo(
    generate: bool = typer.Option(True, "--generate/--no-generate", help="Write demo files"),
    orders: int = typer.Option(300, min=20, max=100_000, help="Number of orders"),
    seed: int = typer.Option(2026, help="Random seed"),
    out: Path = typer.Option(Path("demo"), help="Demo root directory"),
    clean: bool = typer.Option(False, help="Generate the clean (no anomalies) dataset instead"),
) -> None:
    """Generate the deterministic fictional demo dataset and its ground truth."""
    from reconcileai.demo_data import generate as gen
    from reconcileai.demo_data import write_dataset

    ds = gen(orders, seed, challenging=not clean)
    if not generate:
        typer.echo(json.dumps(ds.truth["scenario_counts"], indent=2))
        return
    data_dir = out / "data" / ("clean" if clean else "")
    truth = out / ("ground_truth_clean.json" if clean else "ground_truth.json")
    for p in write_dataset(ds, data_dir, truth):
        typer.echo(f"Wrote {p}")
    typer.echo(f"{ds.truth['order_count']} orders, {ds.truth['bank_line_count']} bank lines, "
               f"{len(ds.truth['associations'])} ground-truth associations")


@app.command()
def evaluate(
    truth: Path = typer.Argument(Path("demo/ground_truth.json"), exists=True, help="Ground truth JSON"),
    orders: Path | None = typer.Option(None, help="Orders file (default: next to the ground truth)"),
    bank: Path | None = typer.Option(None, help="Bank file (default: next to the ground truth)"),
    min_precision: float = typer.Option(0.0, help="Exit 3 if automatic precision is below this"),
    output: Path | None = typer.Option(None, help="Write metrics JSON here"),
) -> None:
    """Measure precision / recall of automatic matching against the generator's ground truth."""
    from reconcileai.evaluation import evaluate as run_eval
    from reconcileai.reconciliation.service import reconcile

    gt = json.loads(truth.read_text(encoding="utf-8"))
    base = truth.parent / "data" / ("clean" if not gt.get("challenging", True) else "")
    orders = orders or base / "orders.xlsx"
    bank = bank or base / "bank_statement.xlsx"
    settings = Settings()
    o, b, lo, lb = load_pair(orders, bank, settings)
    t0 = time.perf_counter()
    rs = reconcile(o, b, settings)
    elapsed = time.perf_counter() - t0
    m = run_eval(rs, gt)
    m["reconcile_seconds"] = round(elapsed, 4)
    a = m["automatic"]

    def pct(x):
        return "n/a" if x is None else f"{x * 100:.1f}%"

    typer.echo(f"Ground-truth associations: {m['ground_truth_pairs']}  (unit: {m['matching_unit']})")
    typer.echo(f"Automatic  precision {pct(a['precision'])}  recall {pct(a['recall'])}  F1 {pct(a['f1'])}  "
               f"(correct {a['tp']}, incorrect {a['fp']}, missed {a['fn']})")
    for rule, r in m["automatic_by_rule"].items():
        typer.echo(f"  {rule:<18} {r['auto_pairs']:>4} auto pairs, {r['incorrect']} incorrect, precision {pct(r['precision'])}")
    rc = m["review_coverage"]
    typer.echo(f"Review coverage: {pct(rc['coverage'])} of true associations matched or suggested "
               f"({rc['suggested_pairs']} suggested pairs, {rc['suggested_correct']} correct)")
    typer.echo(f"Duplicates: precision {pct(m['duplicate_detection']['precision'])}, "
               f"recall {pct(m['duplicate_detection']['recall'])}")
    typer.echo(f"Payments on closed orders flagged: {m['closed_order_payments']['flagged']}/"
               f"{m['closed_order_payments']['total']}, wrongly allocated: "
               f"{len(m['closed_order_payments']['wrongly_allocated'])}")
    typer.echo(f"Integrity: {'passed' if m['integrity_ok'] else 'FAILED'}   reconcile time {elapsed:.3f}s")
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(m, indent=2), encoding="utf-8")
        typer.echo(f"Wrote {output}")
    if not m["integrity_ok"]:
        raise typer.Exit(2)
    if a["precision"] is not None and a["precision"] < min_precision:
        raise typer.Exit(3)


if __name__ == "__main__":  # pragma: no cover
    app()
