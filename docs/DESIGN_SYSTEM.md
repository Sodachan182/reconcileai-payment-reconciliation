# Design system

The rules are enforced by the project skill [`.claude/skills/fintech-minimalist-design`](../.claude/skills/fintech-minimalist-design/SKILL.md). The workflow rules live in [`reconciliation-workflow-ux`](../.claude/skills/reconciliation-workflow-ux/SKILL.md), and verification in [`visual-qa`](../.claude/skills/visual-qa/SKILL.md).

## Concept

The product is a precise instrument for operations staff who review hundreds of rows a day. One element carries the visual weight: the **reconciliation equation** on the overview, set as a single line of large tabular figures:

```
561,968,000  −  472,530,000  =  89,438,000  VND
Expected        Confirmed payments applied   Unresolved
```

Everything else is quiet. Metrics sit in one bordered strip with dividers rather than floating cards. Tables are dense, and colour is reserved for financial status.

## Tokens

Defined once in `src/reconcileai/ui/theme.py` and mirrored in `.streamlit/config.toml` (`[theme.dark]`, `[theme.light]`).

| Token | Dark | Light |
|---|---|---|
| bg-primary | `#0D1015` | `#F7F8FA` |
| bg-sidebar | `#14181F` | `#EEF0F3` |
| bg-panel | `#1A1F27` | `#FFFFFF` |
| border | `#2B323C` | `#D5DAE1` |
| text-primary / secondary / muted | `#F0F2F5` / `#A6AFBB` / `#747F8D` | `#151A21` / `#4A5463` / `#6B7584` |
| accent (one primary action per screen) | `#7D91ED` | `#4157C9` |
| success / warning / danger | `#66B79A` / `#DAB56F` / `#DC8585` | `#2F7D62` / `#946C1F` / `#B23B3B` |

Radii are 6 px for controls and 10 px for panels. The only shadow is a 1 px border.

## Type

Geist (Google Fonts) at 14 px base. The scale is 12 / 13 / 14 / 16 / 20 px, with weights 400, 500 and 600. Tabular numerals are on globally. Labels use sentence case, with no all-caps eyebrows.

## Numbers

- `money.format_amount` everywhere: thousands separators, fixed currency decimals, and a real minus sign (`−`).
- Money columns are right-aligned and named "Amount (VND)". The currency appears once in the header, not in every cell.
- Grids keep numeric types so sorting is numeric, and a Pandas Styler formats the display values.
- Missing values show as `—`, never `None`.

## Status colour

| Tone | Statuses |
|---|---|
| success | Matched |
| warning | Suggested, Ambiguous, Partial payment, Needs investigation |
| danger | Amount mismatch, Overpayment, Duplicate, Unmatched |
| muted | Ignored, Excluded |

Status is always written out as text. Colour reinforces it and never replaces it. On the Exceptions page only **Severity** is coloured, to avoid a wall of red.

## Layout

- Sidebar: logo, six pages, a local-data note. Collapsible (native).
- Page header: compact title and one-line description on the left, the single primary action on the right.
- Reconciliation workspace: orders on the left, bank lines on the right, columns ordered ID → date → amount → status → free text. The free-text column absorbs overflow. The selected row is tinted, and related candidates get a lighter tint. The evidence panel below compares the two sides in one table with columns per record.

## Rejected on purpose

Gradients, glow, emoji navigation, decorative illustrations, card-in-card nesting, "AI insight" copy, donut charts, multiple accent colours, and toast-on-every-hover motion.
