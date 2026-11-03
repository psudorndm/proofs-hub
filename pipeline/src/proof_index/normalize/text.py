"""Text normalization primitives shared by parsers, classification, and identity.

Three distinct forms exist; do not mix them:

* ``clean_text`` — display form: NFKC, dashes unified, control characters removed,
  whitespace collapsed. Case and punctuation are preserved.
* ``match_text`` — classification form: ``clean_text`` (case kept). Callers that
  need case-insensitive matching use ``str.casefold`` on top or a case-insensitive
  regex. Offsets reported by the classifier refer to this normalized string.
* ``title_key`` — identity form: case-folded, punctuation normalized, tokens kept.
"""

from __future__ import annotations

import re
import unicodedata

# Unicode dash punctuation (Pd) plus minus sign variants -> ASCII hyphen-minus.
_DASHES = re.compile(r"[‐‑‒–—―−⸺⸻﹘﹣－]")
_WS = re.compile(r"\s+")
# Control characters except whitespace we will collapse anyway.
_CONTROL = re.compile(r"[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F​⁠﻿]")
_SOFT_HYPHEN = "­"


def clean_text(value: str | None) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", value)
    text = text.replace(_SOFT_HYPHEN, "")
    text = _CONTROL.sub("", text)
    text = _DASHES.sub("-", text)
    text = _WS.sub(" ", text)
    return text.strip()


def clean_optional(value: str | None) -> str | None:
    """``clean_text`` that maps empty results to ``None``."""
    text = clean_text(value)
    return text or None


def match_text(value: str | None) -> str:
    return clean_text(value)


# Characters treated as separators in identity keys. Mathematical symbols such as
# +, *, =, <, >, ^, |, and digits are kept because they can distinguish titles.
_TITLE_SEPARATORS = re.compile(r"[\s\-_,;:.!?'\"`()\[\]{}/\\«»“”‘’]+")


def title_tokens(value: str | None) -> list[str]:
    text = clean_text(value).casefold()
    return [tok for tok in _TITLE_SEPARATORS.split(text) if tok]


def title_key(value: str | None) -> str:
    """Normalized title used for exact-title identity candidates.

    This is not a proof of identity: it only proposes candidates.
    """
    return " ".join(title_tokens(value))


def meaningful_title_tokens(value: str | None) -> list[str]:
    """Title tokens that carry content (excludes a small stop-word list)."""
    return [tok for tok in title_tokens(value) if tok not in _STOP_WORDS]


_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
        "via",
    }
)
