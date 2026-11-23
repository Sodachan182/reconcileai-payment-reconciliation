"""Matching-engine behaviour on hand-built edge cases. None may produce a silent wrong match."""

from decimal import Decimal

from helpers import order, txn
from reconcileai.config import Settings
from reconcileai.models import OrderState, Rule, Status
from reconcileai.reconciliation.service import AUTO, PENDING, reconcile


def auto_pairs(rs):
    return {(a.order_id, a.txn_id) for a in rs.allocations}


def test_exact_reference_match(settings):
    rs = reconcile([order("ORD2026-00125", "Nguyễn Văn An", 1250000, "2026-11-20")],
                   [txn("T1", "2026-11-21", "NGUYEN VAN AN CK ORD2026-00125", 1250000)], settings)
    assert auto_pairs(rs) == {("ORD2026-00125", "T1")}
    assert rs.order_views["ORD2026-00125"].status == Status.MATCHED
    c = next(iter(rs.candidates.values()))
    assert c.rule == Rule.EXACT_REFERENCE and rs.cand_state[c.candidate_id] == AUTO
    assert any("found in description" in e.detail for e in c.evidence)


def test_reference_found_without_separator_and_lowercase(settings):
    rs = reconcile([order("ORD2026-00007", "Le Thu", 300000)],
                   [txn("T1", "2026-11-01", "ck don ord2026 00007 le thu", 300000)], settings)
    assert auto_pairs(rs) == {("ORD2026-00007", "T1")}


def test_reference_with_wrong_amount_is_mismatch_not_match(settings):
    rs = reconcile([order("ORD2026-00013", "Tran Thi Ha", 411000)],
                   [txn("T1", "2026-11-02", "TRAN THI HA CK ORD202600013", 361000)], settings)
    assert not rs.allocations
    assert rs.order_views["ORD2026-00013"].status == Status.AMOUNT_MISMATCH
    assert rs.summary.unresolved_total == Decimal("411000")


def test_two_orders_identical_amounts_resolved_only_by_name(settings):
    orders = [order("O1", "Pham Minh Quan", 500000, "2026-11-01"), order("O2", "Vo Thu Trang", 500000, "2026-11-01")]
    txns = [txn("T1", "2026-11-02", "VO THU TRANG chuyen tien", 500000),
            txn("T2", "2026-11-02", "PHAM MINH QUAN chuyen tien", 500000)]
    rs = reconcile(orders, txns, settings)
    assert auto_pairs(rs) == {("O1", "T2"), ("O2", "T1")}


def test_identical_amount_without_name_evidence_is_never_auto(settings):
    orders = [order("O1", "Pham Minh Quan", 500000), order("O2", "Vo Thu Trang", 500000)]
    txns = [txn("T1", "2026-11-02", "THANH TOAN HOA DON", 500000)]
    rs = reconcile(orders, txns, settings)
    assert not rs.allocations
    assert {rs.order_views[o].status for o in ("O1", "O2")} == {Status.AMBIGUOUS}


def test_similar_customer_names_go_to_review(settings):
    # Two different customers with near-identical names and the same amount
    orders = [order("O1", "Nguyễn Văn An", 800000), order("O2", "Nguyễn Văn Anh", 800000)]
    txns = [txn("T1", "2026-11-02", "NGUYEN VAN AN chuyen tien", 800000)]
    rs = reconcile(orders, txns, settings)
    # "AN" vs "ANH" are different given names; the engine must not assign T1 to O2.
    assert ("O2", "T1") not in auto_pairs(rs)


def test_same_name_same_amount_is_ambiguous(settings):
    orders = [order("O1", "Tran Thi Lan", 700000, "2026-11-01"), order("O2", "Tran Thi Lan", 700000, "2026-11-02")]
    txns = [txn("T1", "2026-11-02", "TRAN THI LAN chuyen tien", 700000),
            txn("T2", "2026-11-03", "TRAN THI LAN chuyen tien", 700000)]
    s = Settings(detect_duplicates=False)
    rs = reconcile(orders, txns, s)
    assert not rs.allocations
    assert rs.order_views["O1"].status == Status.AMBIGUOUS


