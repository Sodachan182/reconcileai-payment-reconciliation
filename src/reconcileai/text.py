"""Deterministic text normalization for Vietnamese and English payment data.

The original text is always kept by callers; these helpers only produce a
*comparison* form. Diacritic removal is used for candidate generation and
similarity scoring, never to rewrite source evidence.
"""

from __future__ import annotations

import re
import unicodedata

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")

# Bank-description words that are never parts of Vietnamese personal names, compared without
# diacritics. Words such as THANH, TOAN, TIEN, HANG, NHAN, TU or HOA are deliberately NOT listed:
# they are also common names (Thanh, Toàn, Tiến, Hằng, Nhân, Tú, Hòa) and must stay as evidence.
BANK_NOISE_TOKENS = frozenset(
    {
        "CK", "CHUYEN", "KHOAN", "TT", "GD", "MBVCB", "IBFT", "FT", "TRANSFER", "PAYMENT", "PAY",
        "FOR", "TO", "FROM", "REF", "NAPAS", "DH", "ORDER", "TRF", "INB", "BIDV", "VCB", "TCB",
        "ACB", "NOP", "DON",
    }
)


def strip_diacritics(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return text.replace("đ", "d").replace("Đ", "D")


def normalize_text(text: object) -> str:
    """Upper-case, NFC, diacritic-free, punctuation collapsed to spaces."""
    if text is None:
        return ""
    s = unicodedata.normalize("NFKC", str(text))
    s = strip_diacritics(s).upper()
    s = _PUNCT.sub(" ", s).replace("_", " ")
    return _SPACES.sub(" ", s).strip()


def normalize_header(text: object) -> str:
    return normalize_text(text).lower()


def compact(text: object) -> str:
    """Alphanumeric-only upper-case key, e.g. ``'ord2026-00125' -> 'ORD202600125'``."""
    return re.sub(r"[^A-Z0-9]", "", normalize_text(text))


def tokens(text: object) -> list[str]:
    return normalize_text(text).split()


def name_tokens(description: str) -> str:
    """Description without noise words and pure-number tokens — used for name similarity."""
    kept = [
        t for t in tokens(description)
        if t not in BANK_NOISE_TOKENS and not any(ch.isdigit() for ch in t)
    ]
    return " ".join(kept)


def reference_keys(text: str, max_join: int = 3) -> set[str]:
    """Candidate compact reference keys found in free text.

    Produces each token's compact form plus joins of up to ``max_join`` adjacent
    tokens, so that ``"ORD2026 00125"`` and ``"ORD-2026-00125"`` both yield
    ``"ORD202600125"``. Only keys containing at least one digit are returned.
    """
    toks = tokens(text)
    keys: set[str] = set()
    for i in range(len(toks)):
        joined = ""
        for j in range(i, min(i + max_join, len(toks))):
            joined += toks[j]
            if any(ch.isdigit() for ch in joined) and len(joined) >= 4:
                keys.add(joined)
    return keys
