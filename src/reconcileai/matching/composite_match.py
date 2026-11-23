"""Stage 4 — composite (one-to-many / many-to-one) candidates without references.

A subset is proposed only when
  * every member has name evidence ≥ ``min_name_similarity`` with the counterpart,
  * all members fall inside the date window,
  * the subset sums *exactly* (within the configured tolerance) to the counterpart amount,
  * the search pool and group size are bounded (no combinatorial explosion).

Composite candidates are never auto-confirmed: a coincidental sum is too easy to hit.
"""

from __future__ import annotations

from itertools import combinations

from reconcileai.config import Settings
from reconcileai.matching.scoring import in_window, name_similarity
from reconcileai.models import BankTransaction, Candidate, Evidence, Order, Rule
from reconcileai.money import dsum, format_amount


def _subsets(pool, target, key, settings: Settings):
    tol = settings.tolerance
    hits = []
    for size in range(2, settings.composite_max_group + 1):
        for combo in combinations(pool, size):
            if abs(dsum(key(x) for x in combo) - target) <= tol:
                hits.append(combo)
                if len(hits) > 3:
                    return hits
    return hits


def many_orders_one_txn(
    txn: BankTransaction, orders: list[Order], settings: Settings
) -> list[Candidate]:
    cur = settings.currency
    pool = []
    for o in orders:
        if o.amount >= txn.credit or not in_window(o, txn, settings):
            continue
        sim = name_similarity(o.customer_norm, txn)
        if sim >= settings.min_name_similarity:
            pool.append((sim, o))
    pool.sort(key=lambda p: (-p[0], abs((txn.txn_date - p[1].order_date).days), p[1].order_id))
    pool = [o for _, o in pool[: settings.composite_max_pool]]
    hits = _subsets(pool, txn.credit, lambda o: o.amount, settings)
    out = []
    for combo in hits:
        combo = sorted(combo, key=lambda o: o.order_id)
        ev = [Evidence("Composite", f"{len(combo)} orders sum to the payment amount "
                       f"{format_amount(txn.credit, cur)} {cur}", 0)]
        for o in combo:
            ev.append(Evidence("Order", f"{o.order_id} · {o.customer} · {format_amount(o.amount, cur)} {cur} "
                               f"· {o.order_date.isoformat()} · name similarity {name_similarity(o.customer_norm, txn):.0f}/100", 0))
        out.append(Candidate(tuple(o.order_id for o in combo), (txn.txn_id,), Rule.COMPOSITE, 70.0, ev,
                             auto=False, allocations=[(o.order_id, txn.txn_id, o.amount) for o in combo],
                             note="ambiguous" if len(hits) > 1 else ""))
    return out


def many_txns_one_order(
    order: Order, txns: list[BankTransaction], settings: Settings
) -> list[Candidate]:
    cur = settings.currency
    pool = []
    for t in txns:
        if t.credit >= order.amount or not in_window(order, t, settings):
            continue
        sim = name_similarity(order.customer_norm, t)
        if sim >= settings.min_name_similarity:
            pool.append((sim, t))
    pool.sort(key=lambda p: (-p[0], p[1].txn_date, p[1].txn_id))
    pool = [t for _, t in pool[: settings.composite_max_pool]]
    hits = _subsets(pool, order.amount, lambda t: t.credit, settings)
    out = []
    for combo in hits:
        combo = sorted(combo, key=lambda t: (t.txn_date, t.txn_id))
        ev = [Evidence("Composite", f"{len(combo)} payments sum to the order amount "
                       f"{format_amount(order.amount, cur)} {cur}", 0)]
        for t in combo:
            ev.append(Evidence("Payment", f"{t.txn_id} · {t.txn_date.isoformat()} · {format_amount(t.credit, cur)} {cur} "
                               f"· \"{t.description.strip()}\" · name similarity {name_similarity(order.customer_norm, t):.0f}/100", 0))
        out.append(Candidate((order.order_id,), tuple(t.txn_id for t in combo), Rule.COMPOSITE, 70.0, ev,
                             auto=False, allocations=[(order.order_id, t.txn_id, t.credit) for t in combo],
                             note="ambiguous" if len(hits) > 1 else ""))
    return out
