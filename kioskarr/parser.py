"""Parse torrent/indexer release titles into a normalized title + issue identifier.

There is no canonical metadata source for magazine issues (see plan), so this is
necessarily heuristic: regex-based extraction of a date or issue number, tried in
order of specificity (day-level date > month-level date > issue number).

Day-level granularity matters even for magazines, but it's load-bearing for daily
newspapers: without a day component, every issue published within the same month
would normalize to the same identifier and get skipped as a duplicate after the
first grab.
"""

import re
import unicodedata
from dataclasses import dataclass

MONTHS = {
    "jan": 1, "january": 1, "janvier": 1,
    "feb": 2, "february": 2, "fevrier": 2, "fev": 2,
    "mar": 3, "march": 3, "mars": 3,
    "apr": 4, "april": 4, "avril": 4, "avr": 4,
    "may": 5, "mai": 5,
    "jun": 6, "june": 6, "juin": 6,
    "jul": 7, "july": 7, "juillet": 7, "juil": 7,
    "aug": 8, "august": 8, "aout": 8,
    "sep": 9, "sept": 9, "september": 9, "septembre": 9,
    "oct": 10, "october": 10, "octobre": 10,
    "nov": 11, "november": 11, "novembre": 11,
    "dec": 12, "december": 12, "decembre": 12,
}
# Longest-first so alternation prefers "september" over the "sep" prefix match.
_MONTH_ALTERNATION = "|".join(sorted(MONTHS.keys(), key=len, reverse=True))

FORMAT_EXTENSIONS = {"pdf", "cbr", "cbz", "epub", "mobi"}

_UNICODE_DASHES = "‐‑‒–—―−"
_UNICODE_QUOTES = "‘’“”"


def _month_num(name: str) -> int:
    return MONTHS[name.lower()]


# Valid day-of-month, 1-31, optionally zero-padded.
_DAY_RE = r"(?:3[01]|[12]\d|0?[1-9])"

# Tried in order; each extractor returns (year, month, day_or_None) from a match.
_DATE_PATTERNS = [
    # numeric day.month.year, e.g. "02.07.2026" — assumes DD.MM.YYYY (European
    # convention); genuinely ambiguous vs. MM.DD.YYYY when day <= 12, accepted
    # as a known heuristic limitation.
    (
        re.compile(r"\b(?P<day>0[1-9]|[12]\d|3[01])[\s.-](?P<month>0[1-9]|1[0-2])[\s.-](?P<year>(?:19|20)\d{2})\b"),
        lambda m: (int(m["year"]), int(m["month"]), int(m["day"])),
    ),
    # day + month name + year, e.g. "17 Mars 2026" / "17 March 2026" / "1er Avril 2026".
    # Excludes a number directly preceded by a bare "N"/"N°" token — that's the
    # French issue-number marker (e.g. "N.90.Mai.2026"), not a day, and a small
    # issue number can otherwise land inside the valid 1-31 day range.
    (
        re.compile(
            rf"(?<![Nn]\s)\b(?P<day>{_DAY_RE})(?:st|nd|rd|th|er)?\s+(?P<month>{_MONTH_ALTERNATION})\.?\s+(?P<year>(?:19|20)\d{{2}})\b",
            re.IGNORECASE,
        ),
        lambda m: (int(m["year"]), _month_num(m["month"]), int(m["day"])),
    ),
    # month name + day + year, e.g. "March 17, 2026" / "March 17th 2026"
    (
        re.compile(
            rf"\b(?P<month>{_MONTH_ALTERNATION})\.?\s+(?P<day>{_DAY_RE})(?:st|nd|rd|th)?,?\s+(?P<year>(?:19|20)\d{{2}})\b",
            re.IGNORECASE,
        ),
        lambda m: (int(m["year"]), _month_num(m["month"]), int(m["day"])),
    ),
    # month name + year, no day, e.g. "August 2026"
    (
        re.compile(rf"\b(?P<month>{_MONTH_ALTERNATION})\.?\s+(?P<year>(?:19|20)\d{{2}})\b", re.IGNORECASE),
        lambda m: (int(m["year"]), _month_num(m["month"]), None),
    ),
    # year + month name, no day, e.g. "2026 August"
    (
        re.compile(rf"\b(?P<year>(?:19|20)\d{{2}})\s+(?P<month>{_MONTH_ALTERNATION})\b", re.IGNORECASE),
        lambda m: (int(m["year"]), _month_num(m["month"]), None),
    ),
    # numeric year-month, no day, e.g. "2026 08" / "2026.08" / "202608"
    (
        re.compile(r"\b(?P<year>(?:19|20)\d{2})[\s.-]?(?P<month>0[1-9]|1[0-2])\b"),
        lambda m: (int(m["year"]), int(m["month"]), None),
    ),
    # numeric month-year, no day, e.g. "08 2026" / "08.2026"
    (
        re.compile(r"\b(?P<month>0[1-9]|1[0-2])[\s.-](?P<year>(?:19|20)\d{2})\b"),
        lambda m: (int(m["year"]), int(m["month"]), None),
    ),
]

