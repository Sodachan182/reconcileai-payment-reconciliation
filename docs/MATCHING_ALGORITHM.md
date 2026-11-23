# Matching algorithm

Source: `src/reconcileai/matching/` (engine) and `src/reconcileai/reconciliation/service.py` (decisions and allocations).

The engine is a **pure function** of `(orders, transactions, settings)`. Given the same input it returns the same candidates in the same order (`test_engine_is_deterministic`). It never allocates money itself. It only proposes candidates and marks some as rule-confirmable.

## 0. Eligibility

| Record | Rule |
|---|---|
| Order | In scope only if its source status is open (not cancelled or refunded) and its currency equals the reconciliation currency |
| Bank line | Only **credit** lines classified as customer payments (`TxnKind.CREDIT`) in the reconciliation currency are matched |
| Excluded kinds | Fees, outgoing refunds, internal transfers (in or out), reversals and other debits. Classified from direction plus keywords (`PHI`, `FEE`, `HOAN TIEN`, `NOI BO`, …) and shown as *Ignored* with the reason |

## 1. Duplicate detection (`duplicate_detection.py`)

- **Exact duplicate rows** (same ID, same content) are dropped at import with a warning. The same ID with different content is rejected.
- **Bank duplicate candidates**: identical normalized description, same direction and amount, dates ≤ 1 day apart. The earliest is kept. The others are excluded from matching and shown as *Duplicate candidate*. Fees are exempt because recurring identical fees are normal.
- **Order duplicate candidates**: different IDs with the same customer, amount and date. These are only flagged, since repeat orders can be legitimate.

## 2. Stage 1 — reference matching (`exact_match.py`)

- Every order contributes its compact ID (`ORD2026-00125` → `ORD202600125`) and, when present and unique, its payment-reference code.
- Each description is tokenized after normalization. Joins of up to 3 adjacent tokens are compacted and looked up. That makes `ORD202600125`, `ord2026 00125` and `MBVCB.123.ORD2026.00125.NAME` all hit.
- Credits that reference only cancelled or refunded orders are **not applied**. They become unmatched bank lines with the note "References ORD…, which is cancelled".

For each order with referencing credits (total `T`, expected `E`):

| Condition | Result | Auto? |
|---|---|---|
| `|T − E| ≤ tolerance`, 1 credit | `exact_reference` | yes (setting) |
| `|T − E| ≤ tolerance`, several credits | `split_reference` | yes (setting) |
| `T < E` **and** (several credits **or** instalment wording such as `DOT 1`, `LAN 2`, `TRA TRUOC`, `DAT COC`) | `split_reference` (partial) | yes (setting); the remainder stays unresolved |
| otherwise (`T ≠ E`) | `reference_amount_mismatch` | **no**: *Amount mismatch*, needs review |

One credit referencing several orders: if the credit equals the sum of those orders, it becomes `multi_reference` (auto). Otherwise it is a mismatch for review. Allocation order is by order ID, and any excess stays unallocated on the payment.

A reference never overrides the amount: the amount still has to agree before anything is auto-confirmed.

## 3. Stage 2 — exact amount + date window (`fuzzy_match.py::amount_date_stage`)

Blocking: orders are sorted by amount, and each credit only looks at orders with `|amount − credit| ≤ tolerance` (bisect). A pair must also be in the date window `order_date − early_payment_days ≤ payment_date ≤ order_date + date_tolerance_days` (defaults 1 and 5).

Each pair gets an **evidence score** (0–100, weights configurable and validated to sum to 100):

| Factor | Default weight | Points |
|---|---|---|
| Amount agreement | 40 | full if equal within tolerance |
| Date proximity | 20 | `20 × (1 − days_after / (date_tolerance + 1))` |
| Customer name | 30 | `30 × similarity / 100` |
| Reference fragment | 10 | the order's serial number (≥ 3 digits) appears as a token, e.g. `DH125` |

