"""Reconciliation service: applies rule and reviewer decisions to engine candidates,
derives statuses and totals, and verifies financial invariants.

The engine (:func:`run_matching`) is expensive and deterministic; this layer is
cheap and is re-run whenever a decision changes. Source records are never mutated.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from reconcileai.config import Settings
from reconcileai.matching.engine import MatchResult, run_matching
from reconcileai.models import (
    Allocation,
    BankTransaction,
    Candidate,
    Evidence,
    Order,
    OrderState,
    ReviewDecision,
    Rule,
    Status,
    TxnKind,
)
from reconcileai.money import dsum, format_amount

# Candidate states
AUTO, CONFIRMED, REJECTED, PENDING, INVESTIGATE, BLOCKED, SUPERSEDED = (
    "auto-confirmed", "confirmed", "rejected", "pending", "investigate", "blocked", "superseded",
)
ACCEPTED_STATES = (AUTO, CONFIRMED)
OPEN_STATES = (PENDING, INVESTIGATE)


@dataclass
class OrderView:
    order: Order
    status: Status
    allocated: Decimal = Decimal(0)
    pending: list[str] = field(default_factory=list)
    accepted: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def applied(self) -> Decimal:
        return min(self.allocated, self.order.amount) if self.counts else Decimal(0)

    @property
    def remaining(self) -> Decimal:
        return max(self.order.amount - self.allocated, Decimal(0)) if self.counts else Decimal(0)

    @property
    def overpaid(self) -> Decimal:
        return max(self.allocated - self.order.amount, Decimal(0)) if self.counts else Decimal(0)

    @property
    def counts(self) -> bool:
        return self.status != Status.EXCLUDED_ORDER


@dataclass
class TxnView:
    txn: BankTransaction
    status: Status
    allocated: Decimal = Decimal(0)
    pending: list[str] = field(default_factory=list)
    accepted: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def unallocated(self) -> Decimal:
        return self.txn.credit - self.allocated


@dataclass
class Summary:
    currency: str
    total_orders: int
    open_orders: int
    excluded_orders: int
    expected_total: Decimal
    applied_total: Decimal
    unresolved_total: Decimal
    overpaid_total: Decimal
    incoming_count: int
    incoming_total: Decimal
    allocated_incoming: Decimal
    unallocated_incoming: Decimal
    other_credits_total: Decimal
    outgoing_total: Decimal
    fees_total: Decimal
    refunds_total: Decimal
    status_counts: dict[str, int]
    txn_status_counts: dict[str, int]
    unresolved_by_reason: dict[str, Decimal]
    unallocated_by_reason: dict[str, Decimal]
    other_currency_records: int


@dataclass
class InvariantResult:
    name: str
    passed: bool
    detail: str


@dataclass
class ReconciliationState:
    settings: Settings
    orders: list[Order]
    txns: list[BankTransaction]
    match: MatchResult
    candidates: dict[str, Candidate]
    cand_state: dict[str, str]
    cand_reason: dict[str, str]
    allocations: list[Allocation]
    order_views: dict[str, OrderView]
    txn_views: dict[str, TxnView]
    summary: Summary
    invariants: list[InvariantResult]
    decisions: list[ReviewDecision]

    @property
    def integrity_ok(self) -> bool:
        return all(i.passed for i in self.invariants)


# ---------------------------------------------------------------------------------

def effective_decisions(decisions: list[ReviewDecision]) -> dict[str, ReviewDecision]:
    """Latest effective decision per candidate. ``undo`` clears the candidate's decision."""
    eff: dict[str, ReviewDecision] = {}
    for d in sorted(decisions, key=lambda d: (d.timestamp, d.decision_id or 0)):
        if d.action == "undo":
            eff.pop(d.candidate_id, None)
        else:
            eff[d.candidate_id] = d
    return eff


def manual_candidate(order_ids: tuple[str, ...], txn_ids: tuple[str, ...], note: str = "") -> Candidate:
    return Candidate(tuple(sorted(order_ids)), tuple(sorted(txn_ids)), Rule.MANUAL, 0.0,
                     [Evidence("Manual", "Created by reviewer" + (f": {note}" if note else ""), 0)])


