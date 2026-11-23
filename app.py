"""ReconcileAI — Streamlit entry point.  Run:  streamlit run app.py"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import streamlit as st  # noqa: E402

from reconcileai.ui import exceptions, import_data, navigation, overview, reconciliation, reports, settings  # noqa: E402
from reconcileai.ui.theme import inject_css  # noqa: E402

st.set_page_config(page_title="ReconcileAI", page_icon=":material/account_balance:", layout="wide",
                   initial_sidebar_state="expanded")
inject_css()

pages = {
    "overview": st.Page(overview.render, title="Overview", icon=":material/dashboard:", url_path="overview",
                        default=True),
    "import": st.Page(import_data.render, title="Import data", icon=":material/upload_file:", url_path="import"),
    "reconciliation": st.Page(reconciliation.render, title="Reconciliation", icon=":material/compare_arrows:",
                              url_path="reconciliation"),
    "exceptions": st.Page(exceptions.render, title="Exceptions", icon=":material/report:", url_path="exceptions"),
    "reports": st.Page(reports.render, title="Reports", icon=":material/description:", url_path="reports"),
    "settings": st.Page(settings.render, title="Settings", icon=":material/tune:", url_path="settings"),
}
navigation.PAGES.update(pages)

st.logo(str(ROOT / "assets" / "logo.svg"), size="large")

nav = st.navigation(list(pages.values()), position="sidebar")

with st.sidebar:
    st.html("<div style='font-size:12px;color:var(--text-muted);padding:8px 4px;line-height:1.5'>"
            "Local workspace. Data stays on this computer.<br>Single user, no bank connection.</div>")

nav.run()