def test_partial_payment_with_instalment_wording(settings):
    rs = reconcile([order("ORD2026-00016", "Bui Duc Nam", 1000000)],
                   [txn("T1", "2026-11-02", "BUI DUC NAM CK ORD2026-00016 dot 1", 400000)], settings)
    v = rs.order_views["ORD2026-00016"]
    assert v.status == Status.PARTIAL and v.allocated == Decimal("400000") and v.remaining == Decimal("600000")
    assert rs.summary.unresolved_total == Decimal("600000")


def test_multiple_referenced_payments_sum_to_order(settings):
    rs = reconcile([order("ORD2026-00020", "Ho Gia Khoa", 900000)],
                   [txn("T1", "2026-11-02", "ORD2026-00020 lan 1", 400000),
                    txn("T2", "2026-11-04", "ORD2026-00020 lan 2", 500000)], settings)
    assert auto_pairs(rs) == {("ORD2026-00020", "T1"), ("ORD2026-00020", "T2")}
    assert rs.order_views["ORD2026-00020"].status == Status.MATCHED


def test_one_transfer_for_several_referenced_orders(settings):
    rs = reconcile([order("ORD2026-00010", "Dang Hai", 300000), order("ORD2026-00011", "Dang Hai", 200000)],
                   [txn("T1", "2026-11-02", "DANG HAI CK ORD2026-00010 ORD2026-00011", 500000)], settings)
    assert auto_pairs(rs) == {("ORD2026-00010", "T1"), ("ORD2026-00011", "T1")}
    assert rs.txn_views["T1"].unallocated == 0


def test_composite_without_reference_is_suggested_not_auto(settings):
    orders = [order("O1", "Duong Quoc Viet", 300000), order("O2", "Duong Quoc Viet", 450000)]
    txns = [txn("T1", "2026-11-02", "DUONG QUOC VIET chuyen tien", 750000)]
    rs = reconcile(orders, txns, settings)
    assert not rs.allocations
    comp = [c for c in rs.candidates.values() if c.rule == Rule.COMPOSITE]
    assert len(comp) == 1 and set(comp[0].order_ids) == {"O1", "O2"}
    assert rs.cand_state[comp[0].candidate_id] == PENDING


def test_coincidental_sum_without_name_evidence_not_proposed(settings):
    orders = [order("O1", "Ly Minh Tam", 300000), order("O2", "Ngo Thu Mai", 450000)]
    txns = [txn("T1", "2026-11-02", "IBFT TT HOA DON", 750000)]
    rs = reconcile(orders, txns, settings)
    assert not [c for c in rs.candidates.values() if c.rule == Rule.COMPOSITE]


def test_split_payments_without_reference_suggested(settings):
    orders = [order("O1", "Hoang Thanh Linh", 1000000)]
    txns = [txn("T1", "2026-11-02", "HOANG THANH LINH chuyen tien", 400000),
            txn("T2", "2026-11-03", "HOANG THANH LINH chuyen tien", 600000)]
    rs = reconcile(orders, txns, settings)
    comp = [c for c in rs.candidates.values() if c.rule == Rule.COMPOSITE]
    assert comp and set(comp[0].txn_ids) == {"T1", "T2"} and not rs.allocations


def test_duplicate_bank_line_flagged_and_not_double_counted(settings):
    orders = [order("ORD2026-00001", "Vu Ngoc Ha", 611000)]
    txns = [txn("T1", "2026-11-02", "VU NGOC HA CK ORD2026-00001", 611000),
            txn("T2", "2026-11-03", "VU NGOC HA CK ORD2026-00001", 611000)]
    rs = reconcile(orders, txns, settings)
    assert auto_pairs(rs) == {("ORD2026-00001", "T1")}
    assert rs.txn_views["T2"].status == Status.DUPLICATE
    assert rs.summary.overpaid_total == 0
    assert rs.summary.unallocated_incoming == Decimal("611000")  # visible, not hidden


def test_bank_fee_is_ignored_not_matched(settings):
    rs = reconcile([order("O1", "An", 11000)], [txn("F1", "2026-11-01", "PHI SMS BANKING", debit=11000)], settings)
    assert rs.txn_views["F1"].status == Status.IGNORED
    assert not rs.allocations and rs.summary.fees_total == Decimal("11000")


