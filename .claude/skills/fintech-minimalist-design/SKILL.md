---
name: fintech-minimalist-design
description: Visual design rules for ReconcileAI's Streamlit interface — financial-operations look, restrained palette, tabular numerals, dense readable tables. Use whenever adding or restyling any UI in src/reconcileai/ui/.
---

# Fintech minimalist design (ReconcileAI)

ReconcileAI is used by finance operations staff who review hundreds of rows a day.
The interface must read like a precise instrument, not a marketing page.

## Tokens (single source of truth: `src/reconcileai/ui/theme.py`)

| Token | Dark | Light | Use |
|---|---|---|---|
| `--bg-primary` | `#0D1015` | `#F7F8FA` | page |
| `--bg-sidebar` | `#14181F` | `#EEF0F3` | navigation |
| `--bg-panel` | `#1A1F27` | `#FFFFFF` | panels, tables |
| `--bg-hover` | `#252B34` | `#E6E9EE` | hover / selected |
| `--border` | `#2B323C` | `#D5DAE1` | 1px rules only |
| `--text-primary` | `#F0F2F5` | `#151A21` | values |
| `--text-secondary` | `#A6AFBB` | `#4A5463` | labels |
| `--text-muted` | `#747F8D` | `#6B7584` | hints |
| `--accent` | `#7D91ED` | `#4157C9` | one primary action, active nav |
| `--success` | `#66B79A` | `#2F7D62` | confirmed / balanced only |
| `--warning` | `#DAB56F` | `#946C1F` | needs review |
| `--danger` | `#DC8585` | `#B23B3B` | mismatch / blocking |

Radii: 6px controls, 10px panels. Never larger. No shadows other than a 1px border.

## Typography
- Family: Geist (Google Fonts), fallback `system-ui`. One family only.
- Scale: 12 (captions) / 13 (tables) / 14 (body) / 16 (section) / 20 (page title). Weights 400/500/600.
- All money and counts use `font-variant-numeric: tabular-nums`.
- Sentence case everywhere. No ALL-CAPS labels, no tracked-out eyebrows.

## Money
- Format with `reconcileai.money.format_amount` — never `f"{x:,.2f}"` on floats.
- Right-align monetary columns; show currency once in the column header ("Amount (VND)"), not in every cell.
- Negative differences use a leading minus sign *and* the danger colour; never colour alone.

## Layout
- Compact header: page title left, the page's single primary action right.
- Metrics render as one bordered strip with vertical dividers, not a grid of floating cards.
- The overview's signature element is the reconciliation equation
  (Expected − Allocated = Unresolved) set as a single typographic line.
- Tables: 13px, 32px rows, sticky header, no zebra striping.

## Charts (Plotly)
- Transparent background, 1px gridlines in `--border`, no legend when a single series.
- Status colours come from the status palette in `theme.py`; never default Plotly colours.
- No 3D, no donut-with-a-number-in-the-middle, no gradients.

## Anti-slop checklist (reject the change if any apply)
- Gradients, glows, neon, emoji in navigation, decorative illustrations.
- More than one accent colour on a screen.
- Cards nested inside cards; identical rounded cards for everything.
- Fake "AI insight" copy. The matcher is deterministic and fuzzy — say so.
- Buttons that do nothing.
