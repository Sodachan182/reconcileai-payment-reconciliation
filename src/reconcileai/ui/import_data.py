"""Two-source import: upload → map columns → validate → confirm."""

from __future__ import annotations

import hashlib

import pandas as pd
import streamlit as st

from reconcileai.ingestion.column_mapper import (
    FIELDS,
    apply_template,
    load_templates,
    missing_required,
    save_template,
    suggest_mapping,
)
from reconcileai.ingestion.normalizer import DATE_FORMATS, normalize_bank, normalize_orders
from reconcileai.ingestion.readers import FileReadError, list_sheets, read_table
from reconcileai.ui import components as ui
from reconcileai.ui import state

NONE = "— not in file —"


def _steps(current: int) -> None:
    names = ["Upload", "Map columns", "Validate", "Import"]
    parts = []
    for i, n in enumerate(names, start=1):
        cls = "done" if i < current else ("on" if i == current else "")
        parts.append(f"<span class='{cls}'>{i}. {n}</span>")
    st.html(f"<div class='ra-steps'>{''.join(parts)}</div>")


def _source(kind: str) -> None:
    label = "sales orders" if kind == "orders" else "bank statement"
    key = f"import_{kind}"
    up = st.file_uploader(f"Upload the {label} (.xlsx or .csv, up to 25 MB)", type=["xlsx", "csv"], key=f"up_{kind}")
    if up is None:
        _steps(1)
        st.caption("Vietnamese and English headers are recognised, e.g. "
                   + ("Mã đơn hàng, Tên khách hàng, Số tiền, Ngày đặt hàng." if kind == "orders"
                      else "Ngày giao dịch, Nội dung, Tiền vào, Tiền ra, Số dư."))
        return
    data = up.getvalue()
    # Widget keys include the file's content hash so mapping choices never leak from a previous file.
    fk = hashlib.sha256(data).hexdigest()[:10]
    sheet = None
    try:
        if up.name.lower().endswith(".xlsx"):
            sheets = list_sheets(data)
            sheet = st.selectbox("Worksheet", sheets, key=f"sheet_{kind}_{fk}") if len(sheets) > 1 else sheets[0]
        table = read_table(data, up.name, sheet=sheet)
    except FileReadError as exc:
        _steps(1)
        st.error(str(exc))
        return

    meta = [f"{len(table.frame):,} data rows", f"{len(table.frame.columns)} columns"]
    if table.encoding:
        meta.append(f"encoding {table.encoding}")
    if table.delimiter:
        meta.append("delimiter " + {"\t": "tab", ",": "comma", ";": "semicolon", "|": "pipe"}.get(table.delimiter, table.delimiter))
    if table.sheet:
        meta.append(f"sheet '{table.sheet}'")
    st.caption(", ".join(meta))
    st.dataframe(table.frame.head(8).astype(str), hide_index=False, height=250)

    headers = list(table.frame.columns)
    templates = {n: t for n, t in load_templates(state.TEMPLATES_PATH).items() if t.get("kind") == kind}
    tcol, _ = st.columns([2, 3])
    tname = tcol.selectbox("Mapping template", ["Suggested from headers", *templates], key=f"tpl_{kind}_{fk}")
    base = suggest_mapping(headers, kind) if tname == "Suggested from headers" else apply_template(templates[tname], headers)

    st.subheader("Map columns")
    mapping: dict[str, str | None] = {}
    specs = FIELDS[kind]
    cols = st.columns(2)
    for i, spec in enumerate(specs):
        options = [NONE, *headers]
        default = base.get(spec.name)
        idx = options.index(default) if default in options else 0
        with cols[i % 2]:
            choice = st.selectbox(spec.label + (" *" if spec.required else ""), options, index=idx,
                                  key=f"map_{kind}_{spec.name}_{tname}_{fk}_{table.sheet}")
            mapping[spec.name] = None if choice == NONE else choice
            if mapping[spec.name]:
                samples = [str(v) for v in table.frame[mapping[spec.name]].head(3).tolist()]
                st.caption("e.g. " + " | ".join(s[:40] for s in samples))
    dcol, ncol = st.columns([1, 2])
    default_fmt = (templates[tname].get("date_format", "auto") if tname in templates else "auto")
    date_format = dcol.selectbox("Date format", DATE_FORMATS, index=DATE_FORMATS.index(default_fmt),
                                 key=f"df_{kind}_{tname}_{fk}",
                                 help="'auto' reads ISO dates and decides day/month order from the whole column. "
                                      "If every slash date could be either, you must choose.")
    with ncol, st.popover("Save as template"):
        name = st.text_input("Template name", key=f"tplname_{kind}")
        if st.button("Save template", key=f"tplsave_{kind}", disabled=not name.strip()):
            save_template(state.TEMPLATES_PATH, name.strip(), kind, mapping, date_format)
            st.toast(f"Saved template '{name.strip()}'")

    missing = missing_required(mapping, kind)
    dup_cols = {c for c in mapping.values() if c and list(mapping.values()).count(c) > 1}
    if missing:
        _steps(2)
        st.warning("Map the required fields: " + ", ".join(missing) + ".")
        return
    if dup_cols:
        _steps(2)
        st.warning("Each column can be mapped to one field only: " + ", ".join(dup_cols) + ".")
        return

    s = state.settings()
    if kind == "orders":
        records, report = normalize_orders(table, mapping, s, date_format)
    else:
        records, report = normalize_bank(table, mapping, s, date_format)

    st.subheader("Validate")
    ui.strip([("Rows in file", f"{report.total_rows:,}", ""), ("Valid", f"{report.valid_rows:,}", ""),
              ("Rejected rows", f"{report.rejected_rows:,}", ""),
              ("Exact duplicates dropped", f"{report.duplicate_rows_dropped:,}", "")], compact=True)
    for b in report.blocking:
        st.error(b)
    suggested = suggest_mapping(headers, kind)
    for fname, label in (("status", "order status"), ("currency", "currency"), ("debit", "outgoing amounts")):
        if fname in mapping and not mapping[fname] and suggested.get(fname):
            st.warning(f"Column '{suggested[fname]}' looks like {label} but is not mapped. "
                       + ("Every order will be treated as open (cancelled and refunded orders included)."
                          if fname == "status" else "Map it unless you are sure it is not needed."))
    if report.errors:
        st.warning(f"{report.rejected_rows} row(s) will not be imported. Fix them in the source file and re-import; "
                   "already-imported rows are not duplicated.")
        st.dataframe(pd.DataFrame([{"Row": x.row, "Field": x.field, "Problem": x.message, "Value": x.value}
                                   for x in report.errors]), hide_index=True, placeholder="—", height=min(300, 38 + 35 * len(report.errors)))
    if report.warnings:
        with st.expander(f"{len(report.warnings)} note(s)"):
            for w in report.warnings[:200]:
                st.write(w)
    if not report.ok:
        _steps(3)
        return

    _steps(4)
    if st.button(f"Import {report.valid_rows:,} rows", type="primary", key=f"go_{kind}_{fk}"):
        ws = state.workspace()
        res = ws.import_orders(records, report) if kind == "orders" else ws.import_bank(records, report)
        st.session_state[key] = {**res, "file": fk}
        st.session_state.pop("match", None)
    res = st.session_state.get(key)
    if res and res.get("file") == fk:
        msg = f"Imported {res['added']:,} new rows; {res['unchanged']:,} were already in the workspace unchanged."
        if res["conflicting"]:
            st.error(msg + f" {len(res['conflicting'])} ID(s) already exist with different values and were not "
                     f"changed: {', '.join(res['conflicting'][:10])}")
        else:
            st.success(msg + " Open Reconciliation and run matching.")