def test_fee_deducted_payment_is_mismatch(settings):
    # Customer's bank deducted a fee: received less than invoiced -> discrepancy, never silently matched
    rs = reconcile([order("ORD2026-00031", "Phan Gia Huy", 1000000)],
                   [txn("T1", "2026-11-02", "PHAN GIA HUY CK ORD2026-00031", 989000)], settings)
    assert rs.order_views["ORD2026-00031"].status == Status.AMOUNT_MISMATCH and not rs.allocations


def test_cancelled_order_with_bank_entry_is_flagged(settings):
    o = order("ORD2026-00050", "Do Thi Nga", 400000, state=OrderState.CANCELLED)
    rs = reconcile([o], [txn("T1", "2026-11-02", "DO THI NGA CK ORD2026-00050", 400000)], settings)
    assert not rs.allocations
    assert rs.order_views["ORD2026-00050"].status == Status.EXCLUDED_ORDER
    assert rs.txn_views["T1"].status == Status.UNMATCHED_TXN and "cancelled" in rs.txn_views["T1"].note
    assert rs.summary.expected_total == 0


def test_refund_after_credit(settings):
    o = order("ORD2026-00060", "Ho Minh Son", 650000, state=OrderState.REFUNDED)
    txns = [txn("T1", "2026-11-02", "HO MINH SON CK ORD2026-00060", 650000),
            txn("T2", "2026-11-06", "HOAN TIEN KH HO MINH SON ORD2026-00060", debit=650000)]
    rs = reconcile([o], txns, settings)
    assert not rs.allocations
    assert rs.txn_views["T2"].status == Status.IGNORED and rs.summary.refunds_total == Decimal("650000")
    assert "refunded" in rs.txn_views["T1"].note


def test_currency_mismatch_never_matched(settings):
    rs = reconcile([order("ORD2026-00070", "Ngo Van Long", 100, currency="VND")],
                   [txn("T1", "2026-11-02", "NGO VAN LONG ORD2026-00070", 100, currency="USD")], settings)
    assert not rs.allocations
    assert rs.txn_views["T1"].status == Status.IGNORED and "no exchange rate" in rs.txn_views["T1"].note


def test_fuzzy_suggestion_when_amount_differs_is_never_auto(settings):
    rs = reconcile([order("O1", "Bui Xuan Phuc", 820000)],
                   [txn("T1", "2026-11-03", "BUI XUAN PHUC chuyen tien", 800000)], settings)
    fz = [c for c in rs.candidates.values() if c.rule == Rule.FUZZY]
    assert fz and not rs.allocations and rs.cand_state[fz[0].candidate_id] == PENDING
    assert any("not a probability" in e.detail for e in fz[0].evidence)


def test_payment_outside_date_window_is_not_amount_matched(settings):
    rs = reconcile([order("O1", "Bui Xuan Phuc", 820000, "2026-10-01")],
                   [txn("T1", "2026-11-20", "BUI XUAN PHUC chuyen tien", 820000)], settings)
    assert not [c for c in rs.candidates.values() if c.rule == Rule.AMOUNT_DATE]


def test_unrelated_incoming_is_unmatched(settings):
    rs = reconcile([order("O1", "Bui Xuan Phuc", 820000)],
                   [txn("T1", "2026-11-03", "LY THANH TAM gui tien", 120000)], settings)
    assert rs.txn_views["T1"].status == Status.UNMATCHED_TXN
    assert rs.order_views["O1"].status == Status.UNMATCHED_ORDER


def test_amount_tolerance_setting_is_respected():
    o = [order("ORD2026-00080", "An", 1000000)]
    t = [txn("T1", "2026-11-02", "ORD2026-00080", 999000)]
    assert not reconcile(o, t, Settings()).allocations
    assert reconcile(o, t, Settings(amount_tolerance="1000")).allocations


def test_engine_is_deterministic(demo_files, settings):
    from reconcileai.matching.engine import run_matching
    from reconcileai.pipeline import load_pair

    root, _ = demo_files
    o, b, _, _ = load_pair(root / "data" / "orders.xlsx", root / "data" / "bank_statement.xlsx", settings)
    m1, m2 = run_matching(o, b, settings), run_matching(list(reversed(o)), list(reversed(b)), settings)
    sig = lambda m: [(c.candidate_id, c.rule, c.auto, c.score) for c in m.candidates]  # noqa: E731
    assert sig(m1) == sig(m2)
