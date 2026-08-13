import pytest

from magazinerr.parser import normalize, parse

# (release_title, expected_identifier, expected_kind, title_substring_expected)
DATE_AND_ISSUE_CASES = [
    ("Wired USA - August 2026.pdf", "2026-08", "date", "wired usa"),
    ("National.Geographic.2026.08.PDF", "2026-08", "date", "national geographic"),
    ("The_Economist_08_2026.pdf", "2026-08", "date", "the economist"),
    ("Sports Illustrated - Issue #245.cbr", "issue-245", "issue", "sports illustrated"),
    ("MAD Magazine No. 620.pdf", "issue-620", "issue", "mad magazine"),
    ("Time Magazine - Iss 12.epub", "issue-12", "issue", "time magazine"),
    # Real examples pulled from a live Prowlarr search for "le monde" — the parser
    # was English-month-only and month-level-only before this, and silently
    # failed on ~80% of real French daily-newspaper releases.
    ("Le.Monde.Quotidien.N.25213.22.Janvier.2026.FRENCH.[PDF]-FLYNNYBABE", "2026-01-22", "date", "le monde"),
    ("Le.Monde.Du.02.07.2026.FR.[PDF]-NOTAG", "2026-07-02", "date", "le monde"),
    ("Le.Monde.N.25342.Du.23.Juin.2026.FR.[PDF]-G11", "2026-06-23", "date", "le monde"),
    ("Le.Monde.Magazine.Du.7.Mars.2026.FR.[PDF]-G11", "2026-03-07", "date", "le monde"),
    ("Le Monde Diplomatique – Février 2026.pdf", "2026-02", "date", "le monde"),
    ("The New York Times - March 17, 2026.pdf", "2026-03-17", "date", "the new york times"),
    # French sub-issue numbers ("N.90", "N.91") directly followed by a month name
    # used to be misread as an invalid day-of-month ("2026-05-90"); a small issue
    # number can otherwise fall inside the valid 1-31 day range, so it must be
    # excluded specifically, not just range-checked.
    ("Science.et.Vie.Guerres.et.Histoire.N.90.Mai.2026.FR.[PDF]-NOTAG", "2026-05", "date", "science et vie"),
    ("Science.et.Vie.Guerres.et.Histoire.N.91.Juillet.2026.FR.[PDF]-G11", "2026-07", "date", "science et vie"),
    # French ordinal "1er" (1st) for the first of the month — every other day in
    # the same batch parsed with day granularity ("2026-04-02", etc); "1er" alone
    # was falling back to month-only ("2026-04"), inconsistent with its siblings.
    ("Ouest.France.Du.1er.Avril.2026.FR.[PDF]-G11", "2026-04-01", "date", "ouest france"),
]


@pytest.mark.parametrize("title,expected_id,expected_kind,title_substr", DATE_AND_ISSUE_CASES)
def test_parse_extracts_identifier(title, expected_id, expected_kind, title_substr):
    result = parse(title)
    assert result.identifier == expected_id
    assert result.identifier_kind == expected_kind
    assert title_substr in result.title_guess.lower()


def test_parse_returns_none_identifier_when_unparseable():
    result = parse("Some Cool Zine Vol 3.pdf")
    assert result.identifier is None
    assert result.identifier_kind is None
    assert result.title_guess  # still returns something usable for fuzzy matching


def test_parse_extracts_format_extension():
    assert parse("Wired USA - August 2026.pdf").format_ext == "pdf"
    assert parse("Some.Release.CBR-GROUP").format_ext == "cbr"
    assert parse("No extension here").format_ext is None


def test_normalize_handles_ampersand_and_unicode_punctuation():
    # en-dash (–) and curly quotes/ampersand as seen in real release names
    raw = "Popular Science – Q&A Weekly’s Edition.pdf"
    result = normalize(raw)
    assert "&" not in result
    assert "–" not in result
    assert "and" in result.lower()


def test_parse_is_resilient_to_bracketed_group_tags():
    result = parse("Wired USA - August 2026 [SomeGroup].pdf")
    assert result.identifier == "2026-08"
    assert "somegroup" not in result.title_guess.lower()


def test_daily_newspaper_editions_within_same_month_get_distinct_identifiers():
    # Regression guard: without day-level granularity, every issue of a daily
    # newspaper published in the same month would collide on one identifier
    # and only the first would ever be imported — the rest silently "already owned".
    day_23 = parse("Le.Monde.N.25342.Du.23.Juin.2026.FR.[PDF]-G11")
    day_24 = parse("Le.Monde.Du.24.Juin.2026.FR.[PDF]-G11")
    assert day_23.identifier != day_24.identifier
    assert day_23.identifier == "2026-06-23"
    assert day_24.identifier == "2026-06-24"


def test_bare_french_issue_marker_without_date():
    result = parse("Le Monde Diplomatique N125.pdf")
    assert result.identifier == "issue-125"
    assert result.identifier_kind == "issue"