def _manual_allocations(c: Candidate, orders: dict[str, Order], txns: dict[str, BankTransaction],
                        order_alloc, txn_alloc) -> list[tuple[str, str, Decimal]]:
    """Allocate each payment's remaining credit to the order(s) in ID order."""
    out = []
    o_rem = {o: orders[o].amount - order_alloc[o] for o in c.order_ids}
    for tid in c.txn_ids:
        t_rem = txns[tid].credit - txn_alloc[tid]
        for idx, oid in enumerate(c.order_ids):
            last = idx == len(c.order_ids) - 1
            part = t_rem if last else min(t_rem, max(o_rem[oid], Decimal(0)))
            if part > 0:
                out.append((oid, tid, part))
                o_rem[oid] -= part
                t_rem -= part
    return out


def reconcile(
    orders: list[Order],
    txns: list[BankTransaction],
    settings: Settings,
    decisions: list[ReviewDecision] | None = None,
    match: MatchResult | None = None,
) -> ReconciliationState:
    decisions = decisions or []
    match = match or run_matching(orders, txns, settings)
    cur = settings.currency
    tol = settings.tolerance
    o_by = {o.order_id: o for o in orders}
    t_by = {t.txn_id: t for t in txns}

    cands: dict[str, Candidate] = {c.candidate_id: c for c in match.candidates}
    eff = effective_decisions(decisions)
    for cid, d in eff.items():
        if cid not in cands and d.order_ids and d.txn_ids:
            mc = manual_candidate(d.order_ids, d.txn_ids, d.note)
            cands[mc.candidate_id] = mc

    state: dict[str, str] = {}
    reason: dict[str, str] = {}
    for cid, c in cands.items():
        d = eff.get(cid)
        if d is None:
            state[cid] = AUTO if c.auto else PENDING
        elif d.action == "confirm":
            state[cid] = CONFIRMED
        elif d.action == "reject":
            state[cid] = REJECTED
        elif d.action == "investigate":
            state[cid] = INVESTIGATE
        else:
            state[cid] = AUTO if c.auto else PENDING

    order_alloc: dict[str, Decimal] = defaultdict(Decimal)
    txn_alloc: dict[str, Decimal] = defaultdict(Decimal)
    allocations: list[Allocation] = []

    def try_accept(c: Candidate, source: str) -> str | None:
        for oid in c.order_ids:
            o = o_by.get(oid)
            if o is None:
                return f"order {oid} no longer exists"
            if o.state != OrderState.OPEN:
                return f"order {oid} is {o.state.value}"
            if o.currency != cur:
                return f"order {oid} is in {o.currency}, not {cur}"
            if order_alloc[oid] > 0 and order_alloc[oid] >= o.amount - tol:
                return f"order {oid} is already fully allocated"
        for tid in c.txn_ids:
            t = t_by.get(tid)
            if t is None:
                return f"transaction {tid} no longer exists"
            if t.kind != TxnKind.CREDIT:
                return f"transaction {tid} is a {t.kind.value} line, not a customer payment"
            if t.currency != cur:
                return f"transaction {tid} is in {t.currency}, not {cur}"
        allocs = (_manual_allocations(c, o_by, t_by, order_alloc, txn_alloc)
                  if c.rule == Rule.MANUAL else c.allocations)
        if not allocs:
            return "nothing left to allocate"
        need: dict[str, Decimal] = defaultdict(Decimal)
        for _, tid, amt in allocs:
            if amt <= 0:
                return "non-positive allocation"
            need[tid] += amt
        for tid, amt in need.items():
            if txn_alloc[tid] + amt > t_by[tid].credit:
                return (f"transaction {tid} has only {format_amount(t_by[tid].credit - txn_alloc[tid], cur)} {cur} "
                        "unallocated (prevents double allocation)")
        for oid, tid, amt in allocs:
            allocations.append(Allocation(oid, tid, amt, c.candidate_id, source))
            order_alloc[oid] += amt
            txn_alloc[tid] += amt
        return None

    # Rule-confirmed first (deterministic order), then reviewer confirmations in decision order.
    for cid in sorted(cid for cid, s in state.items() if s == AUTO):
        err = try_accept(cands[cid], f"rule:{cands[cid].rule.value}")
        if err:
            state[cid], reason[cid] = BLOCKED, err
    confirmed = sorted((d for d in eff.values() if d.action == "confirm" and d.candidate_id in cands),
                       key=lambda d: (d.timestamp, d.decision_id or 0))
    for d in confirmed:
        err = try_accept(cands[d.candidate_id], "review")
        if err:
            state[d.candidate_id], reason[d.candidate_id] = BLOCKED, err

    # Open suggestions that can no longer be applied are superseded.
    for cid, s in state.items():
        if s not in OPEN_STATES:
            continue
        c = cands[cid]
        full_o = [o for o in c.order_ids
                  if o in o_by and order_alloc[o] > 0 and order_alloc[o] >= o_by[o].amount - tol]
        full_t = [t for t in c.txn_ids if t in t_by and txn_alloc[t] >= t_by[t].credit]
        if full_o or full_t:
            state[cid] = SUPERSEDED
            reason[cid] = "Records already allocated by another match: " + ", ".join(full_o + full_t)

    order_views, txn_views = _views(orders, txns, match, cands, state, order_alloc, txn_alloc, settings)
    summary = _summary(orders, txns, order_views, txn_views, settings)
    rs = ReconciliationState(
        settings=settings, orders=orders, txns=txns, match=match, candidates=cands, cand_state=state,
        cand_reason=reason, allocations=allocations, order_views=order_views, txn_views=txn_views,
        summary=summary, invariants=[], decisions=decisions,
    )
    from reconcileai.reconciliation.invariants import check_invariants

    rs.invariants = check_invariants(rs)
    return rs


