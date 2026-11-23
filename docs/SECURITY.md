# Security and privacy

ReconcileAI is a **local, single-user** tool. It does not move money, connect to banks, or call external APIs.

## Threat model

| Asset | Risk | Control |
|---|---|---|
| Uploaded statements | Exposure over the network | Streamlit binds to `127.0.0.1` (`.streamlit/config.toml`), XSRF protection on, usage statistics off. Do not deploy publicly. |
| Uploaded statements | Exposure through git | `data/`, `reports/`, `*.db`, `.env`, `.claude/settings.local.json` are git-ignored. Only generated fictional data under `demo/` is committed. |
| Workstation | Malicious spreadsheet | 25 MB upload limit and 200k-row limit. XLSX size is checked before parsing (zip-bomb guard), and the workbook structure is validated. Only cached values are read, so formulas are never evaluated. Legacy `.xls` is rejected. |
| Recipients of exports | CSV/formula injection | Any text cell starting with `=`, `+`, `-`, `@`, tab or CR gets an apostrophe prefix in XLSX, CSV and the exceptions download (`reports/tables.py::safe_text`). Tested. |
| Financial correctness | Silent rounding, double counting | Decimal-only amounts. Ambiguous amounts and dates are rejected. Capacity checks plus 9 invariants run, and exports are blocked if any check fails. |
| Audit trail | Tampering or loss | Decisions are append-only (SQLite triggers abort UPDATE, and DELETE while records exist). Every import, decision, run, settings change and clear is in `audit_log`. Multi-step writes are transactional. |
| Database file | Other local users | The file is created with mode 0600 where the OS supports it. On Windows it inherits the user-profile ACL; keep the project inside your user folder. |

## What is not provided

- **No authentication or multi-user isolation.** The audit actor is recorded as "local user" because the app cannot verify identity, so it does not invent one.
- No encryption at rest. Use OS disk encryption (BitLocker) for real statements.
- No concurrency guarantees beyond SQLite's own locking.

## Deleting data

*Import data → Import history → Clear workspace* (requires a confirmation tick) deletes all records, runs and decisions from the local database, and writes a `workspace_cleared` audit event. Deleting `data/reconcileai.db` removes everything, including the audit log.

## Secrets

The project needs none. CI uses no secrets. Before publishing, staged files were scanned for credentials and real financial data (see the commit checklist in `TASKS.md`).