**Name similarity** (`scoring.py::name_similarity_text`): each customer-name token is aligned to its best unused description token with RapidFuzz `ratio`. Tokens scoring below 85 count as absent, and the given name (last token) carries double weight. A name written without spaces matches whole tokens only. Example scores: `NGUYEN VAN AN` vs `NGUYEN VAN AN` = 100, vs `NGUYEN VAN BINH` = 50, vs `NGUYEN VAN ANH` = 50, vs `NGUYEN AN` = 75.

A pair is **plausible** if name similarity ≥ `min_name_similarity` (75) or a reference fragment is present. If any plausible candidate exists for a payment or an order, amount-only alternatives are dropped. Pairs below `min_candidate_score` (55) are discarded.

**Auto-confirmation** requires all of:
1. the pair is the *only* remaining candidate for both the order and the payment (mutual uniqueness),
2. it is plausible,
3. name similarity ≥ `auto_name_similarity` (90),
4. the setting `auto_confirm_amount_date` is on.

Anything else becomes *Suggested match*, or *Ambiguous* when candidates compete.

## 4. Stage 3 — composite (`composite_match.py`)

For still-unclaimed records, using date-window blocking:

- **One payment for several orders**: candidate orders smaller than the payment, in the window, with name similarity ≥ 75. The pool is capped at `composite_max_pool` (10), and subsets of size 2..`composite_max_group` (3) are searched for an exact sum.
- **Several payments for one order**: the same search in reverse.

Composite candidates are **never auto-confirmed**, because a coincidental subset sum is too easy to hit. With 10 records and groups of up to 3, at most 165 subsets are evaluated per record.

## 5. Stage 4 — fuzzy name suggestions (`fuzzy_match.py::fuzzy_stage`)

For leftover payments: orders whose date window (widened by `date_tolerance_days` again) contains the payment date are pre-filtered with RapidFuzz `token_set_ratio` (fast, generous). They are then scored with the stricter token-aligned similarity, and the top 3 at or above `min_name_similarity` are kept. These candidates always differ in amount and are **never auto-confirmed**. The evidence list states: "Fuzzy suggestion: name and date are similar but the amount differs."

## 6. Conflict handling (`engine.py::_annotate_conflicts`, `service.py`)

- Every candidate lists the candidates it shares an order or payment with (`conflict_with`). Any auto candidate that has a competitor is demoted to review. Automatic confirmations are therefore conflict-free by construction.
- The service applies rule confirmations first (sorted by candidate ID, which is deterministic), then reviewer confirmations in decision order. Before writing an allocation it checks that:
  - the order is open, in currency, and not already fully allocated;
  - every payment is a customer credit in currency;
  - each payment's allocated total plus the new allocation stays at or below its amount (**no double allocation**).

  A failed check marks the candidate **Blocked** with the reason. Nothing is partially written.
- Open suggestions that touch a fully allocated order or payment become **Superseded**.
- Indistinguishable candidates are never resolved by choosing one at random. They stay *Ambiguous* until a person decides.

## 7. Decisions

Actions: `confirm`, `reject`, `investigate`, `undo` (which clears the candidate's last decision). Manual matches carry their order and transaction IDs inside the decision, so they are rebuilt after a re-run. Candidate IDs hash the sorted order and transaction IDs, so decisions re-attach to the same candidate on every run.

## 8. Status derivation

Order: `Excluded order` → (allocated > 0: `Matched` / `Partial payment` / `Overpayment`) → `Needs investigation` → pending candidates (`Amount mismatch` if any reference-mismatch candidate, `Ambiguous` if more than one, else `Suggested match`) → `Duplicate candidate` → `Unmatched order`.

Bank line: `Ignored` (non-customer kinds or other currency) → (allocated: `Matched`, or `Partial payment` if part of the credit is unallocated) → `Needs investigation` → `Duplicate candidate` → pending (as above) → `Unmatched bank transaction`.

## 9. Settings that change behaviour

All of these live in `config.py::Settings` and are validated by Pydantic: currency, amount tolerance (decimal string), date tolerance, early-payment allowance, minimum / auto-confirm name similarity (auto ≥ minimum), minimum candidate score, the three auto-confirm switches, composite group and pool limits, duplicate detection, score weights (must sum to 100), and whether raw descriptions appear in exports.
