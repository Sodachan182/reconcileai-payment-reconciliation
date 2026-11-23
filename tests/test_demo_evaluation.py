import json

from reconcileai.config import Settings
from reconcileai.demo_data import generate
from reconcileai.evaluation import evaluate
from reconcileai.pipeline import load_pair
from reconcileai.reconciliation.service import reconcile


def test_generator_is_deterministic():
    a, b = generate(150, 7), generate(150, 7)
    assert a.orders.equals(b.orders) and a.bank.equals(b.bank) and a.truth == b.truth
    assert not generate(150, 8).bank.equals(a.bank)


def test_ground_truth_is_internally_consistent(demo_files):
    _, ds = demo_files
    t = ds.truth
    txn_ids = set(ds.bank["Mã giao dịch"])
    order_ids = set(ds.orders["Mã đơn hàng"])
    assert t["order_count"] == 300 and 300 <= t["bank_line_count"] <= 500
    for a in t["associations"]:
        assert a["order_id"] in order_ids and a["txn_id"] in txn_ids
    assert set(t["duplicate_txn_ids"]) <= txn_ids
    # every anomaly type requested by the spec is present
    for scen in ("exact_reference", "no_reference", "same_amount_pair", "ambiguous_name_pair", "missing_payment",
                 "partial_marked", "split_reference", "split_no_reference", "multi_reference", "multi_no_reference",
                 "underpaid", "overpaid", "refunded", "cancelled", "late_reference", "duplicate_order"):
        assert t["scenario_counts"][scen] > 0, scen
    descs = " ".join(ds.bank["Nội dung"])
    assert "PHI" in descs and "NOI BO" in descs and "HOAN TIEN" in descs


def test_names_are_fictional_combinations_and_no_account_numbers(demo_files):
    _, ds = demo_files
    assert not ds.bank["Nội dung"].str.contains(r"\b\d{12,}\b", regex=True).any()


def test_evaluation_metrics_on_challenging_dataset(demo_files):
    root, ds = demo_files
    s = Settings()
    rs = reconcile(*load_pair(root / "data" / "orders.xlsx", root / "data" / "bank_statement.xlsx", s)[:2], s)
    m = evaluate(rs, ds.truth)
    a = m["automatic"]
    # Regression guards. Precision matters most: a wrong automatic match is worse than a missed one.
    assert a["precision"] >= 0.99, a
    assert a["recall"] >= 0.80, a
    assert m["review_coverage"]["coverage"] >= 0.95
    assert m["duplicate_detection"]["recall"] == 1.0
    assert m["closed_order_payments"]["wrongly_allocated"] == []
    assert m["amount_allocation_accuracy"]["rate"] == 1.0
    assert m["by_scenario"]["ambiguous_name_pair"]["auto_matched"] == 0  # must go to review
    assert m["integrity_ok"]
    json.dumps(m)  # serialisable


def test_evaluation_on_clean_dataset(clean_files):
    root, ds = clean_files
    s = Settings()
    rs = reconcile(*load_pair(root / "data" / "clean" / "orders.csv", root / "data" / "clean" / "bank_statement.csv", s)[:2], s)
    m = evaluate(rs, ds.truth)
    assert m["automatic"]["precision"] == 1.0 and m["automatic"]["recall"] >= 0.95