def render() -> None:
    counts = state.workspace().counts()

    def actions():
        if st.button("Load demo dataset", type="primary" if not counts["orders"] else "secondary",
                     help="Imports the bundled fictional orders and bank statement (seed 2026) and runs matching."):
            with st.spinner("Importing fictional demo data and matching…"):
                r = state.load_demo()
            st.toast(f"Demo loaded: {r['orders']['added']} orders, {r['bank']['added']} bank lines added")
            st.rerun()

    ui.header("Import data", f"Workspace holds {counts['orders']:,} orders and {counts['transactions']:,} bank "
              f"transactions. Re-importing the same rows never duplicates them.", actions)

    tab_o, tab_b, tab_h = st.tabs(["Sales / orders", "Bank transactions", "Import history"])
    with tab_o:
        _source("orders")
    with tab_b:
        _source("bank")
    with tab_h:
        hist = state.workspace().import_sessions()
        if hist:
            st.dataframe(pd.DataFrame([{
                "Imported (UTC)": h["imported_at"][:19].replace("T", " "), "Type": h["kind"], "File": h["source_name"],
                "Rows": h["rows_total"], "Added": h["rows_added"], "Unchanged": h["rows_unchanged"],
                "Conflicting": h["rows_conflicting"], "Rejected": h["rows_rejected"],
            } for h in hist]), hide_index=True)
        else:
            st.caption("No imports yet.")
        st.divider()
        st.subheader("Clear workspace")
        st.caption("Deletes all imported records, matching runs and review decisions from the local database. "
                   "The audit log keeps an entry recording the clear.")
        ok = st.checkbox("I understand this cannot be undone", key="clear_ok")
        if st.button("Clear workspace", disabled=not ok):
            state.clear_workspace()
            st.toast("Workspace cleared")
            st.rerun()
