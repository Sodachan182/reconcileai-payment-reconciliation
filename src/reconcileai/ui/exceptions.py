"""Exception management: every discrepancy in one sortable, filterable table."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from reconcileai.reconciliation.exceptions import build_exceptions
from reconcileai.reports.tables import sanitize_frame
from reconcileai.text import normalize_text
from reconcileai.ui import components as ui
from reconcileai.ui import state

KINDS = ["Missing payment", "Unmatched transaction", "Payment for closed order", "Amount mismatch", "Duplicate",
         "Partial payment", "Overpayment", "Ambiguous match", "Suggested match", "Unallocated remainder",
         "Needs investigation"]


def _cb() -> None:
    rows = st.session_state["tbl_exc"]["selection"]["rows"]
    keys = st.session_state.get("exc_keys", [])
    st.session_state.exc_sel = keys[rows[0]] if rows and rows[0] < len(keys) else None


def render() -> None:
    rs, reason = state.current()
    ui.header("Exceptions", "Discrepancies that need a decision, highest risk first. "
              "Suggestions are never counted as matched until confirmed.")
    if rs is None:
        ui.empty("No results to review", "Import data and run matching first.")
        return
    cur = rs.settings.currency
    items = build_exceptions(rs)
    counts = {k: sum(1 for x in items if x.kind == k) for k in KINDS}

    c1, c2, c3 = st.columns([3, 1, 1.4])
    kinds = c1.multiselect("Type", [k for k in KINDS if counts[k]], key="ex_kinds", placeholder="All types",
                           format_func=lambda k: f"{k} ({counts[k]})")
    sev = c2.multiselect("Severity", ["High", "Medium", "Low"], key="ex_sev", placeholder="All")
    search = normalize_text(c3.text_input("Search", key="ex_q", placeholder="ID, customer, text"))
    view = [x for x in items if (not kinds or x.kind in kinds) and (not sev or x.severity in sev)
            and (not search or search in normalize_text(f"{x.order_id} {x.txn_id} {x.customer} {x.explanation}"))]

    total_diff = sum((x.difference for x in view if x.difference is not None), start=0)
    ui.strip([("Exceptions shown", f"{len(view):,} of {len(items):,}", ""),
              ("High severity", f"{sum(1 for x in view if x.severity == 'High'):,}", ""),
              ("Net difference on order-side rows", ui.money(total_diff, cur), cur)], compact=True)
    if not view:
        ui.empty("No exceptions match", "Change the filters, or everything is reconciled.")
        return

    df = pd.DataFrame([{
        "Severity": x.severity, "Type": x.kind, "Status": x.status, "Order ID": x.order_id,
        "Transaction ID": x.txn_id, f"Expected ({cur})": x.expected, f"Received ({cur})": x.received,
        f"Difference ({cur})": x.difference, "Explanation": x.explanation,
    } for x in view])
    money_cols = [f"Expected ({cur})", f"Received ({cur})", f"Difference ({cur})"]
    for c in money_cols:
        df[c] = ui.amount_display(df[c], cur)
    st.session_state.exc_keys = [(x.order_id, x.txn_id) for x in view]
    st.dataframe(ui.styled(df, money_cols, cur, status_cols=()), hide_index=True, placeholder="—", height=440, key="tbl_exc", on_select=_cb,
                 selection_mode="single-row",
                 column_config={"Explanation": st.column_config.TextColumn(width="large"),
                                "Severity": st.column_config.TextColumn(width="small")})
    st.download_button("Download this list (CSV)", sanitize_frame(df).to_csv(index=False).encode("utf-8-sig"),
                       file_name="exceptions.csv", mime="text/csv")

    sel = st.session_state.get("exc_sel")
    st.divider()
    if not sel:
        st.subheader("Investigation")
        st.caption("Select a row to see source evidence and review it.")
        return
    oid, tid = sel
    oid = oid.split(", ")[0] or None
    tid = tid.split(", ")[0] or None
    st.subheader("Investigation — " + " and ".join(x for x in (oid, tid) if x))
    ui.review_panel(rs, oid, tid, key="exc")
