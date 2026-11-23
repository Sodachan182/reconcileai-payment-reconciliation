"""Overview dashboard — every number is computed from the current reconciliation state."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from reconcileai.models import Status, TxnKind
from reconcileai.money import format_amount
from reconcileai.reconciliation.exceptions import build_exceptions
from reconcileai.ui import components as ui
from reconcileai.ui import navigation, state
from reconcileai.ui.theme import plotly_layout, status_color, tok


def _no_data(reason: str) -> None:
    ui.header("Overview", "Reconcile sales orders against bank transactions.")
    if reason == "stale":
        ui.empty("Data changed since the last matching run",
                 "Run matching to refresh candidates. Your review decisions are kept and re-applied.")
        if st.button("Run matching", type="primary"):
            with st.spinner("Matching…"):
                state.run_now()
            st.rerun()
        return
    msg = {"empty": "Import a sales/orders file and a bank statement, or load the fictional demo dataset.",
           "no-bank": "Orders are imported. Import a bank statement to start reconciling.",
           "no-orders": "A bank statement is imported. Import the sales/orders file to start reconciling."}[reason]
    ui.empty("Nothing to reconcile yet", msg)
    c1, c2, _ = st.columns([1.2, 1, 4])
    if reason == "empty" and c1.button("Load demo dataset", type="primary"):
        with st.spinner("Importing fictional demo data and matching…"):
            state.load_demo()
        st.rerun()
    if c2.button("Go to import"):
        st.switch_page(navigation.PAGES["import"])


def render() -> None:
    rs, reason = state.current()
    if rs is None:
        _no_data(reason)
        return
    s = rs.summary
    cur = s.currency
    dates = [o.order_date for o in rs.orders] + [t.txn_date for t in rs.txns]
    ui.header("Overview", f"{min(dates):%d %b %Y} to {max(dates):%d %b %Y}, {len(rs.orders)} orders and "
              f"{len(rs.txns)} bank lines. "
              + ("All integrity checks passed." if rs.integrity_ok else "An integrity check FAILED."))
    if not rs.integrity_ok:
        st.error("An integrity check failed. Figures may be misleading; see Reports → Integrity.")
    ui.equation(rs)

    sc = s.status_counts
    tsc = s.txn_status_counts
    ui.strip([
        ("Total orders", f"{s.total_orders:,}", ""),
        ("Incoming bank transactions", f"{s.incoming_count:,}", ""),
        ("Confirmed matched amount", format_amount(s.applied_total, cur), cur),
        ("Unresolved difference", format_amount(s.unresolved_total, cur), cur),
    ])
    ui.strip([
        ("Matched orders", f"{sc.get(Status.MATCHED.value, 0):,}", ""),
        ("Unmatched orders", f"{sc.get(Status.UNMATCHED_ORDER.value, 0):,}", ""),
        ("Unmatched bank lines", f"{tsc.get(Status.UNMATCHED_TXN.value, 0):,}", ""),
        ("Amount mismatches", f"{sc.get(Status.AMOUNT_MISMATCH.value, 0) + sc.get(Status.OVERPAYMENT.value, 0):,}", ""),
        ("Awaiting review", f"{sc.get(Status.SUGGESTED.value, 0) + sc.get(Status.AMBIGUOUS.value, 0):,}", ""),
        ("Duplicate candidates", f"{tsc.get(Status.DUPLICATE.value, 0) + sc.get(Status.DUPLICATE.value, 0):,}", ""),
    ], compact=True)

    t = tok()
    c1, c2 = st.columns(2, gap="large")
    with c1:
        st.subheader("Order status")
        order = [Status.MATCHED, Status.PARTIAL, Status.OVERPAYMENT, Status.AMOUNT_MISMATCH, Status.SUGGESTED,
                 Status.AMBIGUOUS, Status.INVESTIGATE, Status.DUPLICATE, Status.UNMATCHED_ORDER, Status.EXCLUDED_ORDER]
        labels = [x.value for x in order if sc.get(x.value)]
        vals = [sc[v] for v in labels]
        fig = go.Figure(go.Bar(x=vals, y=labels, orientation="h", marker_color=[status_color(v) for v in labels],
                               text=vals, textposition="outside", cliponaxis=False,
                               hovertemplate="%{y}: %{x} orders<extra></extra>"))
        fig.update_layout(**plotly_layout(280, yaxis=dict(autorange="reversed", gridcolor="rgba(0,0,0,0)",
                                                          tickfont=dict(size=12)),
                                          xaxis=dict(visible=False)))
        st.plotly_chart(fig, config={"displayModeBar": False}, key="ov_status")
    with c2:
        st.subheader("Unresolved amount by reason")
        reasons = sorted(s.unresolved_by_reason.items(), key=lambda kv: -kv[1])
        if reasons:
            fig = go.Figure(go.Bar(
                x=[int(v) for _, v in reasons], y=[k for k, _ in reasons], orientation="h",
                marker_color=[status_color(k) for k, _ in reasons],
                text=[format_amount(v, cur) for _, v in reasons], textposition="outside", cliponaxis=False,
                hovertemplate="%{y}: %{text} " + cur + "<extra></extra>"))
            fig.update_layout(**plotly_layout(280, yaxis=dict(autorange="reversed", gridcolor="rgba(0,0,0,0)",
                                                              tickfont=dict(size=12)),
                                              xaxis=dict(visible=False), margin=dict(l=8, r=90, t=8, b=8)))
            st.plotly_chart(fig, config={"displayModeBar": False}, key="ov_reason")
        else:
            st.caption("Nothing unresolved.")

    st.subheader("Daily incoming payments")
    by_day: dict = defaultdict(lambda: [Decimal(0), Decimal(0)])
    for v in rs.txn_views.values():
        if v.txn.kind == TxnKind.CREDIT and v.txn.currency == cur:
            by_day[v.txn.txn_date][0] += v.allocated
            by_day[v.txn.txn_date][1] += v.unallocated
    days = sorted(by_day)
    fig = go.Figure()
    fig.add_bar(x=days, y=[int(by_day[d][0]) for d in days], name="Allocated to orders", marker_color=t["accent"],
                hovertemplate="%{x|%d %b}: %{y:,} " + cur + " allocated<extra></extra>")
    fig.add_bar(x=days, y=[int(by_day[d][1]) for d in days], name="Not allocated", marker_color=t["warning"],
                hovertemplate="%{x|%d %b}: %{y:,} " + cur + " not allocated<extra></extra>")
    fig.update_layout(**plotly_layout(240, barmode="stack", showlegend=True,
                                      legend=dict(orientation="h", y=1.12, x=0, font=dict(size=12)),
                                      yaxis=dict(gridcolor=t["border"], tickformat=",.0f", zeroline=False)))
    st.plotly_chart(fig, config={"displayModeBar": False}, key="ov_daily")

    st.subheader("Needs attention")
    ex = [x for x in build_exceptions(rs) if x.severity == "High"][:12]
    if not ex:
        st.caption("No high-severity exceptions.")
        return
    df = pd.DataFrame([{
        "Status": x.status, "Order ID": x.order_id, "Transaction ID": x.txn_id,
        f"Expected ({cur})": x.expected, f"Received ({cur})": x.received, f"Difference ({cur})": x.difference,
        "Explanation": x.explanation,
    } for x in ex])
    money_cols = [f"Expected ({cur})", f"Received ({cur})", f"Difference ({cur})"]
    for c in money_cols:
        df[c] = ui.amount_display(df[c], cur)
    st.caption("Select a row to open it in the reconciliation workspace.")
    ev = st.dataframe(ui.styled(df, money_cols, cur), hide_index=True, placeholder="—", on_select="rerun",
                      selection_mode="single-row", key="ov_attention",
                      column_config={"Explanation": st.column_config.TextColumn(width="large"),
                                     "Order ID": st.column_config.TextColumn(width="medium"),
                                     "Transaction ID": st.column_config.TextColumn(width="small"),
                                     "Status": st.column_config.TextColumn(width="medium")})
    rows = ev.selection.rows if ev and ev.selection else []
    if rows:
        x = ex[rows[0]]
        st.session_state.sel_order = x.order_id.split(", ")[0] or None
        st.session_state.sel_txn = x.txn_id.split(", ")[0] or None
        st.switch_page(navigation.PAGES["reconciliation"])
