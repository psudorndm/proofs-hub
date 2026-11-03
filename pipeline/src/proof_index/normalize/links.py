"""External link safety checks (spec section 5.2).

Only absolute HTTP(S) URLs with a host and no embedded credentials are allowed.
No URL is fetched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

MAX_URL_LENGTH = 2048
_FORBIDDEN_CHARS = re.compile(r"[\s\x00-\x1f\x7f<>\"\\^`{|}]")


@dataclass(frozen=True)
class LinkCheck:
    url: str | None
    issue: str | None  # issue code when rejected


def check_url(raw: str | None) -> LinkCheck:
    if raw is None:
        return LinkCheck(None, "url_missing")
    url = raw.strip()
    if not url:
        return LinkCheck(None, "url_missing")
    if len(url) > MAX_URL_LENGTH:
        return LinkCheck(None, "url_too_long")
    if _FORBIDDEN_CHARS.search(url):
        return LinkCheck(None, "url_invalid_characters")
    try:
        parts = urlsplit(url)
    except ValueError:
        return LinkCheck(None, "url_unparseable")
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        return LinkCheck(None, "url_scheme_not_allowed")
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        return LinkCheck(None, "url_embedded_credentials")
    try:
        host = parts.hostname
        _ = parts.port
    except ValueError:
        return LinkCheck(None, "url_unparseable")
    if not host or "." not in host and host != "localhost":
        return LinkCheck(None, "url_missing_host")
    # Re-assemble with a lower-case scheme/host; path and query are kept as given.
    netloc = parts.netloc.lower()
    rebuilt = f"{scheme}://{netloc}{parts.path}"
    if parts.query:
        rebuilt += f"?{parts.query}"
    if parts.fragment:
        rebuilt += f"#{parts.fragment}"
    return LinkCheck(rebuilt, None)


def safe_url(raw: str | None) -> str | None:
    return check_url(raw).url
