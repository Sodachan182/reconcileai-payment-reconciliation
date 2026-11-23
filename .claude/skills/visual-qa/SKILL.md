---
name: visual-qa
description: Browser-based visual and functional QA for the ReconcileAI Streamlit app — screenshots, viewport sizes, theme checks, table overflow, number alignment and interaction tests. Use before claiming any UI change is done and when capturing README screenshots.
---

# Visual QA

## Start the app
```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py --server.headless true --server.port 8501
```
Then drive it with the in-app browser tools or with `scripts/capture_screenshots.py`
(Playwright, Chromium). Only report what actually ran.

## Viewports
1920×1080, 1440×900, 1366×768, 1024×768, 390×844. Desktop is primary; at 390 wide the
page must not scroll horizontally except inside tables.

## States to inspect
Empty workspace · demo loaded · import success · invalid file · overview with metrics ·
reconciliation with matched rows · ambiguous candidate · amount mismatch · partial payment ·
duplicate transaction · after manual confirmation · report download · dark and light theme.

## Checks per screen
- Money columns right-aligned, tabular numerals, thousands separators, no float artefacts
  (e.g. `1250000.0000001`).
- No clipped headers or truncated amounts at 1366×768.
- Exactly one accent-coloured primary action.
- Status communicated by text as well as colour.
- Keyboard focus visible on buttons and inputs.
- Browser console has no errors.
- Interactions: navigation works, filters change rows but not totals, confirm/reject updates
  the overview metrics, export downloads a valid XLSX.

## Screenshots
Save to `assets/screenshots/<page>.png` at 1440×900. Never edit or fake screenshots.
