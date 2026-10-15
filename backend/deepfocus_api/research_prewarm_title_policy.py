"""Pure title policy: skip only known company names followed by a full date.

The caller supplies a normalized exact-name set for the markets it wants to
exclude.  This module never guesses a ticker/market and never accesses a name
resolver, disk, cache, or network. Build the set once per scheduling cycle.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
import re
import unicodedata

_DOCUMENT_SUFFIX = re.compile(
    r"\.(?:pdf|docx?|pptx?|txt|md|markdown|png|jpe?g|webp|gif)$", re.IGNORECASE,
)
# Company and date can have a typography separator, but no report-topic words.
_COMPANY_DATE = re.compile(
    r"(?P<name>.+?)[_\-:：·—–]*"
    r"(?P<date>20\d{6}|20\d{2}(?P<sep>[-/.])\d{1,2}(?P=sep)\d{1,2}|"
    r"20\d{2}年\d{1,2}月\d{1,2}日)"
)
_DATE_ONLY = re.compile(
    r"20\d{6}|20\d{2}(?P<sep>[-/.])\d{1,2}(?P=sep)\d{1,2}|20\d{2}年\d{1,2}月\d{1,2}日"
)
_DATE_PARTS = re.compile(r"\d+")
_DATE_WRAPPERS = (("(", ")"), ("[", "]"), ("【", "】"))


def normalize_company_name(value: str) -> str:
    """Normalize compatibility/fullwidth forms, case, and typography spaces."""
    text = unicodedata.normalize("NFKC", value or "")
    return "".join(text.split()).casefold()


def normalize_company_names(names: Iterable[str]) -> frozenset[str]:
    """Build the exact membership set once; market scope belongs to caller."""
    return frozenset(name for raw in names if (name := normalize_company_name(raw)))


def is_bare_company_date_title(title: str, company_names: frozenset[str]) -> bool:
    """True only for an entire exact known name + a valid full calendar date.

    `company_names` must come from normalize_company_names. This deliberately
    avoids partial/fuzzy matching: "宁德时代盈利展望20261013" stays eligible.
    Company names unknown to the supplied authoritative snapshot stay eligible.
    """
    text = unicodedata.normalize("NFKC", title or "").strip()
    text = _DOCUMENT_SUFFIX.sub("", text).strip()
    text = normalize_company_name(text)
    # Accept a wrapped date only when both the opening and closing delimiters
    # are present. Removing arbitrary brackets would accept malformed titles.
    for left, right in _DATE_WRAPPERS:
        if text.endswith(right):
            opening = text.rfind(left)
            if opening >= 0 and _DATE_ONLY.fullmatch(text[opening + len(left):-len(right)]):
                text = text[:opening] + text[opening + len(left):-len(right)]
            break
    match = _COMPANY_DATE.fullmatch(text)
    if not match or match.group("name") not in company_names:
        return False
    raw_date = match.group("date")
    if raw_date.isdigit():
        year, month, day = int(raw_date[:4]), int(raw_date[4:6]), int(raw_date[6:])
    else:
        year, month, day = map(int, _DATE_PARTS.findall(raw_date))
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True
