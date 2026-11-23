"""Session/workspace state shared by all pages."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from reconcileai.config import Settings
from reconcileai.matching.engine import MatchResult, input_fingerprint, run_matching
from reconcileai.models import ReviewDecision
from reconcileai.persistence.database import DEFAULT_DB, Workspace
from reconcileai.reconciliation.service import ReconciliationState, reconcile

TEMPLATES_PATH = Path("data") / "mapping_templates.json"
DEMO_DIR = Path("demo") / "data"


@st.cache_resource
def workspace() -> Workspace:
    return Workspace(DEFAULT_DB)


def settings() -> Settings:
    if "settings" not in st.session_state:
        st.session_state.settings = workspace().load_settings()
    return st.session_state.settings


def save_settings(s: Settings) -> None:
    workspace().save_settings(s)
    st.session_state.settings = s
    st.session_state.pop("match", None)


def data():
    ws = workspace()
    return ws.orders(), ws.transactions(), ws.decisions()


def run_now() -> ReconciliationState | None:
    """Explicit 'Run matching': compute candidates and persist a run snapshot."""
    orders, txns, decisions = data()
    if not orders or not txns:
        return None
    s = settings()
    match = run_matching(orders, txns, s)
    st.session_state.match = match
    rs = reconcile(orders, txns, s, decisions, match)
    workspace().save_run(rs)
    return rs


def current() -> tuple[ReconciliationState | None, str]:
    """Return ``(state, reason)``. ``state`` is None when there is nothing to show.

    Matching re-runs automatically only if a run with identical inputs and settings
    was already recorded (the engine is deterministic, so the result is identical).
    """
    orders, txns, decisions = data()
    if not orders or not txns:
        return None, "empty" if not orders and not txns else ("no-bank" if orders else "no-orders")
    s = settings()
    h = input_fingerprint(orders, txns, s)
    m: MatchResult | None = st.session_state.get("match")
    if m is None or m.input_hash != h:
        if any(r["input_hash"] == h for r in workspace().runs(200)):
            m = run_matching(orders, txns, s)
            st.session_state.match = m
        else:
            return None, "stale"
    return reconcile(orders, txns, s, decisions, m), "ok"


def decide(candidate_id: str, action: str, note: str = "", order_ids=(), txn_ids=()) -> None:
    workspace().add_decision(ReviewDecision(candidate_id, action, note.strip(), "local user",
                                            order_ids=tuple(order_ids), txn_ids=tuple(txn_ids)))


def load_demo() -> dict:
    """Import the bundled fictional dataset (regenerating it if the files are missing)."""
    from reconcileai.demo_data import generate, write_dataset
    from reconcileai.pipeline import load_bank, load_orders

    o_path, b_path = DEMO_DIR / "orders.xlsx", DEMO_DIR / "bank_statement.xlsx"
    if not (o_path.exists() and b_path.exists()):
        write_dataset(generate(300, 2026, True), DEMO_DIR, DEMO_DIR.parent / "ground_truth.json")
    s = settings()
    lo, lb = load_orders(o_path, s), load_bank(b_path, s)
    ws = workspace()
    r1 = ws.import_orders(lo.records, lo.report)
    r2 = ws.import_bank(lb.records, lb.report)
    run_now()
    return {"orders": r1, "bank": r2}


def clear_workspace() -> None:
    workspace().clear()
    for k in ("match", "sel_order", "sel_txn", "import_orders", "import_bank"):
        st.session_state.pop(k, None)
