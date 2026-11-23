"""Deterministic generator of *fictional* orders, bank statements and ground truth.

All names are random combinations of common Vietnamese name parts; account numbers
are not generated at all. The ground truth is written by the generator from the
scenario it constructs — it is never derived from the matching engine.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd

from reconcileai.text import strip_diacritics

FAMILY = ["Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Huỳnh", "Phan", "Vũ", "Võ", "Đặng", "Bùi", "Đỗ", "Hồ",
          "Ngô", "Dương", "Lý"]
MIDDLE = ["Văn", "Thị", "Hữu", "Minh", "Thanh", "Ngọc", "Đức", "Quốc", "Thu", "Gia", "Xuân", "Hoài"]
GIVEN = ["An", "Bình", "Châu", "Dũng", "Giang", "Hà", "Hải", "Hạnh", "Hiếu", "Hòa", "Hùng", "Khánh", "Lan",
         "Linh", "Long", "Mai", "Nam", "Nga", "Phúc", "Quân", "Sơn", "Tâm", "Thảo", "Trang", "Tuấn", "Vy",
         "Yến", "Khoa", "Nhung", "Trí", "Việt", "Duy"]

CHALLENGING_MIX = {
    "exact_reference": 0.0, "late_reference": 0.03, "no_reference": 0.12, "same_amount_pair": 0.04,
    "ambiguous_name_pair": 0.02, "missing_payment": 0.07, "partial_marked": 0.025, "split_reference": 0.02,
    "split_no_reference": 0.01, "multi_reference": 0.03, "multi_no_reference": 0.014, "underpaid": 0.02,
    "overpaid": 0.013, "refunded": 0.017, "cancelled": 0.033, "duplicate_order": 0.007,
}
CLEAN_MIX = {"exact_reference": 0.0, "no_reference": 0.15, "missing_payment": 0.05}

START = date(2026, 10, 1)
END_ORDERS = date(2026, 11, 12)


@dataclass
class DemoDataset:
    orders: pd.DataFrame
    bank: pd.DataFrame
    truth: dict
    challenging: bool
    seed: int


@dataclass
class _Builder:
    rng: random.Random
    challenging: bool
    names: list[str]
    orders: list[dict] = field(default_factory=list)
    bank: list[dict] = field(default_factory=list)      # temp ids "B<n>"
    assoc: list[dict] = field(default_factory=list)
    closed_payments: list[dict] = field(default_factory=list)
    dup_txn_tmp: list[str] = field(default_factory=list)
    dup_orders: list[str] = field(default_factory=list)
    scenario_of: dict[str, str] = field(default_factory=dict)
    seq: int = 0

    # --- primitives ------------------------------------------------------------
    def name(self) -> str:
        return self.names.pop()

    def amount(self) -> int:
        v = int(self.rng.lognormvariate(14.2, 0.75))
        return max(150, min(v // 1000, 18_000)) * 1000

    def odate(self) -> date:
        return START + timedelta(days=self.rng.randint(0, (END_ORDERS - START).days))

    def order(self, scenario: str, customer: str, amount: int, d: date, status: str = "Chờ thanh toán") -> str:
        self.seq += 1
        oid = f"ORD2026-{self.seq:05d}"
        self.orders.append({"order_id": oid, "customer": customer, "amount": amount, "date": d, "status": status})
        self.scenario_of[oid] = scenario
        return oid

    def txn(self, d: date, desc: str, credit: int = 0, debit: int = 0) -> str:
        tmp = f"B{len(self.bank)}"
        self.bank.append({"tmp": tmp, "date": d, "desc": desc, "credit": credit, "debit": debit})
        return tmp

    def link(self, oid: str, tmp: str, amount: int, scenario: str) -> None:
        self.assoc.append({"order_id": oid, "tmp": tmp, "amount": amount, "scenario": scenario})

    # --- description styles ----------------------------------------------------
    def name_form(self, customer: str, partial: bool = False) -> str:
        parts = customer.split()
        if partial and len(parts) == 3:
            parts = [parts[0], parts[2]]
        n = " ".join(parts)
        r = self.rng.random()
        if r < 0.12 and self.challenging:
            return n  # with diacritics
        n = strip_diacritics(n)
        return n.upper() if r < 0.85 or not self.challenging else n.lower()

    def ref_form(self, oid: str) -> str:
        if not self.challenging:
            return oid
        year, num = oid[3:7], oid[8:]
        return self.rng.choice([oid, oid, f"ORD{year}{num}", f"ord{year} {num}", f"ORD{year}.{num}", oid.lower()])

    def desc_ref(self, customer: str, refs: list[str], extra: str = "") -> str:
        name = self.name_form(customer)
        ref = " ".join(self.ref_form(r) for r in refs)
        n = self.rng.randint(1000000, 9999999)
        templates = [
            f"{name} CK {ref}{extra}", f"{ref} {name}{extra}", f"MBVCB.{n}.{ref}.{name} chuyen tien{extra}",
            f"IBFT {name} thanh toan don {ref}{extra}", f"TT {ref}{extra} {name}",
        ] if self.challenging else [f"{name} CK {ref}{extra}", f"{ref} {name}{extra}"]
        if self.challenging and customer != strip_diacritics(customer):
            templates.append(f"{customer} chuyển khoản {ref}{extra}")
        return self.rng.choice(templates)

    def desc_noref(self, customer: str, partial_name: bool = False) -> str:
        name = self.name_form(customer, partial=partial_name)
        n = self.rng.randint(1000000, 9999999)
        templates = [f"{name} chuyen tien", f"MBVCB.{n}.{name} chuyen khoan", f"{name} thanh toan don hang",
                     f"IBFT {name} TT hoa don", f"TRF FROM {name}"]
        return self.rng.choice(templates if self.challenging else templates[:1])

    def lag(self, lo: int = 0, hi: int = 3) -> timedelta:
        return timedelta(days=self.rng.randint(lo, hi))


def _counts(n: int, mix: dict[str, float]) -> dict[str, int]:
    counts = {k: int(round(n * v)) for k, v in mix.items()}
    for k in ("same_amount_pair", "ambiguous_name_pair", "duplicate_order", "multi_no_reference"):
        if k in counts:
            counts[k] = max(1, counts[k] // 2)  # these create two orders per unit
    if "multi_reference" in counts:
        counts["multi_reference"] = max(1, counts["multi_reference"] // 2)
    used = sum(2 * counts.get(k, 0) for k in ("same_amount_pair", "ambiguous_name_pair", "duplicate_order",
                                             "multi_no_reference", "multi_reference"))
    used += sum(v for k, v in counts.items() if k not in ("same_amount_pair", "ambiguous_name_pair",
                                                         "duplicate_order", "multi_no_reference", "multi_reference"))
    counts["exact_reference"] = max(0, n - used)
    return counts


def generate(n_orders: int = 300, seed: int = 2026, challenging: bool = True) -> DemoDataset:
    rng = random.Random(seed)
    pool = sorted({f"{f} {m} {g}" for f in FAMILY for m in MIDDLE for g in GIVEN})
    rng.shuffle(pool)
    outsiders = pool[:200]                 # names never used for customers
    b = _Builder(rng=rng, challenging=challenging, names=pool[200:])
    counts = _counts(n_orders, CHALLENGING_MIX if challenging else CLEAN_MIX)

    plan = [k for k, c in counts.items() for _ in range(c)]
    rng.shuffle(plan)
    for scen in plan:
        _scenario(b, scen)

    if challenging:
        _noise(b, n_orders, outsiders)

    return _finish(b, seed, challenging, counts)


def _scenario(b: _Builder, scen: str) -> None:  # noqa: C901 — one branch per scenario
    rng = b.rng
    if scen in ("exact_reference", "late_reference"):
        c, a, d = b.name(), b.amount(), b.odate()
        oid = b.order(scen, c, a, d)
        lag = b.lag(6, 9) if scen == "late_reference" else b.lag()
        b.link(oid, b.txn(d + lag, b.desc_ref(c, [oid]), credit=a), a, scen)
    elif scen == "no_reference":
        c, a, d = b.name(), b.amount(), b.odate()
        oid = b.order(scen, c, a, d)
        tmp = b.txn(d + b.lag(), b.desc_noref(c, partial_name=rng.random() < 0.15), credit=a)
        b.link(oid, tmp, a, scen)
    elif scen == "same_amount_pair":
        a, d = b.amount(), b.odate()
        for _ in range(2):
            c = b.name()
            oid = b.order(scen, c, a, d + b.lag(0, 1))
            b.link(oid, b.txn(d + b.lag(1, 3), b.desc_noref(c), credit=a), a, scen)
    elif scen == "ambiguous_name_pair":
        c, a, d = b.name(), b.amount(), b.odate()
        o1 = b.order(scen, c, a, d)
        o2 = b.order(scen, c, a, d + timedelta(days=1))
        t1 = b.txn(d + timedelta(days=1), b.desc_noref(c), credit=a)
        t2 = b.txn(d + timedelta(days=2), b.desc_noref(c), credit=a)
        b.link(o1, t1, a, scen)
        b.link(o2, t2, a, scen)
    elif scen == "missing_payment":
        b.order(scen, b.name(), b.amount(), b.odate())
    elif scen == "partial_marked":
        c, a, d = b.name(), b.amount(), b.odate()
        oid = b.order(scen, c, a, d)
        part = (a * rng.randint(3, 7) // 10) // 1000 * 1000
        b.link(oid, b.txn(d + b.lag(), b.desc_ref(c, [oid], rng.choice([" dot 1", " TRA TRUOC", " DAT COC"])),
                          credit=part), part, scen)
    elif scen == "split_reference":
        c, a, d = b.name(), b.amount(), b.odate()
        oid = b.order(scen, c, a, d)
        p1 = (a * rng.randint(3, 7) // 10) // 1000 * 1000
        b.link(oid, b.txn(d + b.lag(0, 1), b.desc_ref(c, [oid], " lan 1"), credit=p1), p1, scen)
        b.link(oid, b.txn(d + b.lag(2, 4), b.desc_ref(c, [oid], " lan 2"), credit=a - p1), a - p1, scen)
    elif scen == "split_no_reference":
        c, a, d = b.name(), b.amount(), b.odate()
        oid = b.order(scen, c, a, d)
        p1 = (a * rng.randint(3, 7) // 10) // 1000 * 1000
        b.link(oid, b.txn(d + b.lag(0, 1), b.desc_noref(c), credit=p1), p1, scen)
        b.link(oid, b.txn(d + b.lag(2, 3), b.desc_noref(c), credit=a - p1), a - p1, scen)
    elif scen == "multi_reference":
        c, d = b.name(), b.odate()
        oids, amts = [], []
        for _ in range(2):
            a = b.amount()
            oids.append(b.order(scen, c, a, d))
            amts.append(a)
        tmp = b.txn(d + b.lag(), b.desc_ref(c, oids), credit=sum(amts))
        for oid, a in zip(oids, amts, strict=True):
            b.link(oid, tmp, a, scen)
    elif scen == "multi_no_reference":
        c, d = b.name(), b.odate()
        oids, amts = [], []
        for _ in range(2):
            a = b.amount()
            oids.append(b.order(scen, c, a, d))
            amts.append(a)
        tmp = b.txn(d + b.lag(), b.desc_noref(c), credit=sum(amts))
        for oid, a in zip(oids, amts, strict=True):
            b.link(oid, tmp, a, scen)
    elif scen == "underpaid":
        c, a, d = b.name(), b.amount(), b.odate()
        oid = b.order(scen, c, a, d)
        paid = a - rng.choice([10_000, 20_000, 50_000, 100_000])
        b.link(oid, b.txn(d + b.lag(), b.desc_ref(c, [oid]), credit=paid), paid, scen)
    elif scen == "overpaid":
        c, a, d = b.name(), b.amount(), b.odate()
        oid = b.order(scen, c, a, d)
        paid = a + rng.choice([10_000, 50_000, 100_000])
        b.link(oid, b.txn(d + b.lag(), b.desc_ref(c, [oid]), credit=paid), paid, scen)
    elif scen == "refunded":
        c, a, d = b.name(), b.amount(), b.odate()
        oid = b.order(scen, c, a, d, status="Đã hoàn tiền")
        tmp = b.txn(d + b.lag(), b.desc_ref(c, [oid]), credit=a)
        b.txn(d + b.lag(4, 7), f"HOAN TIEN KH {strip_diacritics(c).upper()} {oid}", debit=a)
        b.closed_payments.append({"order_id": oid, "tmp": tmp, "reason": "refunded"})
    elif scen == "cancelled":
        c, a, d = b.name(), b.amount(), b.odate()
        oid = b.order(scen, c, a, d, status="Đã hủy")
        if rng.random() < 0.35:
            tmp = b.txn(d + b.lag(), b.desc_ref(c, [oid]), credit=a)
            b.closed_payments.append({"order_id": oid, "tmp": tmp, "reason": "cancelled"})
    elif scen == "duplicate_order":
        c, a, d = b.name(), b.amount(), b.odate()
        o1 = b.order(scen, c, a, d)
        o2 = b.order(scen, c, a, d)
        b.link(o1, b.txn(d + b.lag(), b.desc_ref(c, [o1]), credit=a), a, scen)
        b.dup_orders.append(o2)
    else:  # pragma: no cover
        raise ValueError(scen)


def _noise(b: _Builder, n: int, outsiders: list[str]) -> None:
    rng = b.rng
    # Duplicate bank lines (same description and amount, next day, different ID)
    exact = [a for a in b.assoc if a["scenario"] == "exact_reference"]
    for a in rng.sample(exact, k=min(len(exact), max(2, n // 60))):
        src = next(x for x in b.bank if x["tmp"] == a["tmp"])
        b.dup_txn_tmp.append(b.txn(src["date"] + timedelta(days=rng.randint(0, 1)), src["desc"], credit=src["credit"]))
    # Bank fees (debits)
    for _ in range(max(3, n // 30)):
        d = START + timedelta(days=rng.randint(0, 52))
        b.txn(d, rng.choice(["PHI SMS BANKING T10", "PHI QUAN LY TAI KHOAN", "Phi dich vu IBFT",
                             "PHI CHUYEN TIEN NHANH"]), debit=rng.choice([11_000, 22_000, 3_300, 55_000]))
    # Internal transfers
    for i in range(max(2, n // 75)):
        d = START + timedelta(days=rng.randint(0, 52))
        amt = rng.randint(20, 80) * 1_000_000
        if i % 2:
            b.txn(d, "CHUYEN TIEN NOI BO TU TK CONG TY", credit=amt)
        else:
            b.txn(d, "CHUYEN VE TK CONG TY - NOI BO", debit=amt)
    # Unrelated incoming payments from non-customers, and supplier payments
    for _ in range(max(3, n // 40)):
        d = START + timedelta(days=rng.randint(0, 52))
        who = strip_diacritics(rng.choice(outsiders)).upper()
        b.txn(d, rng.choice([f"{who} chuyen tien", f"{who} gui tien", f"IBFT {who}"]),
              credit=rng.randint(100, 5000) * 1000)
    for _ in range(max(2, n // 60)):
        d = START + timedelta(days=rng.randint(0, 52))
        b.txn(d, rng.choice(["TT NCC VAN TAI GIAO NHANH", "TT TIEN THUE KHO T11", "TT NCC BAO BI"]),
              debit=rng.randint(500, 20000) * 1000)


def _fmt_amount(v: int, rng: random.Random, challenging: bool) -> str:
    if not v:
        return ""
    if not challenging:
        return str(v)
    style = rng.random()
    if style < 0.4:
        return f"{v:,}".replace(",", ".")
    if style < 0.8:
        return f"{v:,}"
    return str(v)


def _fmt_date(d: date, rng: random.Random, challenging: bool) -> str:
    if challenging and rng.random() < 0.5:
        return d.strftime("%d/%m/%Y")
    return d.isoformat()


def _finish(b: _Builder, seed: int, challenging: bool, counts: dict[str, int]) -> DemoDataset:
    rng = b.rng
    # Sort by date, then creation order, so a duplicate line always follows its original.
    b.bank.sort(key=lambda x: (x["date"], int(x["tmp"][1:])))
    id_of: dict[str, str] = {}
    balance = 250_000_000
    rows = []
    for i, x in enumerate(b.bank, start=1):
        tid = f"TXN-{i:06d}"
        id_of[x["tmp"]] = tid
        balance += x["credit"] - x["debit"]
        rows.append({"txn_id": tid, **x, "balance": balance})

    if challenging:
        bank = pd.DataFrame({
            "Ngày giao dịch": [_fmt_date(r["date"], rng, True) for r in rows],
            "Mã giao dịch": [r["txn_id"] for r in rows],
            "Nội dung": [r["desc"] for r in rows],
            "Tiền vào": [_fmt_amount(r["credit"], rng, True) for r in rows],
            "Tiền ra": [_fmt_amount(r["debit"], rng, True) for r in rows],
            "Số dư": [f"{r['balance']:,}" for r in rows],
        })
        orders = pd.DataFrame({
            "Mã đơn hàng": [o["order_id"] for o in b.orders],
            "Tên khách hàng": [o["customer"] for o in b.orders],
            "Số tiền": [o["amount"] for o in b.orders],
            "Ngày đặt hàng": [o["date"] for o in b.orders],
            "Trạng thái": [o["status"] for o in b.orders],
        })
        # Duplicate imported rows (identical lines the importer must drop)
        orders = pd.concat([orders, orders.iloc[[3, 17]]], ignore_index=True)
        bank = pd.concat([bank, bank.iloc[[5, 40, 77]]], ignore_index=True)
    else:
        bank = pd.DataFrame({
            "Transaction ID": [r["txn_id"] for r in rows],
            "Transaction Date": [r["date"].isoformat() for r in rows],
            "Description": [r["desc"] for r in rows],
            "Credit": [r["credit"] or "" for r in rows],
            "Debit": [r["debit"] or "" for r in rows],
            "Balance": [r["balance"] for r in rows],
        })
        orders = pd.DataFrame({
            "Order ID": [o["order_id"] for o in b.orders],
            "Customer Name": [o["customer"] for o in b.orders],
            "Amount": [o["amount"] for o in b.orders],
            "Order Date": [o["date"].isoformat() for o in b.orders],
            "Status": ["Awaiting payment" for _ in b.orders],
        })

    truth = {
        "description": "Ground truth written by the generator from the constructed scenarios (fictional data).",
        "seed": seed, "challenging": challenging, "currency": "VND",
        "order_count": len(b.orders), "bank_line_count": len(rows),
        "scenario_counts": counts,
        "associations": [{"order_id": a["order_id"], "txn_id": id_of[a["tmp"]], "amount": str(Decimal(a["amount"])),
                          "scenario": a["scenario"]} for a in b.assoc],
        "closed_order_payments": [{"order_id": c["order_id"], "txn_id": id_of[c["tmp"]], "reason": c["reason"]}
                                  for c in b.closed_payments],
        "duplicate_txn_ids": [id_of[t] for t in b.dup_txn_tmp],
        "duplicate_order_ids": b.dup_orders,
        "order_scenarios": b.scenario_of,
    }
    return DemoDataset(orders=orders, bank=bank, truth=truth, challenging=challenging, seed=seed)


def write_dataset(ds: DemoDataset, out_dir: Path, truth_path: Path, stem_orders: str = "orders",
                  stem_bank: str = "bank_statement") -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    truth_path.parent.mkdir(parents=True, exist_ok=True)
    paths = []
    p = out_dir / f"{stem_orders}.xlsx"
    with pd.ExcelWriter(p, engine="openpyxl") as xw:
        ds.orders.to_excel(xw, sheet_name="Orders", index=False)
    paths.append(p)
    p = out_dir / f"{stem_bank}.xlsx"
    with pd.ExcelWriter(p, engine="openpyxl") as xw:
        ds.bank.to_excel(xw, sheet_name="Statement", index=False)
    paths.append(p)
    orders_csv = ds.orders.copy()
    for col in orders_csv.columns:
        if orders_csv[col].map(lambda v: isinstance(v, date)).all():
            orders_csv[col] = orders_csv[col].map(lambda v: v.strftime("%d/%m/%Y") if ds.challenging else v.isoformat())
    p = out_dir / f"{stem_orders}.csv"
    orders_csv.to_csv(p, index=False, encoding="utf-8-sig")
    paths.append(p)
    p = out_dir / f"{stem_bank}.csv"
    ds.bank.to_csv(p, index=False, encoding="utf-8-sig", sep=";" if ds.challenging else ",")
    paths.append(p)
    truth_path.write_text(json.dumps(ds.truth, ensure_ascii=False, indent=2), encoding="utf-8")
    paths.append(truth_path)
    return paths
