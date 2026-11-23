"""Decisions, audit, idempotence, double-allocation prevention and financial invariants."""

from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given
from hypothesis import settings as hsettings
from hypothesis import strategies as st

from helpers import order, txn
from reconcileai.config import Settings
from reconcileai.matching.engine import run_matching
from reconcileai.models import Allocation, ReviewDecision, Status
from reconcileai.reconciliation.invariants import IntegrityError, check_invariants, require_integrity
from reconcileai.reconciliation.service import (
    BLOCKED,
    CONFIRMED,
    INVESTIGATE,
    PENDING,
    REJECTED,
    SUPERSEDED,
    manual_candidate,
    reconcile,
)


def dec(cid, action, ts, note="", **kw):
    return ReviewDecision(cid, action, note, "local user", ts, None, **kw)


def ambiguous_case():
    orders = [order("O1", "Pham Minh Quan", 500000), order("O2", "Vo Thu Trang", 500000)]
    txns = [txn("T1", "2026-11-02", "THANH TOAN HOA DON", 500000)]
    return orders, txns


def test_manual_confirmation_allocates_and_supersedes_competitor(settings):
    orders, txns = ambiguous_case()
    rs = reconcile(orders, txns, settings)
    c1 = next(c for c in rs.candidates.values() if c.order_ids == ("O1",))
    c2 = next(c for c in rs.candidates.values() if c.order_ids == ("O2",))
    rs = reconcile(orders, txns, settings, [dec(c1.candidate_id, "confirm", "2026-11-23T10:00:00")])
    assert rs.cand_state[c1.candidate_id] == CONFIRMED
    assert rs.cand_state[c2.candidate_id] == SUPERSEDED
    assert rs.order_views["O1"].status == Status.MATCHED and rs.order_views["O2"].status == Status.UNMATCHED_ORDER
    assert rs.integrity_ok


def test_double_allocation_is_blocked(settings):
    orders, txns = ambiguous_case()
    rs = reconcile(orders, txns, settings)
    cids = sorted(rs.candidates)
    decisions = [dec(cids[0], "confirm", "2026-11-23T10:00:00"), dec(cids[1], "confirm", "2026-11-23T10:01:00")]
    rs = reconcile(orders, txns, settings, decisions)
    assert sorted(rs.cand_state[c] for c in cids) == [BLOCKED, CONFIRMED]
    assert "double allocation" in next(r for r in rs.cand_reason.values())
    assert sum(a.amount for a in rs.allocations) == Decimal("500000")
    assert rs.integrity_ok


def test_reject_and_undo(settings):
    orders = [order("ORD2026-00125", "An", 1250000)]
    txns = [txn("T1", "2026-11-02", "ORD2026-00125", 1250000)]
    rs = reconcile(orders, txns, settings)
    cid = next(iter(rs.candidates))
    rs2 = reconcile(orders, txns, settings, [dec(cid, "reject", "2026-11-23T10:00:00", "wrong customer")])
    assert rs2.cand_state[cid] == REJECTED and not rs2.allocations
    assert rs2.order_views["ORD2026-00125"].status == Status.UNMATCHED_ORDER
    rs3 = reconcile(orders, txns, settings, [dec(cid, "reject", "2026-11-23T10:00:00"),
                                             dec(cid, "undo", "2026-11-23T10:05:00")])
    assert rs3.allocations and rs3.order_views["ORD2026-00125"].status == Status.MATCHED


def test_investigate_flag(settings):
    orders, txns = ambiguous_case()
    rs = reconcile(orders, txns, settings)
    cid = sorted(rs.candidates)[0]
    rs = reconcile(orders, txns, settings, [dec(cid, "investigate", "2026-11-23T10:00:00")])
    assert rs.cand_state[cid] == INVESTIGATE
    assert Status.INVESTIGATE in {v.status for v in rs.order_views.values()}


def test_manual_match_of_unmatched_records(settings):
    orders = [order("O1", "Pham Minh Quan", 500000)]
    txns = [txn("T1", "2026-11-02", "IBFT 99812", 500000)]
    mc = manual_candidate(("O1",), ("T1",), "Confirmed with customer")
    rs = reconcile(orders, txns, settings, [dec(mc.candidate_id, "confirm", "2026-11-23T10:00:00", "Confirmed",
                                                order_ids=("O1",), txn_ids=("T1",))])
    assert rs.order_views["O1"].status == Status.MATCHED and rs.integrity_ok
    assert rs.allocations[0].source == "review"


def test_manual_match_cannot_use_fee_line(settings):
    orders = [order("O1", "An", 11000)]
    txns = [txn("F1", "2026-11-02", "PHI SMS BANKING", debit=11000)]
    mc = manual_candidate(("O1",), ("F1",))
    rs = reconcile(orders, txns, settings, [dec(mc.candidate_id, "confirm", "t", order_ids=("O1",), txn_ids=("F1",))])
    assert rs.cand_state[mc.candidate_id] == BLOCKED and not rs.allocations


def test_confirmed_mismatch_becomes_partial_with_remaining_visible(settings):
    orders = [order("ORD2026-00013", "Tran Thi Ha", 411000)]
    txns = [txn("T1", "2026-11-02", "TRAN THI HA CK ORD202600013", 361000)]
    cid = next(iter(reconcile(orders, txns, settings).candidates))
    rs = reconcile(orders, txns, settings, [dec(cid, "confirm", "t")])
    v = rs.order_views["ORD2026-00013"]
    assert v.status == Status.PARTIAL and v.remaining == Decimal("50000")
    assert rs.summary.unresolved_total == Decimal("50000")


