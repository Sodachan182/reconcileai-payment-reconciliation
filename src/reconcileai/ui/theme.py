"""Design tokens, CSS and Plotly styling (see .claude/skills/fintech-minimalist-design)."""

from __future__ import annotations

import streamlit as st

from reconcileai.models import Status

TOKENS = {
    "dark": {
        "bg-primary": "#0D1015", "bg-sidebar": "#14181F", "bg-panel": "#1A1F27", "bg-hover": "#252B34",
        "border": "#2B323C", "text-primary": "#F0F2F5", "text-secondary": "#A6AFBB", "text-muted": "#747F8D",
        "accent": "#7D91ED", "success": "#66B79A", "warning": "#DAB56F", "danger": "#DC8585",
        "bg-related": "#1C2232", "bg-selected": "#283150",
    },
    "light": {
        "bg-primary": "#F7F8FA", "bg-sidebar": "#EEF0F3", "bg-panel": "#FFFFFF", "bg-hover": "#E6E9EE",
        "border": "#D5DAE1", "text-primary": "#151A21", "text-secondary": "#4A5463", "text-muted": "#6B7584",
        "accent": "#4157C9", "success": "#2F7D62", "warning": "#946C1F", "danger": "#B23B3B",
        "bg-related": "#F1F3FC", "bg-selected": "#DFE4F8",
    },
}

STATUS_TONE = {
    Status.MATCHED.value: "success",
    Status.PARTIAL.value: "warning",
    Status.SUGGESTED.value: "warning",
    Status.AMBIGUOUS.value: "warning",
    Status.INVESTIGATE.value: "warning",
    Status.AMOUNT_MISMATCH.value: "danger",
    Status.OVERPAYMENT.value: "danger",
    Status.DUPLICATE.value: "danger",
    Status.UNMATCHED_ORDER.value: "danger",
    Status.UNMATCHED_TXN.value: "danger",
    Status.EXCLUDED_ORDER.value: "text-muted",
    Status.IGNORED.value: "text-muted",
}


def mode() -> str:
    try:
        t = st.context.theme.type
    except Exception:
        t = None
    return "light" if t == "light" else "dark"


def tok() -> dict[str, str]:
    return TOKENS[mode()]


def status_color(status: str) -> str:
    return tok()[STATUS_TONE.get(status, "text-secondary")]


