"""Duplicate-candidate detection for bank lines and orders.

Exact duplicate *rows* (same ID, same content) are already dropped at import.
This module flags records with *different* IDs that look like the same money
movement, so they are excluded from automatic matching and sent to review.
"""

from __future__ import annotations

from collections import defaultdict

from reconcileai.models import BankTransaction, DuplicateGroup, Order, TxnKind
from reconcileai.money import format_amount


def detect_bank_duplicates(txns: list[BankTransaction], max_days: int = 1) -> list[DuplicateGroup]:
    """Same direction, same amount, same normalized description, dates ≤ ``max_days`` apart."""
    buckets: dict[tuple, list[BankTransaction]] = defaultdict(list)
    for t in txns:
        if t.kind in (TxnKind.FEE,):
            continue  # recurring identical fees are normal
        buckets[(t.currency, t.credit, t.debit, t.description_norm)].append(t)
    groups: list[DuplicateGroup] = []
    for (cur, _credit, _debit, desc), items in buckets.items():
        if len(items) < 2 or not desc:
            continue
        items.sort(key=lambda t: (t.txn_date, t.source_row, t.txn_id))
        cluster = [items[0]]
        for t in items[1:]:
            if (t.txn_date - cluster[-1].txn_date).days <= max_days:
                cluster.append(t)
            else:
                if len(cluster) > 1:
                    groups.append(_bank_group(cluster, cur))
                cluster = [t]
        if len(cluster) > 1:
            groups.append(_bank_group(cluster, cur))
    return groups


def _bank_group(cluster: list[BankTransaction], cur: str) -> DuplicateGroup:
    amt = cluster[0].credit or cluster[0].debit
    return DuplicateGroup(
        kind="bank",
        record_ids=tuple(t.txn_id for t in cluster),
        reason=(f"{len(cluster)} lines with identical description and amount "
                f"{format_amount(amt, cur)} {cur} within 1 day; {cluster[0].txn_id} kept as original"),
    )


def detect_order_duplicates(orders: list[Order]) -> list[DuplicateGroup]:
    """Different order IDs with the same customer, amount and date."""
    buckets: dict[tuple, list[Order]] = defaultdict(list)
    for o in orders:
        buckets[(o.customer_norm, o.amount, o.currency, o.order_date)].append(o)
    groups = []
    for (_, amt, cur, d), items in buckets.items():
        if len(items) > 1:
            items.sort(key=lambda o: (o.source_row, o.order_id))
            groups.append(DuplicateGroup(
                kind="order",
                record_ids=tuple(o.order_id for o in items),
                reason=(f"{len(items)} orders for {items[0].customer} of {format_amount(amt, cur)} {cur} "
                        f"on {d.isoformat()}"),
            ))
    return groups
