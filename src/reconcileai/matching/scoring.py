"""Explainable evidence scoring for non-reference candidates.

The score is a 0–100 *ranking heuristic*: the sum of points from independent
evidence factors with documented weights (see :class:`~reconcileai.config.ScoreWeights`).
It is not a probability of correctness.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from rapidfuzz import fuzz

from reconcileai.config import Settings
from reconcileai.models import BankTransaction, Evidence, Order
from reconcileai.money import format_amount

TOKEN_MATCH_MIN = 85  # per-token RapidFuzz ratio below this counts as "absent"


def name_similarity_text(customer_norm: str, name_text: str) -> float:
    """Token-aligned name similarity, 0–100.

    Vietnamese names share family and middle names heavily (``NGUYEN VAN AN`` vs
    ``NGUYEN VAN BINH``), so generic set ratios over-score them. Here each customer
    name token is aligned to its best unused description token by RapidFuzz
    ``ratio``; tokens scoring below 85 count as absent; the given name (last token)
    carries double weight. A name written without spaces scores 100.
    """
    if not customer_norm or not name_text:
        return 0.0
    name = customer_norm.split()
    desc = name_text.split()
    if len(name) > 1:
        joined = "".join(name)
        # Name written without spaces, matched against whole tokens only (NGUYENVANAN ≠ NGUYENVANANH).
        for i in range(len(desc)):
            acc = ""
            for j in range(i, min(i + len(name), len(desc))):
                acc += desc[j]
                if acc == joined:
                    return 100.0
    used: set[int] = set()
    total = weight_sum = 0.0
    for i, tok in enumerate(name):
        w = 2.0 if i == len(name) - 1 and len(name) > 1 else 1.0
        best, best_j = 0.0, -1
        for j, d in enumerate(desc):
            if j in used:
                continue
            r = 100.0 if d == tok else fuzz.ratio(tok, d)
            if r > best:
                best, best_j = r, j
        if best >= TOKEN_MATCH_MIN:
            used.add(best_j)
            total += w * best
        weight_sum += w
    return round(total / weight_sum, 1)


def name_similarity(customer_norm: str, txn: BankTransaction) -> float:
    return name_similarity_text(customer_norm, txn.name_text)


def _order_number(order: Order) -> str:
    """Trailing numeric part of the order id without leading zeros (e.g. ORD2026-00125 -> 125)."""
    m = re.search(r"(\d+)\D*$", order.order_id)
    return m.group(1).lstrip("0") if m else ""


def reference_fragment(order: Order, txn: BankTransaction) -> bool:
    """True when the order's serial number (≥ 3 digits) appears as a standalone token."""
    num = _order_number(order)
    if len(num) < 3:
        return False
    for tok in txn.description_norm.split():
        if tok.isdigit() and tok.lstrip("0") == num:
            return True
        if tok.endswith(num) and tok[: -len(num)].rstrip("0").isalpha() and len(tok) - len(num) <= 3:
            return True  # e.g. "DH125", "DH00125"
    return False


def day_offset(order_date: date, txn_date: date) -> int:
    return (txn_date - order_date).days


def in_window(order: Order, txn: BankTransaction, settings: Settings, widen: int = 0) -> bool:
    d = day_offset(order.order_date, txn.txn_date)
    return -settings.early_payment_days <= d <= settings.date_tolerance_days + widen


def amount_equal(a: Decimal, b: Decimal, settings: Settings) -> bool:
    return abs(a - b) <= settings.tolerance


def score_pair(order: Order, txn: BankTransaction, settings: Settings) -> tuple[float, float, list[Evidence]]:
    """Return ``(score, name_sim, evidence)`` for a 1:1 order/transaction pair."""
    w = settings.weights
    cur = order.currency
    ev: list[Evidence] = []
    score = 0.0

    if amount_equal(order.amount, txn.credit, settings):
        score += w.amount
        ev.append(Evidence("Amount", f"Equal: {format_amount(txn.credit, cur)} {cur}", w.amount))
    else:
        diff = txn.credit - order.amount
        ev.append(Evidence("Amount", f"Differs by {format_amount(diff, cur, signed=True)} {cur} "
                           f"(expected {format_amount(order.amount, cur)}, received {format_amount(txn.credit, cur)})", 0.0))

    d = day_offset(order.order_date, txn.txn_date)
    span = settings.date_tolerance_days + 1
    date_pts = w.date * max(0.0, 1 - max(d, 0) / span) if d >= -settings.early_payment_days else 0.0
    score += date_pts
    when = "same day" if d == 0 else (f"{d} day(s) after order" if d > 0 else f"{-d} day(s) before order")
    ev.append(Evidence("Date", f"Payment {when}", round(date_pts, 1)))

    sim = name_similarity(order.customer_norm, txn)
    name_pts = w.name * sim / 100
    score += name_pts
    ev.append(Evidence("Customer name", f"Similarity {sim:.0f}/100 between '{order.customer}' and description "
                       "(token-aligned name match; heuristic, not a probability)", round(name_pts, 1)))

    if reference_fragment(order, txn):
        score += w.reference
        ev.append(Evidence("Reference fragment", f"Order number {_order_number(order)} appears in description", w.reference))

    return round(score, 1), sim, ev
