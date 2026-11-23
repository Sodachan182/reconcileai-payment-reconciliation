"""Multi-stage matching engine (pure function of inputs + settings).

Stages, in order of decreasing evidence strength:

1. Reference matching (order ID / payment code found in the description)
2. Exact amount + date window, ranked by name evidence
3. Composite (split payments / one payment for several orders)
4. Fuzzy name suggestions where amounts differ
5. Conflict annotation — every candidate lists the candidates it competes with

Records claimed by an earlier stage are not offered to later stages. Only
conflict-free candidates can be auto-confirmed; everything else goes to review.
"""

from __future__ import annotations

import bisect
import hashlib
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

from reconcileai.config import Settings
from reconcileai.matching.composite_match import many_orders_one_txn, many_txns_one_order
from reconcileai.matching.duplicate_detection import detect_bank_duplicates, detect_order_duplicates
from reconcileai.matching.exact_match import build_reference_index, find_references, reference_stage
from reconcileai.matching.fuzzy_match import amount_date_stage, fuzzy_stage
from reconcileai.models import (
    BankTransaction,
    Candidate,
    DuplicateGroup,
    Order,
    OrderState,
    Rule,
    TxnKind,
)


@dataclass
class MatchResult:
    candidates: list[Candidate]
    duplicates: list[DuplicateGroup]
    duplicate_txn_ids: set[str]
    duplicate_order_ids: set[str]
    txn_notes: dict[str, str]
    references: dict[str, list[str]]
    input_hash: str
    timings: dict[str, float] = field(default_factory=dict)

    def by_id(self) -> dict[str, Candidate]:
        return {c.candidate_id: c for c in self.candidates}


def input_fingerprint(orders: list[Order], txns: list[BankTransaction], settings: Settings) -> str:
    h = hashlib.sha256()
    for o in sorted(orders, key=lambda o: o.order_id):
        h.update(f"{o.order_id}|{o.customer}|{o.amount}|{o.currency}|{o.order_date}|{o.reference}|{o.state}\n".encode())
    for t in sorted(txns, key=lambda t: t.txn_id):
        h.update(f"{t.txn_id}|{t.txn_date}|{t.description}|{t.credit}|{t.debit}|{t.currency}\n".encode())
    h.update(settings.model_dump_json(exclude={"export_include_raw"}).encode())
    return h.hexdigest()[:16]


