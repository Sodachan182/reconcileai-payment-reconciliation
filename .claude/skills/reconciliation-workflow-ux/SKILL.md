---
name: reconciliation-workflow-ux
description: UX rules for ReconcileAI's import, matching, review and reporting workflows — column mapping, evidence display, manual decisions, auditability and error prevention. Use when building or changing any workflow screen or the review/decision logic behind it.
---

# Reconciliation workflow UX

## Import (two sources, four steps)
1. Upload — orders and bank statement are visibly separate sections.
2. Map columns — show suggested mapping (from `ingestion.column_mapper`) with the
   source header and three sample values beside each field. Required fields are marked.
3. Validate — show counts of valid rows, rejected rows and warnings, plus a table of
   rejected rows with the reason. Ambiguous dates (e.g. 03/04/2026) block import until
   the user chooses a date format; never guess day/month order.
4. Confirm — import only after the user presses "Import N rows". Show what was imported.

"Load demo dataset" must be one click and clearly labelled as fictional data.

## Matching workspace
- Orders on the left, bank transactions on the right, the selected match's evidence below.
- Evidence lists each rule that fired, in plain language:
  "Reference ORD2026-00125 found in description", "Amount equal: 1,250,000 VND",
  "Date difference: 1 day". A fuzzy score is always shown with its meaning:
  "Name similarity 92/100 (heuristic, not a probability)".
- Show the raw bank description next to the normalized text.
- Conflicts: when a transaction is a candidate for more than one order, list every
  candidate — never hide alternatives.

## Decisions
- Actions: Confirm, Reject, Needs investigation, Undo. Each writes an audit entry with
  timestamp, actor ("local user" — never invent an identity), and optional note.
- A suggestion is never counted as matched money until confirmed.
- Decisions persist across re-runs (keyed by stable order/transaction IDs) and are
  re-applied; if a re-run makes a decision impossible (e.g. amount changed), surface it.

## Exceptions
- One table, filterable by type, sortable by severity and amount.
- Severity: High = money at risk (mismatch, overpayment, duplicate credit);
  Medium = needs review (ambiguous, suggested, partial); Low = informational.

## Reports
- Run the integrity check before export. If it fails, block export and show which invariant failed.
- Export the same numbers shown on screen; never recompute differently for export.

## Error prevention
- Destructive actions (clear workspace) require a confirmation checkbox.
- Errors say what happened and how to fix it, in the interface's voice, without apology.
