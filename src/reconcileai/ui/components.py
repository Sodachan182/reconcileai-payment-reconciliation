"""Reusable UI pieces: header, equation, metric strip, tables, review panel."""

from __future__ import annotations

import html
from decimal import Decimal

import pandas as pd
import streamlit as st

from reconcileai.models import Rule
from reconcileai.money import format_amount, precision
from reconcileai.reconciliation.service import (
    ACCEPTED_STATES,
    AUTO,
    BLOCKED,
    CONFIRMED,
    INVESTIGATE,
    PENDING,
    REJECTED,
    SUPERSEDED,
    ReconciliationState,
)
from reconcileai.ui import state
from reconcileai.ui.theme import status_color, tok

RULE_LABEL = {
    Rule.EXACT_REFERENCE: "Exact reference", Rule.SPLIT_REFERENCE: "Referenced split / instalments",
    Rule.MULTI_REFERENCE: "One payment, several referenced orders", Rule.REFERENCE_MISMATCH: "Reference, amount differs",
    Rule.AMOUNT_DATE: "Amount and date", Rule.FUZZY: "Name similarity (fuzzy)", Rule.COMPOSITE: "Composite sum",
    Rule.MANUAL: "Manual match",
}
STATE_LABEL = {AUTO: "Confirmed by rule", CONFIRMED: "Confirmed by reviewer", REJECTED: "Rejected",
               PENDING: "Awaiting review", INVESTIGATE: "Needs investigation", BLOCKED: "Blocked",
               SUPERSEDED: "Superseded"}
STATE_TONE = {AUTO: "success", CONFIRMED: "success", REJECTED: "text-muted", PENDING: "warning",
              INVESTIGATE: "warning", BLOCKED: "danger", SUPERSEDED: "text-muted"}

e = html.escape


def header(title: str, sub: str = "", action=None):
    """Compact page header. ``action`` is a callable rendering the primary control on the right."""
    left, right = st.columns([5, 2], vertical_alignment="bottom")
    with left:
        st.title(title)
        if sub:
            st.html(f'<p class="ra-sub">{e(sub)}</p>')
    if action:
        with right:
            return action()
    return None


def money(v: Decimal | None, cur: str) -> str:
    return "" if v is None else format_amount(v, cur)


def equation(rs: ReconciliationState) -> None:
    s = rs.summary
    cur = s.currency
    zero = " zero" if s.unresolved_total == 0 else ""
    st.html(f"""
<div class="ra-eq" role="group" aria-label="Reconciliation equation">
  <div class="term"><span class="val">{money(s.expected_total, cur)}</span><span class="lbl">Expected from {s.open_orders} orders</span></div>
  <span class="op" aria-hidden="true">−</span>
  <div class="term"><span class="val">{money(s.applied_total, cur)}</span><span class="lbl">Confirmed payments applied</span></div>
  <span class="op" aria-hidden="true">=</span>
  <div class="term result{zero}"><span class="val">{money(s.unresolved_total, cur)}</span><span class="lbl">Unresolved</span></div>
  <span class="cur">{cur}</span>
</div>
<div class="ra-eq2">Bank side, kept separate: incoming <b>{money(s.incoming_total, cur)}</b> − allocated
<b>{money(s.allocated_incoming, cur)}</b> = unmatched incoming <b>{money(s.unallocated_incoming, cur)}</b> {cur}.
Overpayments {money(s.overpaid_total, cur)} {cur}. {s.excluded_orders} cancelled or refunded orders are out of scope.</div>
""")


def strip(items: list[tuple[str, str, str]], compact: bool = False) -> None:
    """Bordered metric strip. items = [(label, value, unit)]."""
    cells = "".join(f'<div class="cell"><div class="k">{e(k)}</div><div class="v">{e(v)}'
                    f'{f"<small>{e(u)}</small>" if u else ""}</div></div>' for k, v, u in items)
    st.html(f'<div class="ra-strip{" compact" if compact else ""}">{cells}</div>')


def status_html(status: str) -> str:
    return f'<span class="ra-status"><span class="ra-dot" style="background:{status_color(status)}"></span>{e(status)}</span>'


def empty(title: str, body: str) -> None:
    st.html(f'<div class="ra-empty"><b>{e(title)}</b>{e(body)}</div>')


def amount_display(series: pd.Series, cur: str) -> pd.Series:
    """Exact Decimal → display-safe numeric. VND (0 decimals) becomes int, exactly."""
    def blank(v) -> bool:
        return v is None or v is pd.NA or (isinstance(v, float) and v != v)

    if precision(cur) == 0:
        return pd.array([pd.NA if blank(v) else int(v) for v in series], dtype="Int64")
    # Display only (never used for arithmetic): minor-unit currencies shown via nullable float.
    return pd.array([pd.NA if blank(v) else float(v) for v in series], dtype="Float64")


