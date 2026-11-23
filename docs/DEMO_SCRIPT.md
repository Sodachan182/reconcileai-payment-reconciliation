# Five-minute demo

Setup: run `START_RECONCILEAI.bat` beforehand and clear the workspace (Import data → Import history → Clear workspace). Use the dark theme, a 1440×900 window, and browser zoom at 100%.

## 0:00 — The problem (30 s)

> "A shop exports its orders from the sales system and downloads its bank statement. Customers type whatever they want in the transfer note: sometimes the order number, sometimes only their name, sometimes they pay in two parts or pay twice. Matching that by hand takes hours, and a VLOOKUP gets it silently wrong. ReconcileAI does the matching, explains every match, and refuses to export numbers that don't balance."

## 0:30 — Import (60 s)

1. **Import data**. Upload `demo/data/orders.csv`.
2. Point out that the Vietnamese headers (*Mã đơn hàng, Số tiền, Ngày đặt hàng*) were mapped automatically. Show the sample values beside each field and the template selector.
3. Show validation: 302 rows, 300 valid, **2 exact duplicates dropped**. Mention that a file with 03/04/2026-style dates would be blocked until you choose day/month order.
4. Press **Import 300 rows**, then press it again: "Imported 0 new rows". Re-imports never duplicate records.
5. Press **Load demo dataset** to add the bank statement and run matching.

## 1:30 — Overview (45 s)

- Read the equation aloud: *expected 561,968,000 − applied 472,530,000 = unresolved 89,438,000 VND*.
- "Bank-side money is kept separate. About 90 million came in that isn't allocated, and the app doesn't net it against missing payments."
- The charts show the status mix and *why* money is unresolved. Select a row in **Needs attention** to jump into the workspace.

## 2:15 — Reconciliation workspace (90 s)

1. Search `ORD2026-00013` and select it. "Reference found, but the customer paid 361,000 of 411,000. The engine refuses to call that a match, so it's an *Amount mismatch*."
2. Type a note and press **Confirm**. The order becomes *Partial payment*, with 50,000 still outstanding. The overview total moves by exactly 361,000.
3. Search `ORD2026-00040`. Two orders from the same customer for the same amount, and two transfers with no reference. "Mathematically indistinguishable. Instead of choosing at random, the app marks them *Ambiguous* and shows every candidate."
4. Point at the score line: *score 75/100 (ranking heuristic)*. "It ranks candidates. It is not a probability."

## 3:45 — Exceptions and reports (45 s)

1. **Exceptions**: sorted by risk. Filter to *Duplicate*. "This bank line is the same transfer exported twice. It's flagged and never allocated."
2. **Reports**: nine integrity checks with ticks. Download the Excel file and open it to show *Summary*, *Review Log* (your confirmation with its timestamp) and *Configuration*. "If any check fails, this button doesn't exist."

## 4:30 — Engineering close (30 s)

> "Every amount is a Decimal, and floats are rejected rather than rounded. The engine is deterministic and has 109 tests, including property-based tests that throw random data and random decisions at it and check that the books always balance. On generated data with 16 kinds of anomaly it makes zero incorrect automatic matches with about 89% recall, and every remaining case goes to review. It's local-only, so no bank data leaves the machine."

## Likely questions

- *Why not ML?* There's no labelled real data, and finance needs explainability. Deterministic rules with exposed evidence are auditable, and fuzzy matching only proposes.
- *How do you stop double allocation?* Capacity checks run before every allocation, competing suggestions are superseded, conflicting confirmations are blocked with a reason, and an invariant re-checks it independently.
- *Is the 100% precision real?* Yes on this synthetic data, and EVALUATION.md explains why that would not carry over to real statements.
