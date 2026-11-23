"""Settings: every option here changes engine behaviour or exports."""

from __future__ import annotations

import streamlit as st
from pydantic import ValidationError

from reconcileai.config import ScoreWeights, Settings
from reconcileai.ingestion.column_mapper import load_templates
from reconcileai.money import SUPPORTED_CURRENCIES
from reconcileai.ui import components as ui
from reconcileai.ui import state

F = Settings.model_fields


def _help(name: str) -> str:
    return F[name].description or ""


def render() -> None:
    ui.header("Settings", "Changes apply on the next matching run. Review decisions are kept.")
    s = state.settings()

    with st.form("settings"):
        st.subheader("Currency and amounts")
        c1, c2 = st.columns(2)
        currency = c1.selectbox("Reconciliation currency", sorted(SUPPORTED_CURRENCIES),
                                index=sorted(SUPPORTED_CURRENCIES).index(s.currency),
                                help="Records in other currencies are kept but excluded; no exchange rate is ever inferred.")
        tolerance = c2.text_input("Amount tolerance", s.amount_tolerance,
                                  help="Absolute difference still treated as equal. Default 0 (exact). Decimal string.")

        st.subheader("Dates")
        c1, c2 = st.columns(2)
        dtol = c1.number_input("Days a payment may arrive after the order", 0, 60, s.date_tolerance_days,
                               help=_help("date_tolerance_days"))
        early = c2.number_input("Days a payment may precede the order", 0, 30, s.early_payment_days,
                                help=_help("early_payment_days"))

        st.subheader("Matching")
        c1, c2, c3 = st.columns(3)
        min_name = c1.slider("Minimum name similarity", 50, 100, s.min_name_similarity,
                             help="Below this, a name is not considered evidence. 0–100, heuristic.")
        auto_name = c2.slider("Name similarity to auto-confirm", 50, 100, s.auto_name_similarity,
                              help="An amount/date match is auto-confirmed only if unique AND the name scores at least this.")
        min_score = c3.slider("Minimum candidate score", 0, 100, s.min_candidate_score,
                              help=_help("min_candidate_score"))
        st.caption("Score weights (must total 100). The score ranks candidates; it is not a probability.")
        w1, w2, w3, w4 = st.columns(4)
        wa = w1.number_input("Amount", 0.0, 100.0, float(s.weights.amount), step=5.0)
        wd = w2.number_input("Date", 0.0, 100.0, float(s.weights.date), step=5.0)
        wn = w3.number_input("Name", 0.0, 100.0, float(s.weights.name), step=5.0)
        wr = w4.number_input("Reference fragment", 0.0, 100.0, float(s.weights.reference), step=5.0)

        st.subheader("Automatic rules")
        a1 = st.checkbox("Auto-confirm reference + exact amount matches", s.auto_confirm_exact_reference)
        a2 = st.checkbox("Auto-confirm unique amount/date matches with strong name evidence", s.auto_confirm_amount_date)
        a3 = st.checkbox("Auto-allocate referenced instalments (remainder stays unresolved)",
                         s.auto_confirm_reference_partial)
        dup = st.checkbox("Detect duplicate bank lines and orders", s.detect_duplicates)
        c1, c2 = st.columns(2)
        grp = c1.number_input("Composite: max records per group", 2, 5, s.composite_max_group,
                              help=_help("composite_max_group"))
        pool = c2.number_input("Composite: max records searched", 3, 20, s.composite_max_pool,
                               help="Bounds the subset search so it cannot explode combinatorially.")

        st.subheader("Exports")
        raw = st.checkbox("Include raw bank descriptions in reports", s.export_include_raw)

        saved = st.form_submit_button("Save settings", type="primary")
    if saved:
        try:
            new = Settings(
                currency=currency, amount_tolerance=tolerance.strip() or "0", date_tolerance_days=int(dtol),
                early_payment_days=int(early), min_name_similarity=min_name, auto_name_similarity=auto_name,
                min_candidate_score=min_score, auto_confirm_exact_reference=a1, auto_confirm_amount_date=a2,
                auto_confirm_reference_partial=a3, composite_max_group=int(grp), composite_max_pool=int(pool),
                detect_duplicates=dup, weights=ScoreWeights(amount=wa, date=wd, name=wn, reference=wr),
                export_include_raw=raw,
            )
        except ValidationError as exc:
            for err in exc.errors():
                st.error(f"{' → '.join(str(x) for x in err['loc']) or 'Settings'}: {err['msg'].removeprefix('Value error, ')}")
        else:
            state.save_settings(new)
            st.success("Settings saved. Run matching on the Reconciliation page to apply them.")

    c1, _ = st.columns([1, 4])
    if c1.button("Reset to defaults"):
        state.save_settings(Settings())
        st.toast("Settings reset to defaults")
        st.rerun()

    st.subheader("Mapping templates")
    tpl = load_templates(state.TEMPLATES_PATH)
    if tpl:
        for name, t in tpl.items():
            mapped = ", ".join(f"{k} ← {v}" for k, v in t["mapping"].items() if v)
            st.write(f"**{name}** ({t['kind']}, dates {t.get('date_format', 'auto')}): {mapped}")
    else:
        st.caption("No saved templates. Save one from the column-mapping step on the Import data page.")

    st.subheader("Appearance")
    st.caption("Dark is the default. Switch to light from the app menu (⋮ top right → Settings → Theme). "
               "Charts and status colours follow the active theme.")
