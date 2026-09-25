"""The brute-force backlink scan, and the escaping it depends on.

``find_backlinks`` is the fallback ``get-backlinks`` drops to when the native
``getPageLinkedReferences`` fails. It builds a regex out of the page name, so a
name carrying regex metacharacters — ``C++``, ``What is this?``,
``Report (2025)`` — compiles into a pattern that no longer matches the link it
was built for. The command then reports no backlinks and exits 0.

That is the failure mode this project has had before: output that shows less
than exists, with nothing to indicate it. Worse here, because the path only
runs when something has already gone wrong.

``escape_regex`` is one line, ``return re.escape(s)``, and dropping the escaping
left all 842 tests green. The assertion is not the line — it is that a page name
is matched literally, whatever characters it contains.
"""

import json
import re
from unittest.mock import MagicMock, patch

import pytest
import requests

from logseq_cli.cli import cli
from logseq_cli.lookup import escape_regex, find_backlinks
from logseq_cli.pagenames import PageRef
from tests.conftest import split_runner


# Names that are legal Logseq page titles and also regex syntax. Each one is a
# different way the unescaped pattern goes wrong: a quantifier with nothing to
# repeat, an optional character, a group, an alternation, a wildcard.
# One name per way an unescaped pattern breaks. Measured, not guessed: each of
# these finds nothing without escaping, and each fails for a different reason.
# Names whose metacharacters happen to still match ("a|b", "Notes.") are not
# listed here -- widening is a separate claim, asserted once below.
METACHARACTER_NAMES = [
    "C++",             # + quantifies the character before it
    "What is this?",   # ? makes it optional
    "Report (2025)",   # () opens a group
    "Budget [2025]",   # [] opens a character class
    "foo*bar",         # * quantifies
]


def _api(pages):
    """pages: {page name: page text}. get_page_blocks_tree answers one block."""
    api = MagicMock()
    api.get_all_pages.return_value = [{"originalName": n} for n in pages]

    def _tree(name):
        text = pages.get(name)
        return [{"content": text, "uuid": f"u-{name}", "children": []}] if text else []

    api.get_page_blocks_tree.side_effect = _tree
    return api


class TestEscaping:
    """The one line, stated as what it guarantees rather than what it calls."""

    @pytest.mark.parametrize("name", METACHARACTER_NAMES)
    def test_escaped_name_matches_itself_literally(self, name):
        pattern = re.compile(escape_regex(name))
        assert pattern.search(f"see [[{name}]] here"), name

    @pytest.mark.parametrize("name", METACHARACTER_NAMES)
    def test_escaped_name_is_a_valid_pattern(self, name):
        """An unescaped ``C++`` raises; the caller has no try/except for that."""
        re.compile(escape_regex(name))

    def test_escaping_does_not_widen_the_match(self):
        """``a|b`` must not match a page containing only ``b``."""
        pattern = re.compile(escape_regex("a|b"))
        assert not pattern.search("see [[b]] here")
        assert pattern.search("see [[a|b]] here")


class TestScanFindsNamesWithMetacharacters:
    """The function, driven the way the command drives it."""

    @pytest.mark.parametrize("name", METACHARACTER_NAMES)
    def test_linking_page_is_found(self, name):
        api = _api({name: "the target page",
                    "Linking Page": f"mentions [[{name}]] in passing",
                    "Unrelated": "no links here"})
        assert find_backlinks(api, name) == ["Linking Page"]

    def test_does_not_match_a_different_page(self):
        """Without escaping, ``a|b`` would report a page linking only ``b``."""
        api = _api({"a|b": "target",
                    "False Friend": "links [[b]] only"})
        assert find_backlinks(api, "a|b") == []

    def test_self_reference_is_excluded(self):
        api = _api({"C++": "a page that mentions [[C++]] itself",
                    "Other": "also mentions [[C++]]"})
        assert find_backlinks(api, "C++") == ["Other"]

    def test_match_is_case_insensitive_and_tolerates_inner_spaces(self):
        api = _api({"C++": "target", "L": "see [[  c++  ]] there"})
        assert find_backlinks(api, "C++") == ["L"]


