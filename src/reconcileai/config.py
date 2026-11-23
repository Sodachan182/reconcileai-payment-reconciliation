"""Matching configuration (validated with Pydantic).

Every option here changes engine behaviour; see docs/MATCHING_ALGORITHM.md.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from reconcileai.money import SUPPORTED_CURRENCIES


class ScoreWeights(BaseModel):
    """Points awarded per evidence factor for amount/date and fuzzy candidates.

    The total is a 0–100 ranking heuristic, not a probability.
    """

    model_config = ConfigDict(frozen=True)

    amount: float = Field(40, ge=0, le=100, description="Exact amount agreement")
    date: float = Field(20, ge=0, le=100, description="Date proximity (linear decay)")
    name: float = Field(30, ge=0, le=100, description="Customer name similarity")
    reference: float = Field(10, ge=0, le=100, description="Partial reference fragment")

    @model_validator(mode="after")
    def _sum_100(self) -> ScoreWeights:
        total = self.amount + self.date + self.name + self.reference
        if abs(total - 100) > 1e-9:
            raise ValueError(f"score weights must sum to 100 (got {total:g})")
        return self


class Settings(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    currency: str = Field("VND", description="Reconciliation currency")
    amount_tolerance: str = Field(
        "0", description="Absolute tolerance for amount equality (exact Decimal string)"
    )
    date_tolerance_days: int = Field(
        5, ge=0, le=60, description="Max days a payment may arrive after the order"
    )
    early_payment_days: int = Field(
        1, ge=0, le=30, description="Max days a payment may precede the order date"
    )
    min_name_similarity: int = Field(
        75, ge=50, le=100, description="Minimum name similarity (0-100) for fuzzy candidates"
    )
    auto_name_similarity: int = Field(
        90, ge=50, le=100,
        description="Name similarity required to auto-confirm a unique amount/date match",
    )
    min_candidate_score: int = Field(
        55, ge=0, le=100, description="Candidates scoring below this are not suggested"
    )
    auto_confirm_exact_reference: bool = Field(
        True, description="Auto-confirm reference + exact amount matches"
    )
    auto_confirm_amount_date: bool = Field(
        True,
        description="Auto-confirm amount/date matches that are mutually unique and have strong name evidence",
    )
    auto_confirm_reference_partial: bool = Field(
        True,
        description="Auto-allocate referenced underpayments marked as instalments or split across several credits",
    )
    composite_max_group: int = Field(
        3, ge=2, le=5, description="Max records combined in one composite match"
    )
    composite_max_pool: int = Field(
        10, ge=3, le=20, description="Max candidate records searched per composite match"
    )
    detect_duplicates: bool = Field(True, description="Flag duplicate bank/order lines")
    weights: ScoreWeights = Field(default_factory=ScoreWeights)
    export_include_raw: bool = Field(
        True, description="Include raw bank descriptions in exported sheets"
    )

    @field_validator("currency")
    @classmethod
    def _currency(cls, v: str) -> str:
        v = v.upper()
        if v not in SUPPORTED_CURRENCIES:
            raise ValueError(f"currency must be one of {sorted(SUPPORTED_CURRENCIES)}")
        return v

    @field_validator("amount_tolerance")
    @classmethod
    def _tolerance(cls, v: str) -> str:
        from decimal import Decimal, InvalidOperation

        try:
            d = Decimal(v)
        except InvalidOperation as exc:
            raise ValueError("amount tolerance must be a number") from exc
        if d < 0:
            raise ValueError("amount tolerance cannot be negative")
        return str(d)

    @model_validator(mode="after")
    def _thresholds(self) -> Settings:
        if self.auto_name_similarity < self.min_name_similarity:
            raise ValueError("auto-confirm similarity must be ≥ minimum similarity")
        return self

    @property
    def tolerance(self):
        from decimal import Decimal

        return Decimal(self.amount_tolerance)


DEFAULT_SETTINGS = Settings()
