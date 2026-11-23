"""Shared import pipeline used by both the CLI and the Streamlit UI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from reconcileai.config import Settings
from reconcileai.ingestion.column_mapper import suggest_mapping
from reconcileai.ingestion.normalizer import ImportReport, normalize_bank, normalize_orders
from reconcileai.ingestion.readers import RawTable, read_path
from reconcileai.models import BankTransaction, Order


@dataclass
class LoadedSource:
    table: RawTable
    mapping: dict[str, str | None]
    report: ImportReport
    records: list


def load_orders(path: str | Path, settings: Settings, *, sheet: str | None = None,
                date_format: str = "auto", mapping: dict | None = None) -> LoadedSource:
    table = read_path(path, sheet=sheet)
    mapping = mapping or suggest_mapping(list(table.frame.columns), "orders")
    records, report = normalize_orders(table, mapping, settings, date_format)
    return LoadedSource(table, mapping, report, records)


def load_bank(path: str | Path, settings: Settings, *, sheet: str | None = None,
              date_format: str = "auto", mapping: dict | None = None) -> LoadedSource:
    table = read_path(path, sheet=sheet)
    mapping = mapping or suggest_mapping(list(table.frame.columns), "bank")
    records, report = normalize_bank(table, mapping, settings, date_format)
    return LoadedSource(table, mapping, report, records)


def load_pair(orders_path, bank_path, settings: Settings) -> tuple[list[Order], list[BankTransaction], LoadedSource, LoadedSource]:
    o = load_orders(orders_path, settings)
    b = load_bank(bank_path, settings)
    return o.records, b.records, o, b
