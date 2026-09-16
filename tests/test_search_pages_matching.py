"""Tests for what search-pages matches on.

Both properties are load-bearing and were previously unguarded: the command
matches case-insensitively and looks at BOTH name fields. A move to a datalog
query would break either one silently — `clojure.string/includes?` is
case-sensitive, and `:block/name` alone misses pages whose casing only shows in
`:block/original-name`. See the comment above `search_pages`.
"""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from logseq_cli.cli import cli

PAGES = [
    # Logseq stores `name` lowercased and keeps the written form in
    # `originalName`; the fixture mirrors that.
    {"originalName": "Project Alpha", "name": "project alpha"},
    {"originalName": "ACME Notes", "name": "acme notes"},
    {"name": "legacy page"},  # older entries can lack originalName entirely
    # Logseq's `name` is a slug: namespace separators and punctuation survive
    # in `originalName` only. A match on "Q&A" therefore exists in one field
    # and not the other, which is what makes the two-field lookup load-bearing.
    {"originalName": "Q&A / Support", "name": "qa support"},
]


def _run(query):
    api = MagicMock()
    api.get_all_pages.return_value = PAGES
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        return CliRunner().invoke(cli, ["--token", "T", "search-pages", "--query", query])


class TestCaseInsensitivity:
    def test_lowercase_query_finds_capitalised_page(self):
        assert "Project Alpha" in _run("project alpha").output

    def test_uppercase_query_finds_it_too(self):
        assert "Project Alpha" in _run("PROJECT").output

    def test_mixed_case_query_matches(self):
        assert "ACME Notes" in _run("aCme").output


class TestBothNameFields:
    def test_matches_a_page_only_findable_through_original_name(self):
        # "Q&A" appears in originalName only - the slugged `name` field drops
        # the ampersand. Searching just :block/name would miss this page.
        assert "Q&A / Support" in _run("Q&A").output

    def test_matches_a_page_that_has_only_name(self):
        assert "legacy page" in _run("legacy").output

    def test_a_miss_reports_nothing_found(self):
        result = _run("nothing-like-this")
        assert "No pages found" in result.output
        assert result.exit_code == 0
