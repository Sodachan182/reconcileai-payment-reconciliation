"""Stage 2 (exact amount + date window) and Stage 3 (fuzzy name) candidate generation.

Both stages use *blocking* — candidates are only compared within an amount bucket
(stage 2) or a date window (stage 3) — so the work grows roughly linearly with the
number of records instead of all-to-all.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from datetime import timedelta

from rapidfuzz import fuzz, process

from reconcileai.config import Settings
from reconcileai.matching.scoring import in_window, name_similarity_text, reference_fragment, score_pair
from reconcileai.models import BankTransaction, Candidate, Evidence, Order, Rule


def amount_date_stage(
    orders: list[Order], txns: list[BankTransaction], settings: Settings
) -> list[Candidate]:
    by_amount = sorted(orders, key=lambda o: (o.amount, o.order_id))
    keys = [o.amount for o in by_amount]
    tol = settings.tolerance

    pairs = []  # (txn, order, score, sim, ev, plausible)
    for t in txns:
        lo = bisect.bisect_left(keys, t.credit - tol)
        hi = bisect.bisect_right(keys, t.credit + tol)
        for o in by_amount[lo:hi]:
            if not in_window(o, t, settings):
                continue
            score, sim, ev = score_pair(o, t, settings)
            plausible = sim >= settings.min_name_similarity or reference_fragment(o, t)
            pairs.append((t, o, score, sim, ev, plausible))

    by_txn: dict[str, list] = defaultdict(list)
    by_order: dict[str, list] = defaultdict(list)
    for p in pairs:
        by_txn[p[0].txn_id].append(p)
        by_order[p[1].order_id].append(p)

    def keep(p) -> bool:
        # If a side has a name/reference-supported candidate, amount-only alternatives are dropped.
        if p[2] < settings.min_candidate_score:
            return False
        return p[5] or not (any(q[5] for q in by_txn[p[0].txn_id]) or any(q[5] for q in by_order[p[1].order_id]))

    kept = [p for p in pairs if keep(p)]
    kt: dict[str, list] = defaultdict(list)
    ko: dict[str, list] = defaultdict(list)
    for p in kept:
        kt[p[0].txn_id].append(p)
        ko[p[1].order_id].append(p)

    out: list[Candidate] = []
    for t, o, score, sim, ev, plausible in kept:
        unique = len(kt[t.txn_id]) == 1 and len(ko[o.order_id]) == 1
        auto = bool(settings.auto_confirm_amount_date and unique and plausible
                    and sim >= settings.auto_name_similarity)
        ev = list(ev)
        if unique:
            ev.append(Evidence("Uniqueness", "Only candidate for both the order and the payment", 0))
        else:
            n = max(len(kt[t.txn_id]), len(ko[o.order_id]))
            ev.append(Evidence("Uniqueness", f"{n} competing candidates share this amount and window — needs review", 0))
        if not plausible:
            ev.append(Evidence("Caution", "Amount and date only; no name or reference evidence", 0))
        out.append(Candidate((o.order_id,), (t.txn_id,), Rule.AMOUNT_DATE, score, ev, auto=auto,
                             allocations=[(o.order_id, t.txn_id, t.credit)],
                             note="" if unique else "ambiguous"))
    return out


def fuzzy_stage(
    orders: list[Order], txns: list[BankTransaction], settings: Settings, per_txn: int = 3
) -> list[Candidate]:
    """Name-similarity suggestions where the amount does not agree. Never auto-confirmed."""
    if not orders:
        return []
    by_date = sorted(orders, key=lambda o: o.order_date)
    dates = [o.order_date for o in by_date]
    widen = settings.date_tolerance_days  # late payments are common when amounts differ
    out: list[Candidate] = []
    for t in txns:
        if not t.name_text:
            continue
        lo = bisect.bisect_left(dates, t.txn_date - timedelta(days=settings.date_tolerance_days + widen))
        hi = bisect.bisect_right(dates, t.txn_date + timedelta(days=settings.early_payment_days))
        window = [o for o in by_date[lo:hi] if in_window(o, t, settings, widen=widen)]
        if not window:
            continue
        # Cheap C-level prefilter (token_set_ratio is generous, so it keeps recall),
        # then the stricter token-aligned similarity decides.
        pre = process.extract(
            t.name_text, [o.customer_norm for o in window], scorer=fuzz.token_set_ratio,
            score_cutoff=settings.min_name_similarity, limit=25,
        )
        scored = []
        for _, _, idx in pre:
            o = window[idx]
            sim = name_similarity_text(o.customer_norm, t.name_text)
            if sim >= settings.min_name_similarity:
                scored.append((sim, o))
        scored.sort(key=lambda p: (-p[0], abs((t.txn_date - p[1].order_date).days), p[1].order_id))
        for _, o in scored[:per_txn]:
            score, sim, ev = score_pair(o, t, settings)
            ev.append(Evidence("Caution", "Fuzzy suggestion: name and date are similar but the amount differs. "
                               "Confirm only with external evidence.", 0))
            out.append(Candidate((o.order_id,), (t.txn_id,), Rule.FUZZY, score, ev, auto=False,
                                 allocations=[(o.order_id, t.txn_id, t.credit)]))
    return out
