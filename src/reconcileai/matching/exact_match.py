"""Stage 1 — reference matching.

A bank description *references* an order when one of its compact token joins equals
the order's compact ID (``ORD2026-00125`` ≙ ``ORD202600125`` ≙ ``ord2026 00125``) or the
order's own payment-reference code. Reference evidence is the strongest signal, but
the amount must still agree before a match is auto-confirmed.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from reconcileai.config import Settings
from reconcileai.models import BankTransaction, Candidate, Evidence, Order, OrderState, Rule
from reconcileai.money import dsum, format_amount
from reconcileai.text import reference_keys

PARTIAL_MARKERS = (
    "DOT 1", "DOT 2", "DOT 3", "LAN 1", "LAN 2", "LAN 3", "TRA TRUOC", "DAT COC", " COC ",
    "CON LAI", "MOT PHAN", "PARTIAL", "INSTALMENT", "INSTALLMENT", "DEPOSIT",
)


def build_reference_index(orders: list[Order]) -> dict[str, str]:
    """compact key -> order_id. Payment-reference keys shared by several orders are dropped."""
    index: dict[str, str] = {}
    ref_owner: dict[str, set[str]] = defaultdict(set)
    for o in orders:
        if o.key:
            index[o.key] = o.order_id
        if o.reference_key and len(o.reference_key) >= 5 and any(c.isdigit() for c in o.reference_key):
            ref_owner[o.reference_key].add(o.order_id)
    for key, owners in ref_owner.items():
        if len(owners) == 1 and key not in index:
            index[key] = next(iter(owners))
    return index


def find_references(txn: BankTransaction, index: dict[str, str]) -> list[str]:
    found = {index[k] for k in reference_keys(txn.description) if k in index}
    return sorted(found)


def has_partial_marker(txn: BankTransaction) -> bool:
    d = f" {txn.description_norm} "
    return any(m in d for m in PARTIAL_MARKERS)


def _ref_evidence(order: Order, txn: BankTransaction) -> Evidence:
    return Evidence("Reference", f"{order.order_id} found in description of {txn.txn_id}: \"{txn.description.strip()}\"", 0)


def reference_stage(
    orders_by_id: dict[str, Order],
    txns: list[BankTransaction],
    refs: dict[str, list[str]],
    settings: Settings,
) -> tuple[list[Candidate], dict[str, str]]:
    """Build reference candidates.

    Returns ``(candidates, notes)`` where ``notes`` maps txn_id to an explanation for
    credits that reference only cancelled/refunded orders.
    """
    cur = settings.currency
    tol = settings.tolerance
    notes: dict[str, str] = {}
    single: dict[str, list[BankTransaction]] = defaultdict(list)
    multi: list[tuple[BankTransaction, list[Order]]] = []

    for t in txns:
        ids = refs.get(t.txn_id, [])
        if not ids:
            continue
        open_orders = [orders_by_id[i] for i in ids
                       if orders_by_id[i].state == OrderState.OPEN and orders_by_id[i].currency == cur]
        closed = [orders_by_id[i] for i in ids if orders_by_id[i] not in open_orders]
        if closed and not open_orders:
            o = closed[0]
            why = o.state.value if o.state != OrderState.OPEN else f"in {o.currency}"
            notes[t.txn_id] = f"References {o.order_id}, which is {why}; not applied to receivables"
            continue
        if len(open_orders) == 1:
            single[open_orders[0].order_id].append(t)
        else:
            multi.append((t, open_orders))

    candidates: list[Candidate] = []
    multi_order_ids = {o.order_id for _, os in multi for o in os}

    for oid, credits in single.items():
        o = orders_by_id[oid]
        credits.sort(key=lambda t: (t.txn_date, t.txn_id))
        total = dsum(t.credit for t in credits)
        ev = [_ref_evidence(o, t) for t in credits]
        allocs = [(oid, t.txn_id, t.credit) for t in credits]
        diff = total - o.amount
        amount_line = (f"Expected {format_amount(o.amount, cur)} {cur}; received "
                       f"{format_amount(total, cur)} {cur} in {len(credits)} payment(s)")
        if abs(diff) <= tol and oid not in multi_order_ids:
            rule = Rule.EXACT_REFERENCE if len(credits) == 1 else Rule.SPLIT_REFERENCE
            ev.append(Evidence("Amount", amount_line + " — equal", 0))
            candidates.append(Candidate((oid,), tuple(t.txn_id for t in credits), rule, 100.0, ev,
                                        auto=settings.auto_confirm_exact_reference, allocations=allocs))
        elif (diff < 0 and settings.auto_confirm_reference_partial and oid not in multi_order_ids
              and (len(credits) > 1 or any(has_partial_marker(t) for t in credits))):
            ev.append(Evidence("Amount", amount_line + f" — {format_amount(-diff, cur)} {cur} still outstanding", 0))
            ev.append(Evidence("Partial payment", "Instalment wording or several referenced credits; "
                               "allocated, remainder stays unresolved", 0))
            candidates.append(Candidate((oid,), tuple(t.txn_id for t in credits), Rule.SPLIT_REFERENCE, 100.0, ev,
                                        auto=True, allocations=allocs, note="partial"))
        else:
            ev.append(Evidence("Amount", amount_line + f" — difference {format_amount(diff, cur, signed=True)} {cur}", 0))
            candidates.append(Candidate((oid,), tuple(t.txn_id for t in credits), Rule.REFERENCE_MISMATCH, 90.0, ev,
                                        auto=False, allocations=allocs,
                                        note="overpaid" if diff > 0 else "underpaid"))

    for t, os in multi:
        os.sort(key=lambda o: o.order_id)
        expected = dsum(o.amount for o in os)
        ev = [_ref_evidence(o, t) for o in os]
        # allocate in order-id sequence; any excess stays unallocated on the transaction
        remaining = t.credit
        allocs: list[tuple[str, str, Decimal]] = []
        for o in os:
            part = min(o.amount, remaining)
            if part > 0:
                allocs.append((o.order_id, t.txn_id, part))
                remaining -= part
        has_other = any(o.order_id in single for o in os)
        line = (f"One payment of {format_amount(t.credit, cur)} {cur} references {len(os)} orders "
                f"totalling {format_amount(expected, cur)} {cur}")
        if abs(t.credit - expected) <= tol and not has_other:
            ev.append(Evidence("Amount", line + " — equal", 0))
            candidates.append(Candidate(tuple(o.order_id for o in os), (t.txn_id,), Rule.MULTI_REFERENCE, 100.0, ev,
                                        auto=settings.auto_confirm_exact_reference, allocations=allocs))
        else:
            ev.append(Evidence("Amount", line + f" — difference {format_amount(t.credit - expected, cur, signed=True)} {cur}"
                               + ("; some orders also have other referenced payments" if has_other else ""), 0))
            candidates.append(Candidate(tuple(o.order_id for o in os), (t.txn_id,), Rule.REFERENCE_MISMATCH, 90.0, ev,
                                        auto=False, allocations=allocs, note="multi-order"))
    return candidates, notes
