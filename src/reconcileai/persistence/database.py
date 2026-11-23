"""Local SQLite workspace: imported records, matching runs, decisions and audit log.

Single-user and local-first. Amounts are stored as exact decimal TEXT. Review
decisions and audit events are append-only; "undo" is recorded as a new row.
Multi-step writes run inside one transaction so a failure never leaves partial
imports or allocations behind.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from reconcileai.config import Settings
from reconcileai.ingestion.normalizer import ImportReport
from reconcileai.models import (
    BankTransaction,
    Order,
    OrderState,
    ReviewDecision,
    TxnKind,
)

DEFAULT_DB = Path(os.environ.get("RECONCILEAI_DB", Path("data") / "reconcileai.db"))

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS import_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('orders','bank')),
    source_name TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    rows_total INTEGER NOT NULL,
    rows_added INTEGER NOT NULL,
    rows_unchanged INTEGER NOT NULL,
    rows_conflicting INTEGER NOT NULL,
    rows_rejected INTEGER NOT NULL,
    report_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    customer TEXT NOT NULL,
    amount TEXT NOT NULL,
    currency TEXT NOT NULL,
    order_date TEXT NOT NULL,
    reference TEXT NOT NULL DEFAULT '',
    raw_status TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    source_row INTEGER NOT NULL,
    customer_norm TEXT NOT NULL,
    key TEXT NOT NULL,
    reference_key TEXT NOT NULL DEFAULT '',
    import_id INTEGER NOT NULL REFERENCES import_sessions(id)
);
CREATE TABLE IF NOT EXISTS bank_transactions (
    txn_id TEXT PRIMARY KEY,
    txn_date TEXT NOT NULL,
    description TEXT NOT NULL,
    credit TEXT NOT NULL,
    debit TEXT NOT NULL,
    currency TEXT NOT NULL,
    balance TEXT,
    source_row INTEGER NOT NULL,
    kind TEXT NOT NULL,
    description_norm TEXT NOT NULL,
    name_text TEXT NOT NULL,
    import_id INTEGER NOT NULL REFERENCES import_sessions(id)
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    settings_json TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    integrity_ok INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS candidates (
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    candidate_id TEXT NOT NULL,
    rule TEXT NOT NULL,
    score REAL NOT NULL,
    state TEXT NOT NULL,
    order_ids TEXT NOT NULL,
    txn_ids TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    PRIMARY KEY (run_id, candidate_id)
);
CREATE TABLE IF NOT EXISTS allocations (
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    order_id TEXT NOT NULL,
    txn_id TEXT NOT NULL,
    amount TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('confirm','reject','investigate','undo')),
    note TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    order_ids TEXT NOT NULL DEFAULT '[]',
    txn_ids TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    event TEXT NOT NULL,
    detail_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    value_json TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS decisions_append_only_u BEFORE UPDATE ON decisions
BEGIN SELECT RAISE(ABORT, 'decisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS decisions_append_only_d BEFORE DELETE ON decisions
WHEN (SELECT COUNT(*) FROM orders) + (SELECT COUNT(*) FROM bank_transactions) > 0
BEGIN SELECT RAISE(ABORT, 'decisions are append-only while records exist'); END;
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class Workspace:
    def __init__(self, path: Path | str = DEFAULT_DB):
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: we issue BEGIN/COMMIT explicitly in tx().
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        if str(self.path) != ":memory:":
            with contextlib.suppress(OSError):
                os.chmod(self.path, 0o600)  # owner-only where the OS supports it

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def tx(self):
        try:
            self.conn.execute("BEGIN")
            yield self.conn
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    # --- audit ------------------------------------------------------------------
    def _audit(self, conn, event: str, detail: dict) -> None:
        conn.execute("INSERT INTO audit_log(at, event, detail_json) VALUES (?,?,?)",
                     (now_iso(), event, json.dumps(detail, ensure_ascii=False, default=str)))

    def audit_log(self, limit: int = 500) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) | {"detail": json.loads(r["detail_json"])} for r in rows]

    # --- settings -----------------------------------------------------------------
    def load_settings(self) -> Settings:
        row = self.conn.execute("SELECT value_json FROM settings WHERE id = 1").fetchone()
        if not row:
            return Settings()
        try:
            return Settings.model_validate_json(row["value_json"])
        except Exception:
            return Settings()

    def save_settings(self, settings: Settings) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO settings(id, value_json) VALUES (1, ?) "
                      "ON CONFLICT(id) DO UPDATE SET value_json = excluded.value_json",
                      (settings.model_dump_json(),))
            self._audit(c, "settings_saved", settings.model_dump(mode="json"))

    # --- imports ------------------------------------------------------------------
    def import_orders(self, records: list[Order], report: ImportReport) -> dict:
        return self._import("orders", records, report)

    def import_bank(self, records: list[BankTransaction], report: ImportReport) -> dict:
        return self._import("bank", records, report)

    def _import(self, kind: str, records: list, report: ImportReport) -> dict:
        table, key = ("orders", "order_id") if kind == "orders" else ("bank_transactions", "txn_id")
        existing = {r[key]: r for r in self.conn.execute(f"SELECT * FROM {table}").fetchall()}  # noqa: S608
        added, unchanged, conflicting = [], 0, []
        for rec in records:
            row = _to_row(rec)
            prior = existing.get(row[key])
            if prior is None:
                added.append(row)
            elif all(str(prior[k]) == str(v) for k, v in row.items() if k != "source_row"):
                unchanged += 1
            else:
                conflicting.append(row[key])
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO import_sessions(kind, source_name, imported_at, rows_total, rows_added, rows_unchanged,"
                " rows_conflicting, rows_rejected, report_json) VALUES (?,?,?,?,?,?,?,?,?)",
                (kind, report.source_name, now_iso(), report.total_rows, len(added), unchanged, len(conflicting),
                 report.rejected_rows,
                 json.dumps({"warnings": report.warnings[:200], "errors": [e.__dict__ for e in report.errors[:200]],
                             "conflicting_ids": conflicting[:200]}, ensure_ascii=False, default=str)),
            )
            import_id = cur.lastrowid
            for row in added:
                row["import_id"] = import_id
                cols = ",".join(row)
                c.execute(f"INSERT INTO {table}({cols}) VALUES ({','.join('?' * len(row))})",  # noqa: S608
                          list(row.values()))
            self._audit(c, f"import_{kind}", {"source": report.source_name, "added": len(added),
                                              "unchanged": unchanged, "conflicting": conflicting[:50],
                                              "rejected_rows": report.rejected_rows})
        return {"added": len(added), "unchanged": unchanged, "conflicting": conflicting, "import_id": import_id}

    def import_sessions(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM import_sessions ORDER BY id DESC").fetchall()]

    def orders(self) -> list[Order]:
        rows = self.conn.execute("SELECT * FROM orders ORDER BY order_date, order_id").fetchall()
        return [Order(order_id=r["order_id"], customer=r["customer"], amount=Decimal(r["amount"]),
                      currency=r["currency"], order_date=date.fromisoformat(r["order_date"]),
                      reference=r["reference"], raw_status=r["raw_status"], state=OrderState(r["state"]),
                      source_row=r["source_row"], customer_norm=r["customer_norm"], key=r["key"],
                      reference_key=r["reference_key"]) for r in rows]

    def transactions(self) -> list[BankTransaction]:
        rows = self.conn.execute("SELECT * FROM bank_transactions ORDER BY txn_date, txn_id").fetchall()
        return [BankTransaction(txn_id=r["txn_id"], txn_date=date.fromisoformat(r["txn_date"]),
                                description=r["description"], credit=Decimal(r["credit"]),
                                debit=Decimal(r["debit"]), currency=r["currency"],
                                balance=Decimal(r["balance"]) if r["balance"] is not None else None,
                                source_row=r["source_row"], kind=TxnKind(r["kind"]),
                                description_norm=r["description_norm"], name_text=r["name_text"]) for r in rows]

    # --- decisions ------------------------------------------------------------------
    def add_decision(self, d: ReviewDecision) -> ReviewDecision:
        if d.action not in ("confirm", "reject", "investigate", "undo"):
            raise ValueError(f"unknown action {d.action}")
        ts = d.timestamp or now_iso()
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO decisions(candidate_id, action, note, actor, created_at, order_ids, txn_ids)"
                " VALUES (?,?,?,?,?,?,?)",
                (d.candidate_id, d.action, d.note, d.actor, ts, json.dumps(list(d.order_ids)),
                 json.dumps(list(d.txn_ids))),
            )
            self._audit(c, f"decision_{d.action}", {"candidate_id": d.candidate_id, "note": d.note,
                                                    "actor": d.actor, "order_ids": list(d.order_ids),
                                                    "txn_ids": list(d.txn_ids)})
        return ReviewDecision(d.candidate_id, d.action, d.note, d.actor, ts, cur.lastrowid, d.order_ids, d.txn_ids)

    def decisions(self) -> list[ReviewDecision]:
        rows = self.conn.execute("SELECT * FROM decisions ORDER BY id").fetchall()
        return [ReviewDecision(r["candidate_id"], r["action"], r["note"], r["actor"], r["created_at"], r["id"],
                               tuple(json.loads(r["order_ids"])), tuple(json.loads(r["txn_ids"]))) for r in rows]

    # --- runs -----------------------------------------------------------------------
    def save_run(self, rs) -> int:
        """Persist one reconciliation snapshot (candidates, states, allocations) atomically."""
        from dataclasses import asdict

        summary = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in asdict(rs.summary).items()}
        summary = json.loads(json.dumps(summary, default=str))
        with self.tx() as c:
            cur = c.execute("INSERT INTO runs(started_at, input_hash, settings_json, summary_json, integrity_ok)"
                            " VALUES (?,?,?,?,?)",
                            (now_iso(), rs.match.input_hash, rs.settings.model_dump_json(),
                             json.dumps(summary), int(rs.integrity_ok)))
            run_id = cur.lastrowid
            c.executemany(
                "INSERT INTO candidates(run_id, candidate_id, rule, score, state, order_ids, txn_ids, evidence_json)"
                " VALUES (?,?,?,?,?,?,?,?)",
                [(run_id, cid, cand.rule.value, cand.score, rs.cand_state[cid], json.dumps(cand.order_ids),
                  json.dumps(cand.txn_ids), json.dumps([asdict(e) for e in cand.evidence], ensure_ascii=False))
                 for cid, cand in rs.candidates.items()])
            c.executemany(
                "INSERT INTO allocations(run_id, order_id, txn_id, amount, candidate_id, source) VALUES (?,?,?,?,?,?)",
                [(run_id, a.order_id, a.txn_id, str(a.amount), a.candidate_id, a.source) for a in rs.allocations])
            self._audit(c, "matching_run", {"run_id": run_id, "input_hash": rs.match.input_hash,
                                            "candidates": len(rs.candidates), "allocations": len(rs.allocations),
                                            "integrity_ok": rs.integrity_ok})
        return run_id

    def runs(self, limit: int = 20) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT id, started_at, input_hash, integrity_ok FROM runs "
                                                   "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

    # --- clearing ---------------------------------------------------------------------
    def clear(self) -> None:
        """Delete all imported data, runs and decisions. The audit log keeps a record of the clear."""
        with self.tx() as c:
            for t in ("allocations", "candidates", "runs", "orders", "bank_transactions"):
                c.execute(f"DELETE FROM {t}")  # noqa: S608
            c.execute("DELETE FROM decisions")
            c.execute("DELETE FROM import_sessions")
            self._audit(c, "workspace_cleared", {})

    def counts(self) -> dict[str, int]:
        q = self.conn.execute
        return {"orders": q("SELECT COUNT(*) FROM orders").fetchone()[0],
                "transactions": q("SELECT COUNT(*) FROM bank_transactions").fetchone()[0],
                "decisions": q("SELECT COUNT(*) FROM decisions").fetchone()[0],
                "runs": q("SELECT COUNT(*) FROM runs").fetchone()[0]}


def _to_row(rec) -> dict:
    out = {}
    for k in rec.__slots__:
        v = getattr(rec, k)
        if isinstance(v, Decimal):
            v = str(v)
        elif isinstance(v, date):
            v = v.isoformat()
        elif hasattr(v, "value"):
            v = v.value
        out[k] = v
    return out