def styled(df: pd.DataFrame, money_cols: list[str], cur: str, status_cols: tuple[str, ...] = ("Status",)):
    """Pandas Styler: thousands-separated money (numeric sort preserved) and status colours."""
    sty = df.style
    fmt = {c: (lambda v: "" if v is None or v is pd.NA or v != v else format_amount(Decimal(str(v)), cur))
           for c in money_cols if c in df}
    sty = sty.format(fmt, na_rep="")
    t = tok()
    for c in status_cols:
        if c in df:
            sty = sty.map(lambda v: f"color: {status_color(v)}", subset=[c])
    if "Severity" in df:
        sev = {"High": t["danger"], "Medium": t["warning"], "Low": t["text-muted"]}
        sty = sty.map(lambda v: f"color: {sev.get(v, '')}", subset=["Severity"])
    for c in money_cols:
        if c.startswith("Difference") and c in df:
            sty = sty.map(lambda v: f"color: {t['danger']}" if v is not None and v is not pd.NA and v == v and v < 0
                          else "", subset=[c])
    return sty


# --------------------------------------------------------------------------------------
# Review panel (shared by Reconciliation and Exceptions)
# --------------------------------------------------------------------------------------

def compare_table(rs: ReconciliationState, order_ids: list[str], txn_ids: list[str]) -> None:
    """Side-by-side source values: orders on the left, bank lines on the right."""
    cur = rs.settings.currency
    ov = [rs.order_views[o] for o in order_ids if o in rs.order_views][:3]
    tv = [rs.txn_views[t] for t in txn_ids if t in rs.txn_views][:3]
    if not ov and not tv:
        return
    cols = [("o", v) for v in ov] + [("t", v) for v in tv]

    def cell(kind, v, field):
        if kind == "o":
            o = v.order
            return {
                "head": f"Order {e(o.order_id)}",
                "party": f"{e(o.customer)}<div class='muted'>{e(o.customer_norm)}</div>",
                "date": o.order_date.isoformat(),
                "amount": f"<span class='num'>{money(o.amount, cur)}</span><div class='muted'>expected</div>",
                "alloc": f"{money(v.allocated, cur)}<div class='muted'>allocated</div>",
                "status": status_html(v.status.value) + (f"<div class='muted'>source: {e(o.raw_status)}</div>"
                                                         if o.raw_status else ""),
            }[field]
        t = v.txn
        amt = t.credit if t.credit else -t.debit
        return {
            "head": f"Bank {e(t.txn_id)}",
            "party": f"{e(t.description)}<div class='muted'>{e(t.description_norm)}</div>",
            "date": t.txn_date.isoformat(),
            "amount": f"{money(amt, cur)}<div class='muted'>{'received' if t.credit else 'paid out'}</div>",
            "alloc": f"{money(v.unallocated, cur)}<div class='muted'>not yet allocated</div>",
            "status": status_html(v.status.value) + (f"<div class='muted'>{e(v.note)}</div>" if v.note else ""),
        }[field]

    labels = [("party", "Customer / description"), ("date", "Date"), ("amount", f"Amount ({cur})"),
              ("alloc", f"Allocation ({cur})"), ("status", "Status")]
    num = {"amount", "alloc"}
    head = "<tr><th></th>" + "".join(f"<th>{cell(k, v, 'head')}</th>" for k, v in cols) + "</tr>"
    body = "".join(
        f"<tr><td class='k'>{lab}</td>" + "".join(
            f"<td class='{'num' if f in num else ''}'>{cell(k, v, f)}</td>" for k, v in cols) + "</tr>"
        for f, lab in labels)
    st.html(f"<div style='overflow-x:auto'><table class='ra-cmp'>{head}{body}</table></div>")
    if ov and tv:
        o, t = ov[0].order, tv[0].txn
        days = (t.txn_date - o.order_date).days
        diff = sum((x.txn.credit for x in tv), start=Decimal(0)) - sum((x.order.amount for x in ov), start=Decimal(0))
        st.html(f"<div style='font-size:13px;color:var(--text-secondary);margin-top:8px'>Date difference "
                f"{days:+d} day(s). Amount difference {format_amount(diff, cur, signed=True)} {cur}"
                f"{' (equal)' if diff == 0 else ''}.</div>")