def test_confirmed_overpayment(settings):
    orders = [order("ORD2026-00035", "Ly Hoai Nam", 1000000)]
    txns = [txn("T1", "2026-11-02", "LY HOAI NAM ORD2026-00035", 1100000)]
    cid = next(iter(reconcile(orders, txns, settings).candidates))
    rs = reconcile(orders, txns, settings, [dec(cid, "confirm", "t")])
    assert rs.order_views["ORD2026-00035"].status == Status.OVERPAYMENT
    assert rs.summary.overpaid_total == Decimal("100000") and rs.summary.applied_total == Decimal("1000000")
    assert rs.integrity_ok


def test_rerun_is_idempotent(demo_files, settings):
    from reconcileai.pipeline import load_pair

    root, _ = demo_files
    o, b, _, _ = load_pair(root / "data" / "orders.xlsx", root / "data" / "bank_statement.xlsx", settings)
    r1, r2 = reconcile(o, b, settings), reconcile(o, b, settings)
    key = lambda rs: (sorted((a.order_id, a.txn_id, a.amount) for a in rs.allocations),  # noqa: E731
                      rs.summary.unresolved_total, rs.summary.unallocated_incoming, rs.match.input_hash)
    assert key(r1) == key(r2)
    assert r1.integrity_ok


def test_csv_and_xlsx_give_identical_results(demo_files, settings):
    from reconcileai.pipeline import load_pair

    root, _ = demo_files
    a = reconcile(*load_pair(root / "data" / "orders.xlsx", root / "data" / "bank_statement.xlsx", settings)[:2], settings)
    b = reconcile(*load_pair(root / "data" / "orders.csv", root / "data" / "bank_statement.csv", settings)[:2], settings)
    assert a.match.input_hash == b.match.input_hash
    assert a.summary.unresolved_total == b.summary.unresolved_total


def test_invariant_violation_is_detected_and_blocks_reports(settings):
    orders, txns = ambiguous_case()
    rs = reconcile(orders, txns, settings)
    cid = sorted(rs.candidates)[0]
    rs.allocations.append(Allocation("O1", "T1", Decimal("500000"), cid, "rule:tampered"))
    rs.allocations.append(Allocation("O2", "T1", Decimal("500000"), cid, "rule:tampered"))
    rs.invariants = check_invariants(rs)
    failed = {i.name for i in rs.invariants if not i.passed}
    assert "No over-allocated transactions" in failed
    with pytest.raises(IntegrityError):
        require_integrity(rs)
    from reconcileai.reports.tables import build_tables

    with pytest.raises(IntegrityError):
        build_tables(rs)


def test_score_cannot_override_impossible_currency(settings):
    orders = [order("O1", "Pham Minh Quan", 500000)]
    txns = [txn("T1", "2026-11-02", "PHAM MINH QUAN chuyen tien", 500000, currency="USD")]
    rs = reconcile(orders, txns, settings)
    assert not rs.candidates and not rs.allocations


def test_filtering_exceptions_does_not_change_totals(demo_files, settings):
    from reconcileai.pipeline import load_pair
    from reconcileai.reconciliation.exceptions import build_exceptions

    root, _ = demo_files
    o, b, _, _ = load_pair(root / "data" / "orders.xlsx", root / "data" / "bank_statement.xlsx", settings)
    rs = reconcile(o, b, settings)
    before = (rs.summary.unresolved_total, rs.summary.applied_total)
    items = build_exceptions(rs)
    _ = [x for x in items if x.severity == "High"]
    assert (rs.summary.unresolved_total, rs.summary.applied_total) == before


# --- property-based: invariants hold for arbitrary data and arbitrary decisions -------------

names = ["Nguyen Van An", "Tran Thi Lan", "Le Minh Duc", "Pham Thu Ha", "Vo Gia Huy"]


@st.composite
def scenario(draw):
    n_o = draw(st.integers(1, 8))
    orders = []
    for i in range(n_o):
        orders.append(order(f"ORD2026-{i + 1:05d}", draw(st.sampled_from(names)),
                            draw(st.sampled_from([100000, 250000, 500000, 750000])),
                            f"2026-11-{draw(st.integers(1, 10)):02d}"))
    txns = []
    for j in range(draw(st.integers(0, 10))):
        ref = draw(st.sampled_from(["", *[o.order_id for o in orders]]))
        who = draw(st.sampled_from(names))
        txns.append(txn(f"T{j}", f"2026-11-{draw(st.integers(1, 14)):02d}", f"{who} CK {ref}".strip(),
                        draw(st.sampled_from([100000, 150000, 250000, 500000, 750000, 1000000]))))
    return orders, txns


@hsettings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(scenario(), st.lists(st.tuples(st.integers(0, 50), st.sampled_from(["confirm", "reject", "investigate", "undo"])),
                            max_size=8))
def test_invariants_hold_for_any_data_and_decisions(sc, actions):
    orders, txns = sc
    s = Settings()
    match = run_matching(orders, txns, s)
    cids = [c.candidate_id for c in match.candidates]
    decisions = [dec(cids[i % len(cids)], a, f"2026-11-23T10:{k:02d}:00") for k, (i, a) in enumerate(actions)] if cids else []
    rs = reconcile(orders, txns, s, decisions, match)
    assert rs.integrity_ok, [(i.name, i.detail) for i in rs.invariants if not i.passed]
    per_txn: dict[str, Decimal] = {}
    for a in rs.allocations:
        per_txn[a.txn_id] = per_txn.get(a.txn_id, Decimal(0)) + a.amount
    for t in txns:
        assert per_txn.get(t.txn_id, Decimal(0)) <= t.credit
    assert rs.summary.expected_total - rs.summary.applied_total == rs.summary.unresolved_total
    assert {rs.cand_state[c] for c in rs.cand_state} <= {"auto-confirmed", CONFIRMED, REJECTED, PENDING, INVESTIGATE,
                                                         BLOCKED, SUPERSEDED}
