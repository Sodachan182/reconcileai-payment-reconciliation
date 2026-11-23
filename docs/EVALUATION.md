# Evaluation

All numbers here come from `reconcileai evaluate` and `scripts/benchmark.py`, run on 23 Nov 2026. The raw outputs are committed: [evaluation_results.json](evaluation_results.json), [evaluation_results_clean.json](evaluation_results_clean.json), [benchmark_results.json](benchmark_results.json).

## Ground truth

`src/reconcileai/demo_data.py` builds each scenario explicitly: it creates an order, then the bank line(s) that pay it, and records the association as it writes them. **The matching engine is never used to label data.**

The challenging dataset (seed 2026) has 300 orders and 308 bank lines after import, plus 2 order and 3 bank exact-duplicate rows that the importer must drop. Scenario counts:

| Scenario | Orders | Expected behaviour |
|---|---|---|
| exact_reference | 160 | auto-match |
| late_reference (6–9 days) | 9 | auto-match (references have no date window) |
| no_reference, name in description | 36 | auto if name ≥ 90 and unique, else suggest |
| same_amount_pair (different names) | 12 | auto, resolved by name |
| ambiguous_name_pair (same name and amount) | 6 | **must not** auto-match |
| missing_payment | 21 | unmatched |
| partial_marked (`dot 1`, `TRA TRUOC`, `DAT COC`) | 8 | allocate, remainder unresolved |
| split_reference (`lan 1`, `lan 2`) | 6 | auto-match both credits |
| split_no_reference | 3 | composite suggestion |
| multi_reference (one transfer, 2 orders) | 8 | auto-match |
| multi_no_reference | 4 | composite suggestion |
| underpaid / overpaid with reference | 6 / 4 | amount mismatch, review |
| refunded / cancelled orders | 5 / 10 | out of scope; credits flagged, not applied |
| duplicate_order | 2 | second order flagged |

Noise lines: 5 duplicate bank lines, 10 fees, 4 internal transfers, 7 unrelated incoming payments and 5 supplier payments. Text and format variation includes diacritics or none, upper/lower case, `MBVCB.n.` / `IBFT` / `TRF FROM` prefixes, hyphen/space/dot/no-separator references, mixed ISO and `dd/mm/yyyy` dates in one column, and `1.250.000` / `1,250,000` / `1250000` amounts.

## Metric definitions

- **Matching unit:** one `(order_id, txn_id)` association. A split payment counts as 2 units, and a transfer covering 2 orders counts as 2 units.
- **Automatic:** allocations whose source is a rule (`rule:*`). Precision = correct / all automatic, recall = correct / all true associations.
- **Review coverage:** true associations that were auto-matched **or** appear in an open suggestion. This measures whether a reviewer will see the right answer.
- **Amount allocation accuracy:** among correct automatic pairs, the share whose allocated amount equals the true amount.
- Duplicate detection and closed-order handling are reported separately. They are never folded into one accuracy number.

## Results: challenging dataset (seed 2026)

| Metric | Value |
|---|---|
| True associations | 272 |
| Automatic matches | 241 correct, **0 incorrect**, 31 missed |
| Precision / recall / F1 | **100.0%** / 88.6% / 94.0% |
| Review coverage | 100% (272/272) |
| Suggestions | 37 pairs, of which 31 are correct (the other 6 are the losing sides of ambiguous pairs) |
| Amount allocation accuracy | 241/241 exact |
| Duplicate bank lines | 5/5 found, 0 false |
| Payments on cancelled or refunded orders | 6/6 flagged, 0 allocated |
| Unmatched-order detection | 22/22, 0 false |
| Integrity checks | all passed |

By rule (automatic):

| Rule | Auto pairs | Incorrect |
|---|---|---|
| exact_reference | 170 | 0 |
| amount_date | 43 | 0 |
| split_reference | 20 | 0 |
| multi_reference | 8 | 0 |

By scenario, auto-matched / total:

| Scenario | Auto | Found (auto or suggested) |
|---|---|---|
| exact_reference | 160/160 | 160 |
| late_reference | 9/9 | 9 |
| same_amount_pair | 12/12 | 12 |
| no_reference | 31/36 | 36 (5 written as family + given name only, e.g. `NGUYEN DUY` for Nguyễn Hữu Duy: similarity 75, sent to review) |
| partial_marked | 8/8 | 8 |
| split_reference | 12/12 | 12 |
| multi_reference | 8/8 | 8 |
| ambiguous_name_pair | **0/6** | 6 |
| split_no_reference | 0/6 | 6 |
| multi_no_reference | 0/4 | 4 |
| underpaid | 0/6 | 6 |
| overpaid | 0/4 | 4 |

## Results: clean dataset

285 associations: 282 auto-matched, 0 incorrect, 3 suggested (partial names). Precision 100%, recall 98.9%.

## Stability across seeds (`scripts/benchmark.py --seeds 10`)

| Seed | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| Precision | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| Recall | 0.893 | 0.893 | 0.875 | 0.882 | 0.875 | 0.890 | 0.879 | 0.879 | 0.886 | 0.897 |
| Review coverage | 1.000 | 1.000 | 1.000 | 0.996 | 1.000 | 1.000 | 0.993 | 1.000 | 0.996 | 0.993 |

Coverage below 1.0 means one or two true associations in that dataset were neither matched nor suggested. They remain visible as an unmatched order and an unmatched payment, so nothing is hidden, but a reviewer has to pair them manually.

## A bug the evaluation found

An earlier version removed bank "noise" words such as `THANH` (from *thanh toán*) before comparing names. `THANH`, `TIEN`, `HANG`, `TU` and `HOA` are also common Vietnamese names, so a payment from *LE THANH NAM* scored only 75 against the customer *Lê Thanh Nam* and was sent to review. The noise list now contains only words that are never names (`text.py`). Recall rose from 88.2% to 88.6% with precision unchanged.

## Performance

See the README table. At 5,000 orders and 5,176 lines, the whole pipeline (read, normalize, match, allocate and verify, XLSX) took about 6 s in the latest run (up to 9 s in earlier runs on the same machine) on one core, with a peak of 12.7 MB during matching. Row-wise validation in `normalizer.py` is the largest remaining cost and could be vectorized.

## Limitations of this evaluation

- **Same author.** The generator and the matcher were written together. The generator's text variations are the ones the matcher was designed for, so real bank narratives will contain patterns it has never seen (truncated names, transliterations, sender-bank prefixes, merged notes). Expect lower recall, and test precision on real data before trusting automatic rules.
- **Zero incorrect automatic matches is a property of this data, not a guarantee.** The design reduces the risk (mutual uniqueness, strong-name threshold, no auto for composite or fuzzy candidates, amount must agree), but two real customers with identical names, amounts and dates **and** no reference cannot be told apart by any algorithm. ReconcileAI marks that case *Ambiguous* instead of guessing.
- Ground-truth amounts are integers in VND. USD/EUR paths are covered by unit tests, not by this evaluation.
- The benchmark measures one machine and one process. No concurrency was tested.
