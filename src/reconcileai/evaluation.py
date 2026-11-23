"""Evaluation of matching output against generator-written ground truth.

Matching unit: one (order_id, txn_id) association. A split payment (1 order, 2
credits) contributes two units; one credit covering two orders contributes two
units. Automatic metrics only count rule-confirmed allocations — suggestions are
measured separately as *review coverage*, because a suggestion is not a match
until a person confirms it.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from reconcileai.models import Status
from reconcileai.reconciliation.service import OPEN_STATES, ReconciliationState


def _prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f1 = 2 * p * r / (p + r) if p and r else None
    return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": f1}


def evaluate(rs: ReconciliationState, truth: dict) -> dict:
    gt = {(a["order_id"], a["txn_id"]): a for a in truth["associations"]}
    gt_pairs = set(gt)
    rule_of: dict[tuple[str, str], str] = {}
    auto_amount: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for al in rs.allocations:
        if al.source.startswith("rule:"):
            key = (al.order_id, al.txn_id)
            rule_of[key] = al.source[5:]
            auto_amount[key] += al.amount
    auto_pairs = set(rule_of)

    suggested: set[tuple[str, str]] = set()
    for cid, c in rs.candidates.items():
        if rs.cand_state[cid] in OPEN_STATES:
            suggested |= {(o, t) for o, t, _ in c.allocations}

    overall = _prf(len(auto_pairs & gt_pairs), len(auto_pairs - gt_pairs), len(gt_pairs - auto_pairs))

    by_rule = {}
    for rule in sorted(set(rule_of.values())):
        pairs = {k for k, r in rule_of.items() if r == rule}
        tp = len(pairs & gt_pairs)
        by_rule[rule] = {"auto_pairs": len(pairs), "correct": tp, "incorrect": len(pairs) - tp,
                         "precision": tp / len(pairs) if pairs else None}

    by_scenario = {}
    scen_pairs: dict[str, set] = defaultdict(set)
    for k, a in gt.items():
        scen_pairs[a["scenario"]].add(k)
    for scen, pairs in sorted(scen_pairs.items()):
        auto_hit = len(pairs & auto_pairs)
        review = len(pairs & (auto_pairs | suggested))
        by_scenario[scen] = {"pairs": len(pairs), "auto_matched": auto_hit,
                             "auto_recall": auto_hit / len(pairs),
                             "found_auto_or_suggested": review, "coverage": review / len(pairs)}

    tp_pairs = auto_pairs & gt_pairs
    amount_ok = sum(1 for k in tp_pairs if auto_amount[k] == Decimal(gt[k]["amount"]))

    pred_dup = rs.match.duplicate_txn_ids
    gt_dup = set(truth.get("duplicate_txn_ids", []))
    dup = _prf(len(pred_dup & gt_dup), len(pred_dup - gt_dup), len(gt_dup - pred_dup))

    closed = truth.get("closed_order_payments", [])
    closed_txn = {c["txn_id"] for c in closed}
    closed_allocated = sorted({al.txn_id for al in rs.allocations if al.txn_id in closed_txn})
    closed_flagged = sum(1 for t in closed_txn if t in rs.txn_views and rs.txn_views[t].status == Status.UNMATCHED_TXN
                         and rs.txn_views[t].note)

    gt_orders_paid = {o for o, _ in gt_pairs}
    unmatched_truth = {o for o, s in truth["order_scenarios"].items()
                       if s == "missing_payment"} | set(truth.get("duplicate_order_ids", []))
    pred_unmatched = {oid for oid, v in rs.order_views.items()
                      if v.status in (Status.UNMATCHED_ORDER, Status.DUPLICATE)}
    unmatched = _prf(len(pred_unmatched & unmatched_truth), len(pred_unmatched - unmatched_truth),
                     len(unmatched_truth - pred_unmatched))

    statuses = rs.summary.status_counts
    return {
        "matching_unit": "(order_id, txn_id) association",
        "ground_truth_pairs": len(gt_pairs),
        "automatic": overall,
        "automatic_by_rule": by_rule,
        "by_scenario": by_scenario,
        "review_coverage": {
            "suggested_pairs": len(suggested),
            "suggested_correct": len(suggested & gt_pairs),
            "gt_pairs_found_auto_or_suggested": len(gt_pairs & (auto_pairs | suggested)),
            "coverage": len(gt_pairs & (auto_pairs | suggested)) / len(gt_pairs) if gt_pairs else None,
        },
        "amount_allocation_accuracy": {"correct_pairs": len(tp_pairs), "amount_exact": amount_ok,
                                       "rate": amount_ok / len(tp_pairs) if tp_pairs else None},
        "ambiguous_orders": statuses.get(Status.AMBIGUOUS.value, 0),
        "orders_needing_review": sum(statuses.get(s.value, 0) for s in
                                     (Status.AMBIGUOUS, Status.SUGGESTED, Status.AMOUNT_MISMATCH)),
        "duplicate_detection": dup,
        "closed_order_payments": {"total": len(closed_txn), "flagged": closed_flagged,
                                  "wrongly_allocated": closed_allocated},
        "unmatched_order_detection": unmatched,
        "orders_with_payment_in_truth": len(gt_orders_paid),
        "integrity_ok": rs.integrity_ok,
        "timings_seconds": {k: round(v, 4) for k, v in rs.match.timings.items()},
    }
