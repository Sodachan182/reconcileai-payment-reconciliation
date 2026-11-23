"""Exception (discrepancy) list derived from a reconciliation state."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from reconcileai.models import Status
from reconcileai.money import dsum

SEVERITY_RANK = {"High": 0, "Medium": 1, "Low": 2}

ORDER_EXCEPTION = {
    Status.UNMATCHED_ORDER: ("Missing payment", "High"),
    Status.AMOUNT_MISMATCH: ("Amount mismatch", "High"),
    Status.OVERPAYMENT: ("Overpayment", "High"),
    Status.PARTIAL: ("Partial payment", "Medium"),
    Status.AMBIGUOUS: ("Ambiguous match", "Medium"),
    Status.SUGGESTED: ("Suggested match", "Medium"),
    Status.DUPLICATE: ("Duplicate", "Medium"),
    Status.INVESTIGATE: ("Needs investigation", "High"),
}
TXN_EXCEPTION = {
    Status.UNMATCHED_TXN: ("Unmatched transaction", "Medium"),
    Status.DUPLICATE: ("Duplicate", "High"),
    Status.PARTIAL: ("Unallocated remainder", "Medium"),
    Status.INVESTIGATE: ("Needs investigation", "High"),
}


@dataclass
class ExceptionItem:
    kind: str
    severity: str
    status: str
    order_id: str
    txn_id: str
    expected: Decimal | None
    received: Decimal | None
    difference: Decimal | None
    explanation: str
    candidate_ids: tuple[str, ...]
    customer: str = ""
    date: str = ""


def build_exceptions(rs) -> list[ExceptionItem]:
    items: list[ExceptionItem] = []
    for v in rs.order_views.values():
        if v.status not in ORDER_EXCEPTION:
            continue
        kind, sev = ORDER_EXCEPTION[v.status]
        cids = tuple(v.pending or v.accepted)
        txn_ids = sorted({t for c in cids for t in rs.candidates[c].txn_ids})
        if v.allocated > 0:
            received = v.allocated
        elif v.pending:
            received = dsum(rs.txn_views[t].txn.credit for t in txn_ids if t in rs.txn_views)
            if len(v.pending) > 1:
                received = None  # competing candidates; no single received amount
        else:
            received = Decimal(0)
        diff = (received - v.order.amount) if received is not None else None
        expl = v.note or _order_expl(rs, v, cids)
        items.append(ExceptionItem(kind, sev, v.status.value, v.order.order_id, ", ".join(txn_ids),
                                   v.order.amount, received, diff, expl, cids, v.order.customer,
                                   v.order.order_date.isoformat()))
    for v in rs.txn_views.values():
        if v.status not in TXN_EXCEPTION:
            continue
        kind, sev = TXN_EXCEPTION[v.status]
        if v.status == Status.UNMATCHED_TXN and v.note:
            kind, sev = "Payment for closed order", "High"
        cids = tuple(v.pending or v.accepted)
        order_ids = sorted({o for c in cids for o in rs.candidates[c].order_ids})
        received = v.txn.credit if v.status != Status.PARTIAL else v.unallocated
        items.append(ExceptionItem(kind, sev, v.status.value, ", ".join(order_ids), v.txn.txn_id,
                                   None, received, None,
                                   v.note or f"No order found for: \"{v.txn.description.strip()}\"",
                                   cids, "", v.txn.txn_date.isoformat()))
    items.sort(key=lambda e: (SEVERITY_RANK[e.severity], -(abs(e.difference) if e.difference is not None
                                                           else (e.received or 0)), e.order_id, e.txn_id))
    return items


def _order_expl(rs, v, cids) -> str:
    st = v.status
    cur = rs.settings.currency
    from reconcileai.money import format_amount

    if st == Status.UNMATCHED_ORDER:
        return "No bank payment found by reference, amount/date, or name"
    if st == Status.PARTIAL:
        return (f"Allocated {format_amount(v.allocated, cur)} of {format_amount(v.order.amount, cur)} {cur}; "
                f"{format_amount(v.remaining, cur)} outstanding")
    if st == Status.OVERPAYMENT:
        return f"Received {format_amount(v.overpaid, cur)} {cur} more than the order amount"
    if st == Status.AMBIGUOUS:
        return f"{len(cids)} competing candidates; choose one in the reconciliation workspace"
    if cids:
        c = rs.candidates[cids[0]]
        amt = next((e.detail for e in c.evidence if e.factor == "Amount"), "")
        return amt or c.evidence[0].detail
    return ""
