"""Reconciliation workspace: orders and bank lines side by side, evidence and decisions below."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from functools import partial

import pandas as pd
import streamlit as st

from reconcileai.models import Status, TxnKind
from reconcileai.text import compact, normalize_text
from reconcileai.ui import components as ui
from reconcileai.ui import state
from reconcileai.ui.theme import tok

STATUS_ORDER = [s.value for s in Status]


def _select_cb(kind: str, key: str) -> None:
    rows = st.session_state[key]["selection"]["rows"]
    ids = st.session_state.get(f"ids_{kind}", [])
    st.session_state[f"sel_{kind}"] = ids[rows[0]] if rows and rows[0] < len(ids) else None


def _run_button():
    if st.button("Run matching", type="primary", help="Re-run all matching stages. Review decisions are kept."):
        with st.spinner("Matching…"):
            rs = state.run_now()
        if rs:
            st.toast(f"Matching complete: {len(rs.candidates)} candidates, {len(rs.allocations)} allocations")
        st.rerun()


def _parse_amount(text: str) -> Decimal | None:
    t = text.replace(",", "").replace(".", "").replace(" ", "").strip()
    if not t:
        return None
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def render() -> None:
    rs, reason = state.current()
    ui.header("Reconciliation", "Select an order or a bank line to see its evidence and decide.", _run_button)
    if rs is None:
        ui.empty("No matching results yet" if reason == "stale" else "Nothing to reconcile yet",
                 "Run matching to generate candidates." if reason == "stale"
                 else "Import both files on the Import data page, or load the demo dataset there.")
        return
    cur = rs.settings.currency

    all_dates = [o.order_date for o in rs.orders] + [t.txn_date for t in rs.txns]
    f1, f2, f3, f4, f5 = st.columns([2.2, 1.6, 1, 1, 1.8])
    statuses = f1.multiselect("Status", STATUS_ORDER, key="f_status", placeholder="All statuses")
    dr = f2.date_input("Date range", value=(min(all_dates), max(all_dates)), key="f_dates",
                       min_value=min(all_dates), max_value=max(all_dates))
    amin = _parse_amount(f3.text_input("Min amount", key="f_amin", placeholder="0"))
    amax = _parse_amount(f4.text_input("Max amount", key="f_amax", placeholder="any"))
    search = f5.text_input("Search", key="f_search", placeholder="ID, customer or description")
    related_only = st.toggle("Show only records related to the selection", key="f_related")
    # Table widget keys change with the filters, so a stale row selection never carries over.
    fkey = abs(hash((tuple(statuses), str(dr), str(amin), str(amax), search, related_only,
                     st.session_state.get("sel_order") if related_only else "",
                     st.session_state.get("sel_txn") if related_only else ""))) % 10**8

    sel_o, sel_t = st.session_state.get("sel_order"), st.session_state.get("sel_txn")
    rel_t: set[str] = set()
    rel_o: set[str] = set()
    for c in rs.candidates.values():
        if sel_o and sel_o in c.order_ids:
            rel_t |= set(c.txn_ids)
        if sel_t and sel_t in c.txn_ids:
            rel_o |= set(c.order_ids)
    d0, d1 = (dr if isinstance(dr, tuple) and len(dr) == 2 else (min(all_dates), max(all_dates)))
    q, qc = normalize_text(search), compact(search)

    def keep(status: str, d: date, amount: Decimal, text: str) -> bool:
        if statuses and status not in statuses:
            return False
        if not (d0 <= d <= d1):
            return False
        if amin is not None and amount < amin:
            return False
        if amax is not None and amount > amax:
            return False
        return not q or q in normalize_text(text) or (bool(qc) and qc in compact(text))

    orders = [v for v in rs.order_views.values()
              if keep(v.status.value, v.order.order_date, v.order.amount,
                      f"{v.order.order_id} {v.order.customer}")]
    txns = [v for v in rs.txn_views.values()
            if keep(v.status.value, v.txn.txn_date, v.txn.credit or v.txn.debit,
                    f"{v.txn.txn_id} {v.txn.description}")]
    if related_only and sel_o:
        txns = [v for v in rs.txn_views.values() if v.txn.txn_id in rel_t or v.txn.txn_id == sel_t]
    if related_only and sel_t:
        orders = [v for v in rs.order_views.values() if v.order.order_id in rel_o or v.order.order_id == sel_o]

    t = tok()
    left, right = st.columns(2, gap="medium")
    with left:
        st.subheader(f"Sales orders  ({len(orders):,} of {len(rs.order_views):,})")
        df = pd.DataFrame([{
            "Order ID": v.order.order_id, "Date": v.order.order_date, f"Amount ({cur})": v.order.amount,
            "Status": v.status.value, "Customer": v.order.customer,
        } for v in orders])
        st.session_state.ids_order = df["Order ID"].tolist() if not df.empty else []
        if df.empty:
            st.caption("No orders match the filters.")
        else:
            df[f"Amount ({cur})"] = ui.amount_display(df[f"Amount ({cur})"], cur)
            sty = ui.styled(df, [f"Amount ({cur})"], cur)
            sty = sty.apply(lambda r: [f"background-color: {t['bg-selected']}" if r["Order ID"] == sel_o
                                       else (f"background-color: {t['bg-related']}" if r["Order ID"] in rel_o else "")
                                       for _ in r], axis=1)
            st.dataframe(sty, hide_index=True, placeholder="—", height=min(420, 40 + 35 * len(df)), key=f"tbl_order_{fkey}",
                         on_select=partial(_select_cb, "order", f"tbl_order_{fkey}"),
                         selection_mode="single-row",
                         column_config={"Order ID": st.column_config.TextColumn(width=112),
                                        "Date": st.column_config.DateColumn(format="YYYY-MM-DD", width=88),
                                        f"Amount ({cur})": st.column_config.NumberColumn(width=104),
                                        "Status": st.column_config.TextColumn(width=128),
                                        "Customer": st.column_config.TextColumn(width=180)})
    with right:
        st.subheader(f"Bank transactions  ({len(txns):,} of {len(rs.txn_views):,})")
        df = pd.DataFrame([{
            "Transaction ID": v.txn.txn_id, "Date": v.txn.txn_date,
            f"Amount ({cur})": v.txn.credit if v.txn.credit else -v.txn.debit,
            "Status": v.status.value, "Description": v.txn.description,
        } for v in txns])
        st.session_state.ids_txn = df["Transaction ID"].tolist() if not df.empty else []
        if df.empty:
            st.caption("No bank lines match the filters.")
        else:
            df[f"Amount ({cur})"] = ui.amount_display(df[f"Amount ({cur})"], cur)
            sty = ui.styled(df, [f"Amount ({cur})"], cur)
            sty = sty.apply(lambda r: [f"background-color: {t['bg-selected']}" if r["Transaction ID"] == sel_t
                                       else (f"background-color: {t['bg-related']}" if r["Transaction ID"] in rel_t else "")
                                       for _ in r], axis=1)
            st.dataframe(sty, hide_index=True, placeholder="—", height=min(420, 40 + 35 * len(df)), key=f"tbl_txn_{fkey}",
                         on_select=partial(_select_cb, "txn", f"tbl_txn_{fkey}"),
                         selection_mode="single-row",
                         column_config={"Transaction ID": st.column_config.TextColumn(width=96),
                                        "Date": st.column_config.DateColumn(format="YYYY-MM-DD", width=88),
                                        f"Amount ({cur})": st.column_config.NumberColumn(width=104),
                                        "Status": st.column_config.TextColumn(width=128),
                                        "Description": st.column_config.TextColumn(width=320)})
    st.caption("Debits (fees, refunds, internal transfers) are shown as negative amounts and are excluded from "
               "sales matching.")

    st.divider()
    if not sel_o and not sel_t:
        st.subheader("Match details")
        ui.empty("Select a record", "Choose an order or a bank line above to see source values, normalized "
                 "values, the evidence behind each candidate match, and review actions. Selecting one of each "
                 "lets you create a manual match.")
        return
    title = " and ".join(x for x in (sel_o, sel_t) if x)
    hc, bc = st.columns([5, 1], vertical_alignment="bottom")
    hc.subheader(f"Match details — {title}")
    if bc.button("Clear selection"):
        st.session_state.sel_order = st.session_state.sel_txn = None
        st.rerun()
    if sel_t and sel_t in rs.txn_views and rs.txn_views[sel_t].txn.kind != TxnKind.CREDIT:
        st.caption(f"{sel_t} is a {rs.txn_views[sel_t].txn.kind.value} line and is not matched to sales orders.")
    ui.review_panel(rs, sel_o, sel_t, key="rec")