class TestFallbackPathReachesTheScan:
    """get-backlinks falls back to the scan when the native API fails.

    Driven through the CLI, because the escaping only matters on the path that
    runs when something else already broke.
    """

    @pytest.mark.parametrize("name", ["C++", "Report (2025)"])
    def test_backlinks_reported_after_native_api_fails(self, name):
        api = _api({name: "target",
                    "Linking Page": f"mentions [[{name}]]"})
        api.get_page_linked_references.side_effect = RuntimeError("unexpected format")
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            result = split_runner().invoke(
                cli, ["get-backlinks", "--page", name, "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        entry = payload[0] if isinstance(payload, list) else payload
        assert "Linking Page" in json.dumps(entry), result.stdout


class TestBothWaysFailing:
    """When the native request and the scan both fail, that is not "no backlinks".

    The fallback caught the scan's exception and returned an empty list, so the
    command printed "No backlinks found" with exit 0 for a page it never read
    (#93). The page is now named, the other pages are still answered, and the
    call fails after them.
    """

    def _api(self):
        api = MagicMock()
        api.get_page.side_effect = lambda n: {"name": n.lower(), "originalName": n}

        def native(name):
            if name == "Broken":
                raise RuntimeError("HTTP 500")
            return [[{"originalName": "Linking Page"}, [{"content": "see [[Fine]]"}]]]

        api.get_page_linked_references.side_effect = native
        api.get_all_pages.side_effect = RuntimeError("connection dropped")
        return api

    def test_the_page_is_named_and_the_call_fails(self):
        with patch("logseq_cli.group.LogseqAPI", return_value=self._api()):
            result = split_runner().invoke(
                cli, ["get-backlinks", "--page", "Fine", "--page", "Broken", "--json"])
        assert result.exit_code != 0
        payload = {e["page"]: e for e in json.loads(result.stdout)}
        assert payload["Fine"]["count"] == 1
        assert "backlinks_error" in payload["Broken"]
        # Under --json stderr holds the error object and nothing in front.
        error = json.loads(result.stderr)
        assert error["reason"] == "partial_read"
        assert error["backlinks_unread"] == ["Broken"]

    def test_text_mode_says_it_could_not_read(self):
        with patch("logseq_cli.group.LogseqAPI", return_value=self._api()):
            result = split_runner().invoke(cli, ["get-backlinks", "--page", "Broken"])
        assert result.exit_code != 0
        assert "No backlinks found" not in result.stdout
        assert "could not read the backlinks of 'Broken'" in result.stdout


    def test_an_alias_is_named_as_asked(self):
        """The failure is recorded under the name the caller gave, which is the
        name the results carry; keyed by the resolved page it went unnoticed and
        the alias read "No backlinks found" again."""
        api = self._api()
        with patch("logseq_cli.group.LogseqAPI", return_value=api), \
             patch("logseq_cli.commands.pages.follow_pages",
                   return_value=([PageRef("Old Name", "Broken", True)], {})):
            result = split_runner().invoke(cli, ["get-backlinks", "--page", "Old Name"])
        assert result.exit_code != 0
        assert "No backlinks found" not in result.stdout
        assert "could not read the backlinks of 'Old Name'" in result.stdout


    def test_a_dropped_connection_still_answers_the_other_pages(self):
        """The scan after a connection error was not guarded, so the whole
        batch was lost to the handler, the pages already answered with it."""
        api = self._api()

        def native(name):
            if name == "Broken":
                raise requests.ConnectionError("connection dropped")
            return [[{"originalName": "Linking Page"}, [{"content": "see [[Fine]]"}]]]

        api.get_page_linked_references.side_effect = native
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            result = split_runner().invoke(
                cli, ["get-backlinks", "--page", "Fine", "--page", "Broken", "--json"])
        assert result.exit_code != 0
        payload = {e["page"]: e for e in json.loads(result.stdout)}
        assert payload["Fine"]["count"] == 1
        assert json.loads(result.stderr)["backlinks_unread"] == ["Broken"]


    def test_a_hanging_logseq_is_not_waited_for_once_per_page(self):
        """A timeout ends the call: every further page would wait as long."""
        api = self._api()
        api.get_page_linked_references.side_effect = requests.ReadTimeout("timed out")
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            result = split_runner().invoke(
                cli, ["get-backlinks", "--page", "Fine", "--page", "Broken", "--json"])
        assert result.exit_code != 0
        assert json.loads(result.stderr)["reason"] == "timeout"
        assert api.get_page_linked_references.call_count == 1
        api.get_all_pages.assert_not_called()
