# TASKS

Progress checklist for the ReconcileAI MVP. Ticked items were verified by running them, not only by writing them.

## Phase 1 — Environment and skills
- [x] Python 3.12.10, Git 2.54 and GitHub CLI inspected; `.venv` created
- [x] Official `frontend-design` plugin: already installed in this Claude Code environment, and its guidance was applied to the UI plan
- [x] Browser testing: Claude in-app browser for interactive QA, plus Playwright (Chromium) for scripted QA (`scripts/capture_screenshots.py`)
- [x] Project skills: `.claude/skills/fintech-minimalist-design`, `reconciliation-workflow-ux`, `visual-qa`
- [x] Commit attribution disabled in `.claude/settings.local.json` (git-ignored)

## Phase 2–4 — Core
- [x] Domain models, Decimal money, Pydantic settings, VN text normalization
- [x] CSV/XLSX import, encoding/delimiter detection, sheet selection, size and zip guards
- [x] Column mapping with VI/EN suggestions and templates; ambiguous dates block import
- [x] Validation report (invalid amounts and dates, negatives, missing fields, duplicates, currencies)
- [x] Reference matching, amount/date matching, composite, fuzzy suggestions, duplicates
- [x] Conflict handling (mutual uniqueness, superseded, blocked), manual decisions, undo
- [x] 9 financial invariants; exports blocked on failure
- [x] SQLite workspace with append-only decisions and an audit log

## Phase 5 — UI
- [x] Overview, Import data, Reconciliation, Exceptions, Reports, Settings
- [x] Dark theme (default) and light theme via Streamlit's theme switcher
- [x] Real metrics only; no inactive buttons

## Phase 6–7 — Data and evaluation
- [x] Deterministic fictional generator, challenging and clean datasets, generator-written ground truth
- [x] Evaluation harness (precision, recall, F1, coverage, per rule, per scenario); results committed in `docs/`
- [x] Benchmark at 300 / 1,000 / 5,000 orders and 10 seeds

## Phase 8–9 — Testing and QA
- [x] 109 pytest tests (including Hypothesis property tests), ruff clean
- [x] CLI smoke test, `scripts/test.ps1`
- [x] `START_RECONCILEAI.bat` tested from a fresh copy in a path containing spaces (venv created, second run skips install, app served)
- [x] Browser QA: 24 checks pass (empty state, invalid file, import idempotence, ambiguous, mismatch, confirm, exports, 5 viewports, light theme, console errors)

### Bugs found by tests and QA, and fixed
- USD `1.250` was read as 1,250. Ambiguous separators are now rejected for currencies with cents.
- Name shortcut matched `NGUYENVANAN` inside `NGUYENVANANH`. Matching now uses whole tokens only.
- CSV export skipped formula sanitizing under pandas 3 (`str` dtype). The sanitizer now applies to every cell.
- Noise-word list removed real name parts (THANH, TIEN, HANG, HOA). The list is narrowed, and recall went up.
- Mapping dropdowns kept the previous file's choices, which dropped order statuses. Widgets are now keyed per file, and a warning appears for unmapped status columns.
- Selection callback crash and stale row selection after filtering in the workspace.
- Composite stage compared all leftovers against each other. It now blocks by date window.

## Phase 10–11 — Docs and publication
- [x] README, ARCHITECTURE, MATCHING_ALGORITHM, EVALUATION, DESIGN_SYSTEM, DEMO_SCRIPT, SECURITY
- [x] Secret and private-data scan of staged files
- [ ] Public GitHub repository created and pushed
- [ ] GitHub Actions run checked

## Known gaps (not implemented)
- Multi-user authentication, exchange-rate conversion, bank APIs, OCR (out of MVP scope)
- Composite groups larger than 3 records
- Vectorized normalization (5,000 rows take about 1.5–3 s)
