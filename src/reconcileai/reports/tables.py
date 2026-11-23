"""Report tables built once from a reconciliation state and shared by every exporter
and by the UI, so exported numbers are exactly the numbers shown on screen."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pandas as pd

from reconcileai.models import Status
from reconcileai.reconciliation.exceptions import build_exceptions
from reconcileai.reconciliation.invariants import require_integrity
from reconcileai.reconciliation.service import ACCEPTED_STATES, ReconciliationState

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")


def safe_text(value):
    """Neutralise spreadsheet formula injection in text cells (OWASP CSV injection)."""
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


@dataclass
class ReportTables:
    summary: pd.DataFrame
    matched: pd.DataFrame
    unmatched_orders: pd.DataFrame
    unmatched_transactions: pd.DataFrame
    amount_mismatches: pd.DataFrame
    duplicates: pd.DataFrame
    review_log: pd.DataFrame
    configuration: pd.DataFrame
    integrity: pd.DataFrame
    currency: str

    def sheets(self) -> dict[str, pd.DataFrame]:
        return {
            "Summary": self.summary, "Matched": self.matched, "Unmatched Orders": self.unmatched_orders,
            "Unmatched Transactions": self.unmatched_transactions, "Amount Mismatches": self.amount_mismatches,
            "Duplicate Candidates": self.duplicates, "Review Log": self.review_log,
            "Configuration": self.configuration,
        }


def build_tables(rs: ReconciliationState, *, check_integrity: bool = True) -> ReportTables:
    if check_integrity:
        require_integrity(rs)
    s = rs.summary
    cur = s.currency
    include_raw = rs.settings.export_include_raw
    t_by = {t.txn_id: t for t in rs.txns}
    o_by = {o.order_id: o for o in rs.orders}
    exceptions = build_exceptions(rs)

    summary = pd.DataFrame([
        ("Currency", cur, ""),
        ("Total orders", s.total_orders, "All imported orders"),
        ("Orders in scope", s.open_orders, "Excludes cancelled / refunded orders"),
        ("Excluded orders", s.excluded_orders, "Cancelled, refunded, or other currency"),
        ("Total expected amount", s.expected_total, "Sum of in-scope order amounts"),
        ("Confirmed applied amount", s.applied_total, "Allocated payments, capped at each order amount"),
        ("Unresolved order amount", s.unresolved_total, "Expected − applied"),
        ("Overpaid amount", s.overpaid_total, "Allocated beyond order amounts"),
        ("Incoming customer payments", s.incoming_count, "Credit lines classified as customer payments"),
        ("Total incoming bank amount", s.incoming_total, "Sum of customer-payment credits (incl. duplicates)"),
        ("Allocated incoming amount", s.allocated_incoming, "= applied + overpaid"),
        ("Unmatched incoming amount", s.unallocated_incoming, "Incoming − allocated; not a receivable shortfall"),
        ("Other credits (internal / reversals)", s.other_credits_total, "Not customer payments"),
        ("Bank fees", s.fees_total, "Outgoing"),
        ("Refunds paid out", s.refunds_total, "Outgoing"),
        ("Number of exceptions", len(exceptions), "See Exceptions in the app"),
        ("Integrity checks", "Passed" if rs.integrity_ok else "FAILED",
         "; ".join(i.name for i in rs.invariants)),
    ], columns=["Metric", "Value", "Notes"])

    matched_rows = []
    for a in sorted(rs.allocations, key=lambda a: (a.order_id, a.txn_id)):
        o, t = o_by[a.order_id], t_by[a.txn_id]
        c = rs.candidates[a.candidate_id]
        row = {
            "Order ID": o.order_id, "Customer": o.customer, "Order date": o.order_date,
            f"Order amount ({cur})": o.amount, "Transaction ID": t.txn_id, "Transaction date": t.txn_date,
            f"Credit ({cur})": t.credit, f"Allocated ({cur})": a.amount, "Rule": c.rule.value,
            "Decided by": "Rule" if a.source.startswith("rule:") else "Reviewer",
            "Order status": rs.order_views[o.order_id].status.value,
        }
        if include_raw:
            row["Bank description"] = t.description
        row["Evidence"] = " | ".join(e.detail for e in c.evidence)
        matched_rows.append(row)
    matched = pd.DataFrame(matched_rows)

    unmatched_orders = pd.DataFrame([{
        "Order ID": v.order.order_id, "Customer": v.order.customer, "Order date": v.order.order_date,
        f"Expected ({cur})": v.order.amount, f"Allocated ({cur})": v.allocated,
        f"Outstanding ({cur})": v.remaining, "Status": v.status.value,
        "Open suggestions": len(v.pending), "Note": v.note,
    } for v in rs.order_views.values() if v.counts and v.remaining > 0])

    um_txn = []
    for v in rs.txn_views.values():
        if v.txn.kind.value == "credit" and v.txn.currency == cur and v.unallocated > 0:
            row = {"Transaction ID": v.txn.txn_id, "Transaction date": v.txn.txn_date,
                   f"Credit ({cur})": v.txn.credit, f"Unallocated ({cur})": v.unallocated,
                   "Status": v.status.value, "Note": v.note}
            if include_raw:
                row["Bank description"] = v.txn.description
            um_txn.append(row)
    unmatched_transactions = pd.DataFrame(um_txn)

    mm = []
    for e in exceptions:
        if e.kind in ("Amount mismatch", "Overpayment", "Partial payment"):
            mm.append({"Order ID": e.order_id, "Transaction ID(s)": e.txn_id, "Status": e.status,
                       f"Expected ({cur})": e.expected, f"Received ({cur})": e.received,
                       f"Difference ({cur})": e.difference, "Explanation": e.explanation})
    amount_mismatches = pd.DataFrame(mm)

    duplicates = pd.DataFrame([{
        "Type": "Bank transaction" if g.kind == "bank" else "Order", "Kept original": g.record_ids[0],
        "Duplicate candidate(s)": ", ".join(g.record_ids[1:]), "Reason": g.reason,
    } for g in rs.match.duplicates])

    review_log = pd.DataFrame([{
        "Timestamp (UTC)": d.timestamp, "Action": d.action, "Candidate": d.candidate_id,
        "Orders": ", ".join(rs.candidates[d.candidate_id].order_ids) if d.candidate_id in rs.candidates
        else ", ".join(d.order_ids),
        "Transactions": ", ".join(rs.candidates[d.candidate_id].txn_ids) if d.candidate_id in rs.candidates
        else ", ".join(d.txn_ids),
        "Actor": d.actor, "Note": d.note,
    } for d in rs.decisions])

    st = rs.settings
    dates = [o.order_date for o in rs.orders] + [t.txn_date for t in rs.txns]
    configuration = pd.DataFrame([
        ("Currency", st.currency), ("Amount tolerance", st.amount_tolerance),
        ("Date tolerance (days after order)", st.date_tolerance_days),
        ("Early payment allowance (days)", st.early_payment_days),
        ("Minimum name similarity", st.min_name_similarity),
        ("Auto-confirm name similarity", st.auto_name_similarity),
        ("Minimum candidate score", st.min_candidate_score),
        ("Auto-confirm exact reference", st.auto_confirm_exact_reference),
        ("Auto-confirm amount/date", st.auto_confirm_amount_date),
        ("Auto-allocate referenced partial payments", st.auto_confirm_reference_partial),
        ("Composite max group size", st.composite_max_group),
        ("Composite search pool", st.composite_max_pool),
        ("Duplicate detection", st.detect_duplicates),
        ("Score weights (amount/date/name/reference)",
         f"{st.weights.amount:g}/{st.weights.date:g}/{st.weights.name:g}/{st.weights.reference:g}"),
        ("Data date range", f"{min(dates).isoformat()} to {max(dates).isoformat()}" if dates else ""),
        ("Input fingerprint", rs.match.input_hash),
        ("Accepted matches", sum(1 for s_ in rs.cand_state.values() if s_ in ACCEPTED_STATES)),
    ], columns=["Setting", "Value"])

    integrity = pd.DataFrame([(i.name, "Pass" if i.passed else "FAIL", i.detail) for i in rs.invariants],
                             columns=["Check", "Result", "Detail"])
    return ReportTables(summary, matched, unmatched_orders, unmatched_transactions, amount_mismatches,
                        duplicates, review_log, configuration, integrity, cur)


def sanitize_frame(df: pd.DataFrame) -> pd.DataFrame:
    # Element-wise on every column: pandas 3 stores text as "str" dtype, not object.
    out = df.map(safe_text)
    out.columns = [safe_text(str(c)) for c in out.columns]
    return out


def is_money_column(name: str, currency: str) -> bool:
    return name.endswith(f"({currency})")


def status_is_problem(value: str) -> bool:
    return value in {Status.AMOUNT_MISMATCH.value, Status.OVERPAYMENT.value, Status.DUPLICATE.value,
                     Status.UNMATCHED_ORDER.value, Status.UNMATCHED_TXN.value}


def decimal_to_str(v):
    return str(v) if isinstance(v, Decimal) else v
