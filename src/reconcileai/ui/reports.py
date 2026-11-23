"""Reports: integrity gate, exports and audit history."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from reconcileai.reports.exporters import export_csv_zip, export_json, export_xlsx
from reconcileai.reports.tables import build_tables
from reconcileai.ui import components as ui
from reconcileai.ui import state


@st.cache_data(show_spinner=False, max_entries=4)
def _exports(_rs, cache_key: str) -> tuple[bytes, bytes, bytes]:
    tables = build_tables(_rs)
    return export_xlsx(_rs, tables), export_csv_zip(_rs, tables), export_json(_rs, tables)


def render() -> None:
    rs, reason = state.current()
    ui.header("Reports", "Exports contain exactly the figures shown in the app. "
              "They are generated only when every integrity check passes.")
    if rs is None:
        ui.empty("No results to report", "Import data and run matching first.")
        return

    st.subheader("Integrity checks")
    st.html("".join(
        f"<div class='ra-check'><span class='{'ok' if i.passed else 'bad'}'>{'✓' if i.passed else '✗'}</span>"
        f"<span>{ui.e(i.name)}</span><span class='d'>{ui.e(i.detail)}</span></div>" for i in rs.invariants))

    if not rs.integrity_ok:
        st.error("Export is blocked because an integrity check failed. Resolve the issue above and re-run matching.")
        return

    st.subheader("Export")
    key = f"{rs.match.input_hash}-{len(rs.decisions)}-{rs.decisions[-1].decision_id if rs.decisions else 0}"
    with st.spinner("Building reports…"):
        xlsx, csvzip, js = _exports(rs, key)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    c1, c2, c3, _ = st.columns([1.3, 1.3, 1.3, 3])
    c1.download_button("Download Excel (.xlsx)", xlsx, file_name=f"reconciliation_{stamp}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary")
    c2.download_button("Download CSV (.zip)", csvzip, file_name=f"reconciliation_{stamp}_csv.zip",
                       mime="application/zip")
    c3.download_button("Download JSON", js, file_name=f"reconciliation_{stamp}.json", mime="application/json")
    st.caption("Workbook sheets: Summary, Matched, Unmatched Orders, Unmatched Transactions, Amount Mismatches, "
               "Duplicate Candidates, Review Log, Configuration, Integrity. Text cells that start with = + − @ are "
               "prefixed with an apostrophe so spreadsheet programs never execute them as formulas.")

    tables = build_tables(rs)
    st.subheader("Preview")
    tabs = st.tabs(list(tables.sheets()))
    cur = tables.currency
    for tab, (name, df) in zip(tabs, tables.sheets().items(), strict=True):
        with tab:
            if df.empty:
                st.caption("No rows.")
                continue
            view = df.copy()
            money_cols = [c for c in view.columns if c.endswith(f"({cur})")]
            for c in money_cols:
                view[c] = ui.amount_display(view[c], cur)
            if name == "Summary":
                view["Value"] = view["Value"].map(lambda v: ui.money(v, cur) if hasattr(v, "as_tuple") else str(v))
            st.dataframe(ui.styled(view, money_cols, cur, ("Status", "Order status")), hide_index=True, placeholder="—",
                         height=min(420, 38 + 35 * len(view)))

    st.subheader("Audit trail")
    log = state.workspace().audit_log(300)
    if log:
        st.dataframe(pd.DataFrame([{
            "When (UTC)": r["at"][:19].replace("T", " "), "Event": r["event"].replace("_", " "),
            "Detail": ", ".join(f"{k}: {v}" for k, v in r["detail"].items() if v not in ("", [], None))[:300],
        } for r in log]), hide_index=True, placeholder="—", height=300,
            column_config={"Detail": st.column_config.TextColumn(width="large")})
    runs = state.workspace().runs(10)
    if runs:
        st.caption("Recent matching runs: " + "; ".join(
            f"#{r['id']} at {r['started_at'][:16].replace('T', ' ')} UTC (inputs {r['input_hash']}, "
            f"integrity {'ok' if r['integrity_ok'] else 'failed'})" for r in runs[:5]))