_ISSUE_RE = re.compile(
    r"\b(?:issue|iss)\.?\s*#?\s*(?P<num>\d{1,4})\b"
    r"|#(?P<num2>\d{1,4})\b"
    r"|\bno\.?\s*(?P<num3>\d{1,4})\b"
    r"|\bn\N{DEGREE SIGN}?\.?\s*(?P<num4>\d{2,6})\b",  # French "N°"/"N." issue marker
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


def _strip_diacritics(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _strip_known_extension(text: str) -> str:
    match = re.search(r"\.([A-Za-z0-9]{2,5})$", text)
    if match and match.group(1).lower() in FORMAT_EXTENSIONS:
        return text[: match.start()]
    return text


def normalize(text: str) -> str:
    text = _strip_unicode_punct(text)
    text = _strip_diacritics(text)
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


def _strip_issue_marker(text: str) -> str:
    """Drop an issue-number marker ("N.25342", "#245"...) from a title guess.

    Many French periodicals carry both an issue number AND a date in the same
    release name (e.g. "Le Monde N.25342 Du 23 Juin 2026"). The date pattern
    match only removes the date span, leaving "N 25342 Du" in title_guess —
    which is close enough to poison the fuzzy match against a clean
    publication title ("Le Monde" alone scored 59/100 against it, well under
    the default 75 confidence threshold).
    """
    stripped = _ISSUE_RE.sub(" ", text)
    stripped = re.sub(r"\s+", " ", stripped).strip(" -")
    return stripped or text


def parse(release_title: str) -> ParsedRelease:
    normalized = normalize(release_title)
    format_ext = _extract_format(release_title)

    for pattern, extract in _DATE_PATTERNS:
        match = pattern.search(normalized)
        if not match:
            continue
        year, month, day = extract(match)
        identifier = f"{year:04d}-{month:02d}-{day:02d}" if day else f"{year:04d}-{month:02d}"
        title_guess = normalized[: match.start()].strip(" -")
        if not title_guess:
            title_guess = normalized[match.end():].strip(" -")
        title_guess = _strip_issue_marker(title_guess)
        return ParsedRelease(release_title, normalized, title_guess or normalized, identifier, "date", format_ext)

    match = _ISSUE_RE.search(normalized)
    if match:
        num = match.group("num") or match.group("num2") or match.group("num3") or match.group("num4")
        identifier = f"issue-{int(num)}"
        title_guess = normalized[: match.start()].strip(" -#")
        if not title_guess:
            title_guess = normalized[match.end():].strip(" -#")
        return ParsedRelease(release_title, normalized, title_guess or normalized, identifier, "issue", format_ext)

    return ParsedRelease(release_title, normalized, normalized, None, None, format_ext)


def identifier_sort_key(identifier: str) -> tuple:
    """Comparable key for "is this issue newer than that one" — plain string
    comparison breaks for issue numbers ("issue-10" < "issue-9" lexicographically),
    though it works fine as-is for our zero-padded date identifiers.
    """
    if identifier.startswith("issue-"):
        return (1, int(identifier.removeprefix("issue-")))
    return (0, identifier)


def is_identifier_newer(identifier: str, baseline: str | None) -> bool:
    if baseline is None:
        return True
    return identifier_sort_key(identifier) > identifier_sort_key(baseline)
