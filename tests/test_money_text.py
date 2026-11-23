from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from reconcileai.matching.scoring import name_similarity_text
from reconcileai.money import AmountError, dsum, format_amount, from_minor_units, parse_amount, to_minor_units
from reconcileai.text import compact, normalize_text, reference_keys, strip_diacritics


@pytest.mark.parametrize("raw,expected", [
    ("1.250.000", "1250000"), ("1,250,000", "1250000"), ("1 250 000 đ", "1250000"), ("1250000", "1250000"),
    ("1.250.000 VNĐ", "1250000"), (1250000, "1250000"), (1250000.0, "1250000"), ("1250000.00", "1250000"),
    ("(500.000)", "-500000"), ("-500000", "-500000"), (Decimal("750000"), "750000"), ("1,250", "1250"),
])
def test_parse_vnd_variants(raw, expected):
    assert parse_amount(raw, "VND") == Decimal(expected)


@pytest.mark.parametrize("raw", ["abc", "", None, "1.25.000", "1250000.5", "12,5", float("nan"), True, "1,2,3"])
def test_parse_vnd_rejects_invalid_or_fractional(raw):
    with pytest.raises(AmountError):
        parse_amount(raw, "VND")


@pytest.mark.parametrize("raw,expected", [
    ("1,250.50", "1250.50"), ("1.250,50", "1250.50"), ("12.5", "12.50"), (12.34, "12.34"), ("0.1", "0.10"),
    ("1,250,000.00", "1250000.00"),
])
def test_parse_usd(raw, expected):
    assert parse_amount(raw, "USD") == Decimal(expected)


def test_usd_rejects_sub_cent_instead_of_rounding():
    with pytest.raises(AmountError):
        parse_amount("10.005", "USD")
    with pytest.raises(AmountError):
        parse_amount(0.125, "USD")
    with pytest.raises(AmountError, match="ambiguous"):
        parse_amount("1.250", "USD")


def test_float_artifacts_never_leak():
    # 0.1 + 0.2 style artefacts must be impossible: amounts are exact decimals
    assert parse_amount(0.1, "USD") + parse_amount(0.2, "USD") == Decimal("0.30")
    assert dsum([parse_amount("0.10", "USD")] * 3) == Decimal("0.30")


def test_unsupported_currency():
    with pytest.raises(AmountError):
        parse_amount("10", "XYZ")


def test_format_amount():
    assert format_amount(Decimal("1250000"), "VND") == "1,250,000"
    assert format_amount(Decimal("-50000"), "VND") == "−50,000"
    assert format_amount(Decimal("50000"), "VND", signed=True) == "+50,000"
    assert format_amount(Decimal("1234.5"), "USD") == "1,234.50"


def test_minor_units_round_trip():
    assert to_minor_units(Decimal("12.34"), "USD") == 1234
    assert from_minor_units(1234, "USD") == Decimal("12.34")
    assert to_minor_units(Decimal("1250000"), "VND") == 1250000


@given(st.integers(min_value=0, max_value=10**12))
def test_vnd_format_parse_round_trip(n):
    v = Decimal(n)
    assert parse_amount(format_amount(v, "VND"), "VND") == v


@given(st.integers(min_value=0, max_value=10**11))
def test_usd_format_parse_round_trip(cents):
    v = from_minor_units(cents, "USD")
    assert parse_amount(format_amount(v, "USD"), "USD") == v


def test_vietnamese_normalization():
    assert strip_diacritics("Nguyễn Văn Đức") == "Nguyen Van Duc"
    assert normalize_text("  Trần   Thị  Hà, CK! ") == "TRAN THI HA CK"
    assert compact("ord2026-00125") == "ORD202600125"


def test_reference_keys_handle_spacing_and_separators():
    for desc in ["CK ORD2026-00125", "ORD202600125 NGUYEN VAN AN", "ck ord2026 00125", "MBVCB.123.ORD2026.00125.X"]:
        assert "ORD202600125" in reference_keys(desc)


def test_name_similarity_discriminates_shared_family_names():
    assert name_similarity_text("NGUYEN VAN AN", "NGUYEN VAN AN") == 100
    assert name_similarity_text("NGUYEN VAN AN", "NGUYEN VAN BINH") < 75
    assert name_similarity_text("NGUYEN VAN AN", "NGUYEN VAN ANH") < 75  # different given name
    assert name_similarity_text("NGUYEN VAN AN", "NGUYENVANAN") == 100
    assert 70 <= name_similarity_text("NGUYEN VAN AN", "NGUYEN AN") < 90   # partial name: review, not auto


def test_name_parts_that_look_like_bank_words_are_kept():
    # Regression: THANH / TIEN / HANG / HOA are Vietnamese names as well as bank words.
    from reconcileai.text import name_tokens

    assert name_tokens("IBFT LE THANH NAM TT hoa don") == "LE THANH NAM HOA"
    assert name_similarity_text("LE THANH NAM", name_tokens("IBFT LE THANH NAM TT hoa don")) == 100
    assert name_similarity_text("TRAN THI HANG", name_tokens("TRAN THI HANG CK ORD2026-00001")) == 100