def candidate_block(rs: ReconciliationState, cid: str, key: str) -> None:
    c = rs.candidates[cid]
    s = rs.cand_state[cid]
    t = tok()
    tone = t[STATE_TONE[s]]
    score = f"score {c.score:.0f}/100 (ranking heuristic)" if c.rule in (Rule.AMOUNT_DATE, Rule.FUZZY) else ""
    st.html(
        f"<div style='font-size:14px;font-weight:500;margin-top:6px'>{e(RULE_LABEL.get(c.rule, c.rule.value))}"
        f"<span style='font-weight:400;color:var(--text-secondary)'> &nbsp;{e(', '.join(c.order_ids))} ↔ {e(', '.join(c.txn_ids))}</span></div>"
        f"<div style='font-size:13px;margin-top:2px'><span class='ra-dot' style='background:{tone}'></span>"
        f"{e(STATE_LABEL[s])}{' — ' + e(rs.cand_reason[cid]) if cid in rs.cand_reason else ''}"
        f"<span style='color:var(--text-muted)'>{' · ' + e(score) if score else ''}</span></div>"
        "<ul class='ra-ev'>" + "".join(
            f"<li><span class='f'>{e(ev.factor)}</span><span>{e(ev.detail)}</span>"
            f"<span class='p'>{f'+{ev.points:g}' if ev.points else ''}</span></li>" for ev in c.evidence) + "</ul>"
        + (f"<div style='font-size:12px;color:var(--text-muted);margin-top:6px'>Competes with {len(c.conflict_with)} other "
           f"candidate(s); confirming one supersedes the others.</div>" if c.conflict_with else "")
    )
    note = st.text_input("Review note (optional)", key=f"note_{key}_{cid}", placeholder="Why this decision?",
                         label_visibility="collapsed")
    cols = st.columns([1, 1, 1.4, 1, 3])
    pending = s in (PENDING, INVESTIGATE)
    if pending and cols[0].button("Confirm", key=f"c_{key}_{cid}", type="primary"):
        state.decide(cid, "confirm", note)
        st.toast(f"Confirmed {', '.join(c.order_ids)} ↔ {', '.join(c.txn_ids)}")
        st.rerun()
    if s in (PENDING, INVESTIGATE, AUTO, BLOCKED) and cols[1].button("Reject", key=f"r_{key}_{cid}"):
        state.decide(cid, "reject", note)
        st.toast("Rejected")
        st.rerun()
    if s == PENDING and cols[2].button("Needs investigation", key=f"i_{key}_{cid}"):
        state.decide(cid, "investigate", note)
        st.toast("Marked for investigation")
        st.rerun()
    has_decision = any(d.candidate_id == cid for d in rs.decisions)
    if has_decision and s in (CONFIRMED, REJECTED, INVESTIGATE, BLOCKED) and cols[3].button("Undo", key=f"u_{key}_{cid}"):
        state.decide(cid, "undo", note)
        st.toast("Decision undone")
        st.rerun()


def review_panel(rs: ReconciliationState, order_id: str | None, txn_id: str | None, key: str) -> None:
    cids: list[str] = []
    for cid, c in rs.candidates.items():
        if (order_id and order_id in c.order_ids) or (txn_id and txn_id in c.txn_ids):
            cids.append(cid)
    order_rank = {AUTO: 0, CONFIRMED: 0, PENDING: 1, INVESTIGATE: 1, BLOCKED: 2, SUPERSEDED: 3, REJECTED: 4}
    cids.sort(key=lambda cid: (order_rank[rs.cand_state[cid]], -rs.candidates[cid].score))

    o_ids = {order_id} if order_id else set()
    t_ids = {txn_id} if txn_id else set()
    for cid in cids[:1]:
        o_ids |= set(rs.candidates[cid].order_ids)
        t_ids |= set(rs.candidates[cid].txn_ids)
    compare_table(rs, sorted(o_ids), sorted(t_ids))

    if order_id and order_id in rs.order_views and rs.order_views[order_id].note:
        st.caption(rs.order_views[order_id].note)
    if not cids:
        st.caption("No candidate matches were found for this record.")
    else:
        st.html(f"<div style='font-size:13px;color:var(--text-secondary);margin-top:14px'>"
                f"{len(cids)} candidate match{'es' if len(cids) != 1 else ''}</div>")
        for cid in cids:
            with st.container(border=True):
                candidate_block(rs, cid, key)

    # Manual match: an order and a payment chosen together without an existing candidate.
    if order_id and txn_id:
        existing = any(order_id in rs.candidates[c].order_ids and txn_id in rs.candidates[c].txn_ids for c in cids)
        ov, tv = rs.order_views.get(order_id), rs.txn_views.get(txn_id)
        if not existing and ov and tv and ov.counts and tv.unallocated > 0 and tv.txn.kind.value == "credit":
            with st.container(border=True):
                cur = rs.settings.currency
                st.html(f"<div style='font-size:14px;font-weight:500'>Manual match {e(order_id)} ↔ {e(txn_id)}</div>"
                        f"<div style='font-size:13px;color:var(--text-secondary)'>Allocates "
                        f"{money(tv.unallocated, cur)} {cur} of {e(txn_id)} to {e(order_id)} "
                        f"(expected {money(ov.order.amount, cur)} {cur}). Recorded in the review log.</div>")
                note = st.text_input("Reason", key=f"mnote_{key}", placeholder="Reason for manual match (required)")
                if st.button("Create manual match", key=f"mbtn_{key}", disabled=not note.strip()):
                    from reconcileai.reconciliation.service import manual_candidate

                    mc = manual_candidate((order_id,), (txn_id,), note)
                    state.decide(mc.candidate_id, "confirm", note, (order_id,), (txn_id,))
                    st.toast("Manual match recorded")
                    st.rerun()


def accepted_count(rs: ReconciliationState) -> int:
    return sum(1 for s in rs.cand_state.values() if s in ACCEPTED_STATES)