def inject_css() -> None:
    t = tok()
    vars_ = "\n".join(f"  --{k}: {v};" for k, v in t.items())
    st.html(f"""
<style>
:root {{
{vars_}
  --radius-sm: 6px; --radius-md: 10px;
}}
html, body, [class*="st-"] {{ font-feature-settings: "tnum" 1, "cv11" 1; }}
.block-container {{ padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1480px; }}
h1 {{ letter-spacing: -0.01em; margin-bottom: 0 !important; padding-bottom: 0 !important; }}
h2, h3 {{ letter-spacing: -0.005em; }}
[data-testid="stCaptionContainer"] {{ color: var(--text-muted); }}
[data-testid="stSidebarNav"] a span {{ font-size: 14px; }}
[data-testid="stSidebarNavLink"][aria-current="page"] {{ background: var(--bg-hover); }}
[data-testid="stSidebarNavLink"][aria-current="page"] span {{ color: var(--text-primary); font-weight: 500; }}
.stButton button, .stDownloadButton button {{ font-weight: 500; box-shadow: none; }}
.stButton button:focus-visible, .stDownloadButton button:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}

.ra-sub {{ color: var(--text-secondary); font-size: 13px; margin: 2px 0 0 0; }}

.ra-eq {{ display: flex; flex-wrap: wrap; align-items: flex-end; gap: 6px 22px; padding: 18px 0 16px 0;
          border-bottom: 1px solid var(--border); margin-bottom: 4px; }}
.ra-eq .term {{ display: flex; flex-direction: column; }}
.ra-eq .val {{ font-size: 30px; font-weight: 500; line-height: 1.1; color: var(--text-primary);
               font-variant-numeric: tabular-nums; letter-spacing: -0.02em; }}
.ra-eq .lbl {{ font-size: 12px; color: var(--text-muted); margin-top: 6px; }}
.ra-eq .op {{ font-size: 26px; color: var(--text-muted); padding-bottom: 22px; font-weight: 400; }}
.ra-eq .term.result .val {{ color: var(--warning); }}
.ra-eq .term.result.zero .val {{ color: var(--success); }}
.ra-eq .cur {{ font-size: 13px; color: var(--text-muted); padding-bottom: 24px; }}
.ra-eq2 {{ font-size: 13px; color: var(--text-secondary); padding: 8px 0 18px 0; font-variant-numeric: tabular-nums; }}
.ra-eq2 b {{ color: var(--text-primary); font-weight: 500; }}

.ra-strip {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
             border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--bg-panel);
             margin: 4px 0 14px 0; overflow: hidden; }}
.ra-strip .cell {{ padding: 12px 16px; border-left: 1px solid var(--border); margin-left: -1px; }}
.ra-strip .k {{ font-size: 12px; color: var(--text-secondary); }}
.ra-strip .v {{ font-size: 20px; font-weight: 500; color: var(--text-primary); font-variant-numeric: tabular-nums;
                margin-top: 2px; white-space: nowrap; }}
.ra-strip .v small {{ font-size: 12px; color: var(--text-muted); font-weight: 400; margin-left: 4px; }}
.ra-strip.compact .v {{ font-size: 16px; }}

.ra-dot {{ display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 6px; vertical-align: 1px; }}
.ra-status {{ font-size: 13px; white-space: nowrap; }}

.ra-cmp {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
.ra-cmp th {{ text-align: left; font-weight: 500; color: var(--text-secondary); padding: 6px 10px;
              border-bottom: 1px solid var(--border); }}
.ra-cmp td {{ padding: 6px 10px; border-bottom: 1px solid var(--border); vertical-align: top; color: var(--text-primary); }}
.ra-cmp td.k {{ color: var(--text-secondary); width: 150px; }}
.ra-cmp td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
.ra-cmp .muted {{ color: var(--text-muted); }}

.ra-ev {{ list-style: none; padding: 0; margin: 6px 0 0 0; font-size: 13px; }}
.ra-ev li {{ display: grid; grid-template-columns: 140px 1fr 56px; gap: 10px; padding: 5px 0;
             border-bottom: 1px solid var(--border); }}
.ra-ev .f {{ color: var(--text-secondary); }}
.ra-ev .p {{ text-align: right; color: var(--text-muted); font-variant-numeric: tabular-nums; }}

.ra-empty {{ border: 1px dashed var(--border); border-radius: var(--radius-md); padding: 28px 24px;
             color: var(--text-secondary); font-size: 14px; }}
.ra-empty b {{ color: var(--text-primary); font-weight: 500; display: block; margin-bottom: 4px; font-size: 15px; }}

.ra-check {{ font-size: 13px; padding: 6px 0; border-bottom: 1px solid var(--border); display: grid;
             grid-template-columns: 22px 260px 1fr; gap: 8px; }}
.ra-check .ok {{ color: var(--success); }} .ra-check .bad {{ color: var(--danger); }}
.ra-check .d {{ color: var(--text-secondary); font-variant-numeric: tabular-nums; }}

.ra-steps {{ display: flex; gap: 18px; font-size: 13px; color: var(--text-muted); margin: 2px 0 10px 0; flex-wrap: wrap; }}
.ra-steps .on {{ color: var(--text-primary); font-weight: 500; }}
.ra-steps .done {{ color: var(--success); }}

@media (max-width: 640px) {{
  .ra-eq .val {{ font-size: 22px; }}
  .ra-eq .op {{ font-size: 20px; padding-bottom: 18px; }}
  .ra-ev li {{ grid-template-columns: 1fr; gap: 2px; }}
  .ra-check {{ grid-template-columns: 22px 1fr; }}
  .ra-check .d {{ grid-column: 2; }}
  .block-container {{ padding-left: 16px; padding-right: 16px; }}
}}
</style>
""")


def plotly_layout(height: int = 260, **extra) -> dict:
    t = tok()
    base = dict(
        height=height, margin=dict(l=8, r=8, t=8, b=8), paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)", font=dict(family="Geist, system-ui, sans-serif", size=12,
                                                 color=t["text-secondary"]),
        xaxis=dict(gridcolor=t["border"], zeroline=False, linecolor=t["border"], tickfont=dict(size=11)),
        yaxis=dict(gridcolor=t["border"], zeroline=False, linecolor=t["border"], tickfont=dict(size=11)),
        showlegend=False, bargap=0.25,
        hoverlabel=dict(bgcolor=t["bg-panel"], bordercolor=t["border"], font=dict(color=t["text-primary"])),
    )
    base.update(extra)
    return base
