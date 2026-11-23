"""Small builders for hand-written test records."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from reconcileai.ingestion.normalizer import classify_txn
from reconcileai.models import BankTransaction, Order, OrderState
from reconcileai.text import compact, name_tokens, normalize_text


def order(oid: str, customer: str, amount, d: str = "2026-11-01", *, state: OrderState = OrderState.OPEN,
          currency: str = "VND", reference: str = "") -> Order:
    return Order(
        order_id=oid, customer=customer, amount=Decimal(str(amount)), currency=currency,
        order_date=date.fromisoformat(d), reference=reference, raw_status="", state=state, source_row=0,
        customer_norm=normalize_text(customer), key=compact(oid),
        reference_key=compact(reference) if reference else "",
    )


def txn(tid: str, d: str, desc: str, credit=0, debit=0, currency: str = "VND") -> BankTransaction:
    c, db = Decimal(str(credit)), Decimal(str(debit))
    dn = normalize_text(desc)
    return BankTransaction(
        txn_id=tid, txn_date=date.fromisoformat(d), description=desc, credit=c, debit=db, currency=currency,
        balance=None, source_row=0, kind=classify_txn(dn, c, db), description_norm=dn, name_text=name_tokens(desc),
    )