def _views(orders, txns, match: MatchResult, cands, state, order_alloc, txn_alloc, settings: Settings):
    cur = settings.currency
    tol = settings.tolerance
    o_pending: dict[str, list[str]] = defaultdict(list)
    t_pending: dict[str, list[str]] = defaultdict(list)
    o_acc: dict[str, list[str]] = defaultdict(list)
    t_acc: dict[str, list[str]] = defaultdict(list)
    investigate_o, investigate_t = set(), set()
    for cid, c in cands.items():
        s = state[cid]
        for o in c.order_ids:
            if s in OPEN_STATES:
                o_pending[o].append(cid)
            elif s in ACCEPTED_STATES:
                o_acc[o].append(cid)
            if s == INVESTIGATE:
                investigate_o.add(o)
        for t in c.txn_ids:
            if s in OPEN_STATES:
                t_pending[t].append(cid)
            elif s in ACCEPTED_STATES:
                t_acc[t].append(cid)
            if s == INVESTIGATE:
                investigate_t.add(t)

    def pending_status(ids: list[str]) -> Status:
        rules = {cands[i].rule for i in ids}
        if Rule.REFERENCE_MISMATCH in rules:
            return Status.AMOUNT_MISMATCH
        if len(ids) > 1 or any(cands[i].note == "ambiguous" for i in ids):
            return Status.AMBIGUOUS
        return Status.SUGGESTED

    ov: dict[str, OrderView] = {}
    for o in orders:
        alloc = order_alloc[o.order_id]
        note = ""
        if o.state != OrderState.OPEN or o.currency != cur:
            st = Status.EXCLUDED_ORDER
            note = (f"Order is {o.state.value}" if o.state != OrderState.OPEN
                    else f"Order currency {o.currency} differs from {cur}")
        elif alloc > 0:
            if abs(alloc - o.amount) <= tol:
                st = Status.MATCHED
            elif alloc < o.amount:
                st = Status.PARTIAL
            else:
                st = Status.OVERPAYMENT
        elif o.order_id in investigate_o:
            st = Status.INVESTIGATE
        elif o_pending[o.order_id]:
            st = pending_status(o_pending[o.order_id])
        elif o.order_id in match.duplicate_order_ids:
            st = Status.DUPLICATE
            note = next((g.reason for g in match.duplicates if o.order_id in g.record_ids), "")
        else:
            st = Status.UNMATCHED_ORDER
        ov[o.order_id] = OrderView(o, st, alloc, o_pending[o.order_id], o_acc[o.order_id], note)

    tv: dict[str, TxnView] = {}
    for t in txns:
        alloc = txn_alloc[t.txn_id]
        note = match.txn_notes.get(t.txn_id, "")
        if t.kind != TxnKind.CREDIT:
            st, note = Status.IGNORED, {
                TxnKind.FEE: "Bank fee", TxnKind.REFUND: "Outgoing refund", TxnKind.INTERNAL: "Internal transfer",
                TxnKind.REVERSAL: "Reversal", TxnKind.DEBIT: "Outgoing payment",
            }[t.kind]
        elif t.currency != cur:
            st, note = Status.IGNORED, f"Currency {t.currency} differs from {cur}; no exchange rate is applied"
        elif alloc > 0:
            st = Status.MATCHED if alloc >= t.credit else Status.PARTIAL
            if st == Status.PARTIAL:
                note = f"{format_amount(t.credit - alloc, cur)} {cur} of this payment is not allocated"
        elif t.txn_id in investigate_t:
            st = Status.INVESTIGATE
        elif t.txn_id in match.duplicate_txn_ids:
            st = Status.DUPLICATE
            note = next((g.reason for g in match.duplicates if t.txn_id in g.record_ids), note)
        elif t_pending[t.txn_id]:
            st = pending_status(t_pending[t.txn_id])
        else:
            st = Status.UNMATCHED_TXN
        tv[t.txn_id] = TxnView(t, st, alloc, t_pending[t.txn_id], t_acc[t.txn_id], note)
    return ov, tv


