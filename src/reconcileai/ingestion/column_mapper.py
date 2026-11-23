"""Suggest mappings from arbitrary spreadsheet headers to normalized fields."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from rapidfuzz import fuzz

from reconcileai.text import normalize_header


@dataclass(frozen=True)
class FieldSpec:
    name: str
    label: str
    required: bool
    synonyms: tuple[str, ...]


ORDER_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("order_id", "Order ID", True,
              ("order id", "order no", "order number", "ma don hang", "ma don", "so don hang", "ma dh", "invoice no")),
    FieldSpec("customer", "Customer", True,
              ("customer", "customer name", "ten khach hang", "khach hang", "ho ten", "client")),
    FieldSpec("amount", "Order amount", True,
              ("amount", "order amount", "total", "so tien", "tong tien", "thanh tien", "gia tri")),
    FieldSpec("order_date", "Order date", True,
              ("order date", "date", "ngay dat hang", "ngay don hang", "ngay tao", "ngay")),
    FieldSpec("reference", "Payment reference", False,
              ("payment reference", "reference", "ma thanh toan", "noi dung chuyen khoan", "noi dung ck", "ref")),
    FieldSpec("status", "Status", False, ("status", "trang thai", "order status")),
    FieldSpec("currency", "Currency", False, ("currency", "tien te", "loai tien", "ccy")),
)

BANK_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("txn_id", "Transaction ID", True,
              ("transaction id", "txn id", "ma giao dich", "so giao dich", "so tham chieu", "reference no", "so ct")),
    FieldSpec("txn_date", "Transaction date", True,
              ("transaction date", "date", "ngay giao dich", "ngay gd", "ngay hieu luc", "value date", "posting date")),
    FieldSpec("description", "Description", True,
              ("description", "dien giai", "noi dung", "noi dung giao dich", "noi dung chuyen khoan", "narrative", "details", "remarks")),
    FieldSpec("credit", "Incoming (credit)", False,
              ("credit", "tien vao", "so tien ghi co", "ghi co", "co", "deposit", "money in")),
    FieldSpec("debit", "Outgoing (debit)", False,
              ("debit", "tien ra", "so tien ghi no", "ghi no", "no", "withdrawal", "money out")),
    FieldSpec("amount", "Signed amount", False, ("amount", "so tien", "signed amount")),
    FieldSpec("balance", "Balance", False, ("balance", "so du", "so du cuoi")),
    FieldSpec("currency", "Currency", False, ("currency", "tien te", "loai tien", "ccy")),
)

FIELDS = {"orders": ORDER_FIELDS, "bank": BANK_FIELDS}


def suggest_mapping(headers: list[str], kind: str) -> dict[str, str | None]:
    """Return ``{field: header or None}``. Exact synonym hits win, then fuzzy (≥ 85)."""
    specs = FIELDS[kind]
    norm = {h: normalize_header(h) for h in headers}
    mapping: dict[str, str | None] = {s.name: None for s in specs}
    used: set[str] = set()

    # Pass 1: exact synonym, in field order so that specific names beat generic ones.
    for spec in specs:
        for h in headers:
            if h not in used and norm[h] in spec.synonyms:
                mapping[spec.name] = h
                used.add(h)
                break
    # Pass 2: fuzzy on remaining.
    for spec in specs:
        if mapping[spec.name] is not None:
            continue
        best, best_score = None, 0.0
        for h in headers:
            if h in used:
                continue
            score = max(fuzz.ratio(norm[h], syn) for syn in spec.synonyms)
            if score > best_score:
                best, best_score = h, score
        if best is not None and best_score >= 85:
            mapping[spec.name] = best
            used.add(best)
    if kind == "bank" and mapping.get("credit") and mapping.get("amount"):
        mapping["amount"] = None  # prefer explicit credit/debit columns
    return mapping


def missing_required(mapping: dict[str, str | None], kind: str) -> list[str]:
    missing = [s.label for s in FIELDS[kind] if s.required and not mapping.get(s.name)]
    if kind == "bank" and not (mapping.get("credit") or mapping.get("amount")):
        missing.append("Incoming (credit) or Signed amount")
    return missing


# --- mapping templates -------------------------------------------------------------

def load_templates(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_template(path: Path, name: str, kind: str, mapping: dict[str, str | None], date_format: str) -> None:
    templates = load_templates(path)
    templates[name] = {"kind": kind, "mapping": mapping, "date_format": date_format}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(templates, ensure_ascii=False, indent=2), encoding="utf-8")


def apply_template(template: dict, headers: list[str]) -> dict[str, str | None]:
    """Use a saved mapping, dropping columns that are absent from this file."""
    return {f: (h if h in headers else None) for f, h in template.get("mapping", {}).items()}
