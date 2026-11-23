"""Domain models: source records, candidates, allocations and statuses.

Source records (:class:`Order`, :class:`BankTransaction`) are immutable once
imported. Reconciliation *decisions* live separately (:class:`Candidate`,
:class:`Allocation`, review decisions) so the original data is never altered.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum


class OrderState(StrEnum):
    """Business state of an order as given by the source system."""

    OPEN = "open"            # awaiting payment / unpaid / paid-but-unverified
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class TxnKind(StrEnum):
    """Classification of a bank line, decided deterministically from direction + keywords."""

    CREDIT = "credit"          # incoming customer payment candidate
    REVERSAL = "reversal"      # incoming reversal / refund-back of an earlier debit
    INTERNAL = "internal"      # internal transfer between own accounts
    FEE = "fee"                # bank fee (debit)
    REFUND = "refund"          # outgoing refund to a customer (debit)
    DEBIT = "debit"            # any other outgoing payment


class Status(StrEnum):
    MATCHED = "Matched"
    SUGGESTED = "Suggested match"
    UNMATCHED_ORDER = "Unmatched order"
    UNMATCHED_TXN = "Unmatched bank transaction"
    AMOUNT_MISMATCH = "Amount mismatch"
    DUPLICATE = "Duplicate candidate"
    PARTIAL = "Partial payment"
    OVERPAYMENT = "Overpayment"
    AMBIGUOUS = "Ambiguous"
    IGNORED = "Ignored / not applicable"
    EXCLUDED_ORDER = "Excluded order"
    INVESTIGATE = "Needs investigation"


class Rule(StrEnum):
    EXACT_REFERENCE = "exact_reference"
    SPLIT_REFERENCE = "split_reference"          # one order, several referenced credits
    MULTI_REFERENCE = "multi_reference"          # one credit referencing several orders
    REFERENCE_MISMATCH = "reference_amount_mismatch"
    AMOUNT_DATE = "amount_date"
    FUZZY = "fuzzy_candidate"
    COMPOSITE = "composite_amount"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class Order:
    order_id: str
    customer: str
    amount: Decimal
    currency: str
    order_date: date
    reference: str = ""
    raw_status: str = ""
    state: OrderState = OrderState.OPEN
    source_row: int = 0
    # normalized comparison forms
    customer_norm: str = ""
    key: str = ""                # compact order id, e.g. ORD202600125
    reference_key: str = ""      # compact payment reference, if any


@dataclass(frozen=True, slots=True)
class BankTransaction:
    txn_id: str
    txn_date: date
    description: str
    credit: Decimal
    debit: Decimal
    currency: str
    balance: Decimal | None = None
    source_row: int = 0
    kind: TxnKind = TxnKind.CREDIT
    description_norm: str = ""
    name_text: str = ""          # description minus noise tokens, for name similarity

    @property
    def amount(self) -> Decimal:
        """Signed net amount (credit positive, debit negative)."""
        return self.credit - self.debit

    @property
    def is_incoming(self) -> bool:
        return self.credit > 0


@dataclass(slots=True)
class Evidence:
    """One human-readable reason a candidate was proposed."""

    factor: str
    detail: str
    points: float = 0.0


@dataclass(slots=True)
class Candidate:
    """A proposed association between orders and bank transactions."""

    order_ids: tuple[str, ...]
    txn_ids: tuple[str, ...]
    rule: Rule
    score: float
    evidence: list[Evidence] = field(default_factory=list)
    auto: bool = False                       # confirmed by a deterministic rule
    # allocation amounts per (order_id, txn_id) used if the candidate is accepted
    allocations: list[tuple[str, str, Decimal]] = field(default_factory=list)
    conflict_with: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def candidate_id(self) -> str:
        raw = "|".join(sorted(self.order_ids)) + "#" + "|".join(sorted(self.txn_ids))
        return "C-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True, slots=True)
class Allocation:
    order_id: str
    txn_id: str
    amount: Decimal
    candidate_id: str
    source: str           # "rule:<name>" or "review"


@dataclass(slots=True)
class DuplicateGroup:
    kind: str                     # "bank" | "order"
    record_ids: tuple[str, ...]   # first id is the retained original
    reason: str


@dataclass(slots=True)
class ReviewDecision:
    candidate_id: str
    action: str                   # confirm | reject | investigate | undo
    note: str = ""
    actor: str = "local user"
    timestamp: str = ""
    decision_id: int | None = None
    # Only for manual matches created by the reviewer:
    order_ids: tuple[str, ...] = ()
    txn_ids: tuple[str, ...] = ()