def _summary(orders, txns, ov: dict[str, OrderView], tv: dict[str, TxnView], settings: Settings) -> Summary:
    cur = settings.currency
    counted = [v for v in ov.values() if v.counts]
    expected = dsum(v.order.amount for v in counted)
    applied = dsum(v.applied for v in counted)
    unresolved = dsum(v.remaining for v in counted)
    overpaid = dsum(v.overpaid for v in counted)
    credits = [v for v in tv.values() if v.txn.kind == TxnKind.CREDIT and v.txn.currency == cur]
    incoming = dsum(v.txn.credit for v in credits)
    allocated = dsum(v.allocated for v in credits)
    by_reason: dict[str, Decimal] = defaultdict(Decimal)
    for v in counted:
        if v.remaining > 0:
            by_reason[v.status.value] += v.remaining
    unalloc_reason: dict[str, Decimal] = defaultdict(Decimal)
    for v in credits:
        if v.unallocated > 0:
            unalloc_reason[v.status.value] += v.unallocated
    same_cur = [t for t in txns if t.currency == cur]
    status_counts: dict[str, int] = defaultdict(int)
    for v in ov.values():
        status_counts[v.status.value] += 1
    tstatus: dict[str, int] = defaultdict(int)
    for v in tv.values():
        tstatus[v.status.value] += 1
    return Summary(
        currency=cur, total_orders=len(orders), open_orders=len(counted),
        excluded_orders=len(orders) - len(counted), expected_total=expected, applied_total=applied,
        unresolved_total=unresolved, overpaid_total=overpaid, incoming_count=len(credits),
        incoming_total=incoming, allocated_incoming=allocated, unallocated_incoming=incoming - allocated,
        other_credits_total=dsum(t.credit for t in same_cur if t.credit > 0 and t.kind != TxnKind.CREDIT),
        outgoing_total=dsum(t.debit for t in same_cur),
        fees_total=dsum(t.debit for t in same_cur if t.kind == TxnKind.FEE),
        refunds_total=dsum(t.debit for t in same_cur if t.kind == TxnKind.REFUND),
        status_counts=dict(status_counts), txn_status_counts=dict(tstatus),
        unresolved_by_reason=dict(by_reason), unallocated_by_reason=dict(unalloc_reason),
        other_currency_records=sum(1 for o in orders if o.currency != cur) + sum(1 for t in txns if t.currency != cur),
    )
