"""Accounting-style consistency checks. Reports are blocked if any check fails.

Equations (all exact Decimal arithmetic, reconciliation currency only):

    Expected receivable  − Applied payments          = Unresolved expected amount
    Incoming customer credits − Allocated to orders  = Unallocated incoming amount
    Allocated to orders  = Applied payments + Overpayments
"""

from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
from typing import TYPE_CHECKING

from reconcileai.models import OrderState, TxnKind
from reconcileai.money import dsum, format_amount, quantum

if TYPE_CHECKING:
    from reconcileai.reconciliation.service import InvariantResult, ReconciliationState


class IntegrityError(RuntimeError):
    """Raised when a report is requested from a state that fails an invariant."""


def check_invariants(rs: ReconciliationState) -> list[InvariantResult]:
    from reconcileai.reconciliation.service import InvariantResult

    s = rs.summary
    cur = s.currency
    out: list[InvariantResult] = []

    def add(name: str, ok: bool, detail_ok: str, detail_fail: str) -> None:
        out.append(InvariantResult(name, ok, detail_ok if ok else detail_fail))

    # 1. Stable unique identifiers
    oc = Counter(o.order_id for o in rs.orders)
    tc = Counter(t.txn_id for t in rs.txns)
    dup = [k for k, n in oc.items() if n > 1] + [k for k, n in tc.items() if n > 1]
    add("Unique record identifiers", not dup, f"{len(oc)} orders and {len(tc)} transactions have unique IDs",
        f"Duplicate IDs: {', '.join(dup[:10])}")

    # 2. Allocations reference valid, eligible records in the same currency
    o_by = {o.order_id: o for o in rs.orders}
    t_by = {t.txn_id: t for t in rs.txns}
    bad = []
    for a in rs.allocations:
        o, t = o_by.get(a.order_id), t_by.get(a.txn_id)
        if o is None or t is None:
            bad.append(f"{a.order_id}/{a.txn_id}: missing record")
        elif o.state != OrderState.OPEN or t.kind != TxnKind.CREDIT:
            bad.append(f"{a.order_id}/{a.txn_id}: ineligible record")
        elif o.currency != t.currency or o.currency != cur:
            bad.append(f"{a.order_id}/{a.txn_id}: currency mismatch")
        elif a.amount <= 0:
            bad.append(f"{a.order_id}/{a.txn_id}: non-positive amount")
    add("Allocations reference valid records", not bad, f"{len(rs.allocations)} allocations verified",
        "; ".join(bad[:10]))

    # 3. No transaction allocated beyond its amount (double-allocation guard)
    per_txn: dict[str, Decimal] = defaultdict(Decimal)
    for a in rs.allocations:
        per_txn[a.txn_id] += a.amount
    over = [f"{tid} ({format_amount(v, cur)} > {format_amount(t_by[tid].credit, cur)})"
            for tid, v in per_txn.items() if tid in t_by and v > t_by[tid].credit]
    add("No over-allocated transactions", not over, "Every payment is allocated at most once in full",
        "Over-allocated: " + ", ".join(over[:10]))

    # 4. Exact Decimal arithmetic at the currency's precision
    q = quantum(cur)
    amounts = [o.amount for o in rs.orders] + [t.credit for t in rs.txns] + [t.debit for t in rs.txns] \
        + [a.amount for a in rs.allocations]
    non_dec = [a for a in amounts if not isinstance(a, Decimal)]
    bad_prec = [a for a in amounts if isinstance(a, Decimal) and a.as_tuple().exponent < q.as_tuple().exponent
                and a != a.quantize(q)]
    add("Exact decimal amounts", not non_dec and not bad_prec, f"All amounts are Decimal at {cur} precision",
        f"{len(non_dec)} non-Decimal and {len(bad_prec)} over-precise amounts")

    # 5. Order-side equation, recomputed independently from allocations
    alloc_by_order: dict[str, Decimal] = defaultdict(Decimal)
    for a in rs.allocations:
        alloc_by_order[a.order_id] += a.amount
    counted = [v.order for v in rs.order_views.values() if v.counts]
    expected = dsum(o.amount for o in counted)
    applied = dsum(min(alloc_by_order[o.order_id], o.amount) for o in counted)
    unresolved = dsum(max(o.amount - alloc_by_order[o.order_id], Decimal(0)) for o in counted)
    ok = expected - applied == unresolved == s.unresolved_total and applied == s.applied_total \
        and expected == s.expected_total
    add("Expected − applied = unresolved", ok,
        f"{format_amount(expected, cur)} − {format_amount(applied, cur)} = {format_amount(unresolved, cur)} {cur}",
        f"Mismatch: expected {expected}, applied {applied}, unresolved {unresolved}, summary {s.unresolved_total}")

    # 6. Bank-side equation
    allocated = dsum(a.amount for a in rs.allocations)
    ok = (s.incoming_total - s.allocated_incoming == s.unallocated_incoming and s.unallocated_incoming >= 0
          and allocated == s.allocated_incoming)
    add("Incoming − allocated = unallocated", ok,
        f"{format_amount(s.incoming_total, cur)} − {format_amount(s.allocated_incoming, cur)} = "
        f"{format_amount(s.unallocated_incoming, cur)} {cur}",
        f"Mismatch: incoming {s.incoming_total}, allocated {s.allocated_incoming} vs {allocated}, "
        f"unallocated {s.unallocated_incoming}")

    # 7. Allocated = applied + overpaid (nothing counted twice)
    ok = allocated == s.applied_total + s.overpaid_total
    add("Allocated = applied + overpaid", ok,
        f"{format_amount(allocated, cur)} = {format_amount(s.applied_total, cur)} + {format_amount(s.overpaid_total, cur)}",
        f"Allocated {allocated} ≠ applied {s.applied_total} + overpaid {s.overpaid_total}")

    # 8. Every allocation is explained by an accepted candidate
    unexplained = [a for a in rs.allocations
                   if a.candidate_id not in rs.candidates or rs.cand_state.get(a.candidate_id) not in
                   ("auto-confirmed", "confirmed")]
    add("Allocations are explainable", not unexplained, "Each allocation links to an accepted match with evidence",
        f"{len(unexplained)} allocations without an accepted match")

    # 9. Every record has exactly one status
    ok = len(rs.order_views) == len(rs.orders) and len(rs.txn_views) == len(rs.txns)
    add("Every record classified", ok, "Each order and transaction has exactly one status",
        "Some records are missing a status")
    return out


def require_integrity(rs: ReconciliationState) -> None:
    failed = [i for i in rs.invariants if not i.passed]
    if failed:
        raise IntegrityError("Integrity check failed: " + "; ".join(f"{i.name}: {i.detail}" for i in failed))
