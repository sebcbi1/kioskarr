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
