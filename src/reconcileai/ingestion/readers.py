"""Safe CSV / XLSX readers returning raw tables of untyped cells."""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

MAX_FILE_BYTES = 25 * 1024 * 1024          # 25 MB upload limit
MAX_UNCOMPRESSED_XLSX = 250 * 1024 * 1024  # zip-bomb guard
MAX_ROWS = 200_000


class FileReadError(ValueError):
    """A file could not be read. Message is safe to show to the user."""


@dataclass
class RawTable:
    frame: pd.DataFrame
    source_name: str
    file_type: str
    sheet_names: list[str] = field(default_factory=list)
    sheet: str | None = None
    encoding: str | None = None
    delimiter: str | None = None
    empty_rows_skipped: int = 0
    warnings: list[str] = field(default_factory=list)


def _check_size(data: bytes) -> None:
    if not data:
        raise FileReadError("The file is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise FileReadError(
            f"The file is {len(data) / 1e6:.1f} MB; the limit is {MAX_FILE_BYTES // 1_000_000} MB."
        )


def _decode(data: bytes, encoding: str | None) -> tuple[str, str, list[str]]:
    warnings: list[str] = []
    if encoding:
        try:
            return data.decode(encoding), encoding, warnings
        except (UnicodeDecodeError, LookupError) as exc:
            raise FileReadError(f"The file is not valid {encoding} text.") from exc
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="strict"), "utf-8-sig", warnings
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "utf-16", warnings
    try:
        return data.decode("utf-8"), "utf-8", warnings
    except UnicodeDecodeError:
        pass
    # Vietnamese Windows legacy code page. Only accepted when it decodes cleanly.
    try:
        text = data.decode("cp1258")
    except UnicodeDecodeError as exc:
        raise FileReadError(
            "The file's text encoding could not be detected. Save it as UTF-8 CSV and retry."
        ) from exc
    warnings.append("File is not UTF-8; decoded as Windows-1258 (Vietnamese). Check names in the preview.")
    return text, "cp1258", warnings


def _sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        first = sample.splitlines()[0] if sample else ""
        counts = {d: first.count(d) for d in ",;\t|"}
        best = max(counts, key=counts.get)
        return best if counts[best] else ","


def _finish(frame: pd.DataFrame, table: RawTable) -> RawTable:
    frame = frame.copy()
    frame.columns = [str(c).strip() for c in frame.columns]
    if any(c == "" or c.lower().startswith("unnamed:") for c in frame.columns):
        table.warnings.append("Some columns have no header; they are shown as 'Unnamed'.")
    seen: set[str] = set()
    dupes = [c for c in frame.columns if c in seen or seen.add(c)]
    if dupes:
        raise FileReadError(f"Duplicate column headers: {', '.join(dupes)}.")

    def _blank(v: object) -> bool:
        return v is None or (isinstance(v, float) and v != v) or (isinstance(v, str) and not v.strip())

    empty_mask = frame.apply(lambda row: all(_blank(v) for v in row), axis=1) if len(frame) else []
    table.empty_rows_skipped = int(sum(empty_mask)) if len(frame) else 0
    if table.empty_rows_skipped:
        # Keep the original (1-based spreadsheet) row numbers for error messages.
        frame = frame[~empty_mask]
        table.warnings.append(f"Skipped {table.empty_rows_skipped} empty row(s).")
    if frame.empty:
        raise FileReadError("The file has a header row but no data rows.")
    if len(frame) > MAX_ROWS:
        raise FileReadError(f"The file has {len(frame):,} rows; the limit is {MAX_ROWS:,}.")
    table.frame = frame
    return table


def read_csv_bytes(
    data: bytes, name: str = "upload.csv", *, encoding: str | None = None, delimiter: str | None = None
) -> RawTable:
    _check_size(data)
    text, used_encoding, warnings = _decode(data, encoding)
    if not text.strip():
        raise FileReadError("The file is empty.")
    delim = delimiter or _sniff_delimiter(text[:8192])
    try:
        frame = pd.read_csv(
            io.StringIO(text), sep=delim, dtype=str, keep_default_na=False,
            skip_blank_lines=False, engine="python",
        )
    except (pd.errors.ParserError, csv.Error) as exc:
        raise FileReadError(f"The CSV could not be parsed: {exc}") from exc
    # 1-based spreadsheet row numbers (header is row 1)
    frame.index = range(2, len(frame) + 2)
    table = RawTable(frame=frame, source_name=name, file_type="csv",
                     encoding=used_encoding, delimiter=delim, warnings=warnings)
    return _finish(frame, table)


def _xlsx_guard(data: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            total = sum(info.file_size for info in zf.infolist())
            if total > MAX_UNCOMPRESSED_XLSX:
                raise FileReadError("The workbook expands to an unreasonable size and was rejected.")
            if "xl/workbook.xml" not in zf.namelist():
                raise FileReadError("The file is not a valid XLSX workbook.")
    except zipfile.BadZipFile as exc:
        raise FileReadError("The XLSX file is corrupted or not an Excel workbook.") from exc


def list_sheets(data: bytes) -> list[str]:
    _check_size(data)
    _xlsx_guard(data)
    try:
        with pd.ExcelFile(io.BytesIO(data), engine="openpyxl") as xf:
            return list(xf.sheet_names)
    except Exception as exc:  # openpyxl raises many types for corrupt files
        raise FileReadError("The XLSX file could not be opened; it may be corrupted.") from exc


def read_xlsx_bytes(data: bytes, name: str = "upload.xlsx", *, sheet: str | None = None) -> RawTable:
    sheets = list_sheets(data)
    target = sheet or sheets[0]
    if target not in sheets:
        raise FileReadError(f"Worksheet '{target}' not found. Available: {', '.join(sheets)}.")
    try:
        # Cached cell values only: formulas are never evaluated.
        frame = pd.read_excel(io.BytesIO(data), sheet_name=target, dtype=object, engine="openpyxl")
    except Exception as exc:
        raise FileReadError(f"Worksheet '{target}' could not be read: {exc}") from exc
    frame.index = range(2, len(frame) + 2)
    table = RawTable(frame=frame, source_name=name, file_type="xlsx", sheet_names=sheets, sheet=target)
    return _finish(frame, table)


def read_table(
    data: bytes, name: str, *, sheet: str | None = None, encoding: str | None = None,
    delimiter: str | None = None,
) -> RawTable:
    suffix = Path(name).suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        return read_xlsx_bytes(data, name, sheet=sheet)
    if suffix in (".csv", ".txt", ".tsv"):
        return read_csv_bytes(data, name, encoding=encoding, delimiter=delimiter)
    if suffix == ".xls":
        raise FileReadError("Legacy .xls files are not supported. Save the file as .xlsx or CSV.")
    raise FileReadError(f"Unsupported file type '{suffix or name}'. Use .xlsx or .csv.")


def read_path(path: str | Path, **kwargs) -> RawTable:
    p = Path(path)
    return read_table(p.read_bytes(), p.name, **kwargs)