def run_matching(orders: list[Order], txns: list[BankTransaction], settings: Settings) -> MatchResult:
    timings: dict[str, float] = {}
    t0 = time.perf_counter()
    cur = settings.currency
    orders_by_id = {o.order_id: o for o in orders}
    open_orders = [o for o in orders if o.state == OrderState.OPEN and o.currency == cur]

    dup_groups: list[DuplicateGroup] = []
    dup_txn: set[str] = set()
    dup_order: set[str] = set()
    if settings.detect_duplicates:
        bank_groups = detect_bank_duplicates(txns)
        order_groups = detect_order_duplicates(open_orders)
        dup_groups = bank_groups + order_groups
        dup_txn = {i for g in bank_groups for i in g.record_ids[1:]}
        dup_order = {i for g in order_groups for i in g.record_ids[1:]}
    timings["duplicates"] = time.perf_counter() - t0

    incoming = sorted(
        (t for t in txns if t.kind == TxnKind.CREDIT and t.currency == cur and t.txn_id not in dup_txn),
        key=lambda t: (t.txn_date, t.txn_id),
    )

    # Stage 1 — references
    t1 = time.perf_counter()
    index = build_reference_index(orders)
    refs = {t.txn_id: r for t in incoming if (r := find_references(t, index))}
    ref_cands, notes = reference_stage(orders_by_id, incoming, refs, settings)
    timings["reference"] = time.perf_counter() - t1

    claimed_o = {o for c in ref_cands for o in c.order_ids}
    claimed_t = {t for c in ref_cands for t in c.txn_ids} | set(notes)
    rem_orders = [o for o in open_orders if o.order_id not in claimed_o]
    rem_txns = [t for t in incoming if t.txn_id not in claimed_t]

    # Stage 2 — exact amount + date
    t2 = time.perf_counter()
    ad_cands = amount_date_stage(rem_orders, rem_txns, settings)
    timings["amount_date"] = time.perf_counter() - t2
    claimed_o |= {o for c in ad_cands for o in c.order_ids}
    claimed_t |= {t for c in ad_cands for t in c.txn_ids}

    # Stage 3 — composite
    t3 = time.perf_counter()
    comp: list[Candidate] = []
    rem_orders = sorted((o for o in open_orders if o.order_id not in claimed_o), key=lambda o: o.order_date)
    rem_txns = [t for t in incoming if t.txn_id not in claimed_t]  # already sorted by date
    o_dates = [o.order_date for o in rem_orders]
    t_dates = [t.txn_date for t in rem_txns]
    late, early = timedelta(days=settings.date_tolerance_days), timedelta(days=settings.early_payment_days)
    for t in rem_txns:  # blocking: only orders whose date window contains the payment date
        lo, hi = bisect.bisect_left(o_dates, t.txn_date - late), bisect.bisect_right(o_dates, t.txn_date + early)
        comp.extend(many_orders_one_txn(t, rem_orders[lo:hi], settings))
    used_t = {t for c in comp for t in c.txn_ids}
    for o in rem_orders:
        lo, hi = bisect.bisect_left(t_dates, o.order_date - early), bisect.bisect_right(t_dates, o.order_date + late)
        comp.extend(many_txns_one_order(o, [t for t in rem_txns[lo:hi] if t.txn_id not in used_t], settings))
    timings["composite"] = time.perf_counter() - t3
    claimed_o |= {o for c in comp for o in c.order_ids}
    claimed_t |= {t for c in comp for t in c.txn_ids}

    # Stage 4 — fuzzy suggestions
    t4 = time.perf_counter()
    rem_orders = [o for o in open_orders if o.order_id not in claimed_o]
    rem_txns = [t for t in incoming if t.txn_id not in claimed_t]
    fz = fuzzy_stage(rem_orders, rem_txns, settings)
    timings["fuzzy"] = time.perf_counter() - t4

    candidates = _dedupe(ref_cands + ad_cands + comp + fz)
    _annotate_conflicts(candidates)
    timings["total"] = time.perf_counter() - t0
    return MatchResult(
        candidates=candidates, duplicates=dup_groups, duplicate_txn_ids=dup_txn,
        duplicate_order_ids=dup_order, txn_notes=notes, references=refs,
        input_hash=input_fingerprint(orders, txns, settings), timings=timings,
    )


def _dedupe(cands: list[Candidate]) -> list[Candidate]:
    seen: dict[str, Candidate] = {}
    for c in cands:
        cid = c.candidate_id
        if cid not in seen or c.score > seen[cid].score:
            seen[cid] = c
    return sorted(seen.values(), key=lambda c: (min(c.order_ids), min(c.txn_ids), c.candidate_id))


def _annotate_conflicts(cands: list[Candidate]) -> None:
    """Record competing candidates and enforce: auto-confirmation only without conflicts."""
    by_rec: dict[str, list[Candidate]] = defaultdict(list)
    for c in cands:
        for o in c.order_ids:
            by_rec["o:" + o].append(c)
        for t in c.txn_ids:
            by_rec["t:" + t].append(c)
    for c in cands:
        others = {x.candidate_id for k in [*("o:" + o for o in c.order_ids), *("t:" + t for t in c.txn_ids)]
                  for x in by_rec[k] if x.candidate_id != c.candidate_id}
        c.conflict_with = sorted(others)
        if others and c.auto:
            c.auto = False  # never auto-confirm something another candidate also claims
            c.note = c.note or "conflict"
        if others and c.rule in (Rule.AMOUNT_DATE, Rule.FUZZY, Rule.COMPOSITE) and not c.note:
            c.note = "ambiguous"
