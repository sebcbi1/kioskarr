"""Parse torrent/indexer release titles into a normalized title + issue identifier.

There is no canonical metadata source for magazine issues (see plan), so this is
necessarily heuristic: regex-based extraction of a date or issue number, tried in
order of reliability (explicit month name > numeric date > issue number).
"""

import re
from dataclasses import dataclass

MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
# Longest-first so alternation prefers "september" over the "sep" prefix match.
_MONTH_ALTERNATION = "|".join(sorted(MONTHS.keys(), key=len, reverse=True))

FORMAT_EXTENSIONS = {"pdf", "cbr", "cbz", "epub", "mobi"}

_UNICODE_DASHES = "‐‑‒–—―−"
_UNICODE_QUOTES = "‘’“”"

_MONTH_YEAR_RE = re.compile(
    rf"\b(?P<month>{_MONTH_ALTERNATION})\.?\s+(?P<year>(?:19|20)\d{{2}})\b", re.IGNORECASE
)
_YEAR_MONTH_NAME_RE = re.compile(
    rf"\b(?P<year>(?:19|20)\d{{2}})\s+(?P<month>{_MONTH_ALTERNATION})\b", re.IGNORECASE
)
_NUMERIC_YM_RE = re.compile(r"\b(?P<year>(?:19|20)\d{2})[\s.-]?(?P<month>0[1-9]|1[0-2])\b")
_NUMERIC_MY_RE = re.compile(r"\b(?P<month>0[1-9]|1[0-2])[\s.-](?P<year>(?:19|20)\d{2})\b")
_ISSUE_RE = re.compile(
    r"\b(?:issue|iss)\.?\s*#?\s*(?P<num>\d{1,4})\b"
    r"|#(?P<num2>\d{1,4})\b"
    r"|\bno\.?\s*(?P<num3>\d{1,4})\b",
    re.IGNORECASE,
)


@dataclass
class ParsedRelease:
    raw: str
    normalized: str
    title_guess: str
    identifier: str | None
    identifier_kind: str | None  # "date" | "issue" | None
    format_ext: str | None


def _strip_unicode_punct(text: str) -> str:
    for ch in _UNICODE_DASHES:
        text = text.replace(ch, "-")
    for ch in _UNICODE_QUOTES:
        text = text.replace(ch, "'")
    return text


def _strip_known_extension(text: str) -> str:
    match = re.search(r"\.([A-Za-z0-9]{2,5})$", text)
    if match and match.group(1).lower() in FORMAT_EXTENSIONS:
        return text[: match.start()]
    return text


def normalize(text: str) -> str:
    text = _strip_unicode_punct(text)
    text = text.replace("&", " and ")
    text = _strip_known_extension(text)
    text = re.sub(r"[\[\(][^\]\)]*[\]\)]", " ", text)  # drop [group]/(extra) tags
    text = re.sub(r"[._-]+", " ", text)  # all separators collapse to a single space
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_format(raw_title: str) -> str | None:
    lowered = raw_title.lower()
    for ext in FORMAT_EXTENSIONS:
        if re.search(rf"\b{ext}\b", lowered):
            return ext
    return None


def parse(release_title: str) -> ParsedRelease:
    normalized = normalize(release_title)
    format_ext = _extract_format(release_title)

    for pattern, month_from_name in (
        (_MONTH_YEAR_RE, True),
        (_YEAR_MONTH_NAME_RE, True),
        (_NUMERIC_YM_RE, False),
        (_NUMERIC_MY_RE, False),
    ):
        match = pattern.search(normalized)
        if not match:
            continue
        year = int(match.group("year"))
        month_raw = match.group("month")
        month = MONTHS[month_raw.lower()] if month_from_name else int(month_raw)
        identifier = f"{year:04d}-{month:02d}"
        title_guess = normalized[: match.start()].strip(" -")
        if not title_guess:
            title_guess = normalized[match.end():].strip(" -")
        return ParsedRelease(release_title, normalized, title_guess or normalized, identifier, "date", format_ext)

    match = _ISSUE_RE.search(normalized)
    if match:
        num = match.group("num") or match.group("num2") or match.group("num3")
        identifier = f"issue-{int(num)}"
        title_guess = normalized[: match.start()].strip(" -#")
        if not title_guess:
            title_guess = normalized[match.end():].strip(" -#")
        return ParsedRelease(release_title, normalized, title_guess or normalized, identifier, "issue", format_ext)

    return ParsedRelease(release_title, normalized, normalized, None, None, format_ext)
