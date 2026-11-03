"""Author normalization (spec section 5.2).

Author order is preserved. A display string is never split into given/family
parts when the provider did not supply structured names.
"""

from __future__ import annotations

import re

from proof_index.normalize.text import clean_text

_ORCID = re.compile(r"^(\d{4})-?(\d{4})-?(\d{4})-?(\d{3}[\dX])$")


def normalize_orcid(value: str | None) -> str | None:
    """Return ``0000-0002-1825-0097`` form when the checksum is valid, else ``None``."""
    if value is None:
        return None
    text = value.strip()
    for prefix in ("https://orcid.org/", "http://orcid.org/", "orcid.org/", "orcid:"):
        if text.lower().startswith(prefix):
            text = text[len(prefix) :]
    match = _ORCID.match(text.upper())
    if not match:
        return None
    digits = "".join(match.groups())
    total = 0
    for ch in digits[:-1]:
        total = (total + int(ch)) * 2
    check = (12 - total % 11) % 11
    expected = "X" if check == 10 else str(check)
    if digits[-1] != expected:
        return None
    return "-".join(match.groups())


def author_key(display_name: str) -> str:
    """Comparison form used by identity matching: NFKC, case-folded, whitespace collapsed."""
    return clean_text(display_name).casefold()


def author_keys(names: list[str]) -> list[str]:
    return [author_key(n) for n in names]
