"""A page that could not be read is not an empty page.

get-page-stats turned any error while reading backlinks into
``inbound_count: 0``; the fallback backlink scan skipped pages it could not
read and returned a short list; analyze-graph, find-knowledge-gaps,
analyze-journal-patterns and suggest-connections counted such a page as
empty. All of it with exit 0, so a connection that dropped halfway through a
scan produced wrong numbers that looked right (#93).

Reading every page of a real graph raised nothing (measured
2026-09-25), so an error there means the connection, not a kind of page to
skip. It now reaches the caller.
"""
import datetime
import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from logseq_cli.api import BadResponseError
from logseq_cli.cli import cli
from tests.conftest import split_runner


TODAY = datetime.date.today()
JOURNAL = TODAY.strftime("%Y-%m-%d")


def _api(fail_on):
    """Three pages; reading `fail_on` drops the connection."""
    pages = [{"name": "alpha", "originalName": "Alpha"},
             {"name": "beta", "originalName": "Beta"},
             {"name": JOURNAL, "originalName": JOURNAL, "journal?": True,
              "journalDay": int(TODAY.strftime("%Y%m%d"))}]
    api = MagicMock()
    api.get_all_pages.return_value = pages
    api.get_page.side_effect = lambda n: next(
        (p for p in pages if p["originalName"].lower() == str(n).lower()), None)

    def tree(name):
        if str(name).lower() == fail_on.lower():
            raise requests.ConnectionError("connection dropped")
        return [{"uuid": f"u-{name}", "content": "see [[Alpha]] #topic", "children": []}]

    api.get_page_blocks_tree.side_effect = tree
    api.get_page_linked_references.return_value = []
    api.datascript_query.return_value = []
    return api


def _run(api, *args):
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        return split_runner().invoke(cli, [*args, "--json"])


@pytest.mark.parametrize("command", [
    ["analyze-graph"],
    ["find-knowledge-gaps"],
    ["suggest-connections"],
])
def test_an_analysis_does_not_count_an_unreadable_page_as_empty(command):
    result = _run(_api("Beta"), *command)
    assert result.exit_code != 0, result.stdout
    assert json.loads(result.stderr)["reason"] == "connection_refused"


def test_journal_patterns_do_not_count_an_unreadable_day_as_empty():
    result = _run(_api(JOURNAL), "analyze-journal-patterns", "--timeframe", "last 7 days")
    assert result.exit_code != 0, result.stdout
    assert json.loads(result.stderr)["reason"] == "connection_refused"


@pytest.mark.parametrize("error,reason", [
    (requests.exceptions.ReadTimeout("read timed out"), "timeout"),
    (BadResponseError("Logseq API returned non-JSON response: <html>"), "bad_response"),
], ids=["timeout", "not-json"])
def test_a_scan_that_hits_a_timeout_or_a_broken_answer_fails_cleanly(error, reason):
    """These were tracebacks, rare while the scans swallowed read errors."""
    api = _api("Beta")

    def tree(name):
        if name == "Beta":
            raise error
        return [{"uuid": f"u-{name}", "content": "text", "children": []}]

    api.get_page_blocks_tree.side_effect = tree
    result = _run(api, "analyze-graph")
    assert result.exit_code != 0
    assert json.loads(result.stderr)["reason"] == reason


def test_a_ref_whose_lookup_fails_is_not_called_dead():
    """Only a lookup that answers null means the block is gone."""
    api = _api("Beta")
    api.get_page_blocks_tree.side_effect = lambda name: [
        {"uuid": "u1", "content": "see ((00000000-0000-4000-8000-0000000000aa))", "children": []}]
    api.get_block.side_effect = requests.ConnectionError("connection dropped")
    result = _run(api, "get-page", "--page", "Alpha", "--resolve-refs", "--no-backlinks")
    assert result.exit_code != 0
    assert "no longer exists" not in result.stderr


@pytest.mark.parametrize("args", [
    ["add-journal-block", "--date", "2026-01-05", "--content", "x"],
    ["add-journal-block", "--date", "2026-01-05", "--content", "x", "--content", "y"],
    ["add-journal-content", "--date", "2026-01-05", "--content", "x"],
    ["add-journal-entry", "--date", "2026-01-05", "--content", "x"],
], ids=["journal-block", "journal-block-batch", "journal-content", "journal-entry"])
def test_a_failed_existence_check_does_not_create_the_page(args):
    """A read that failed was taken for "no such page", and the page was
    created, when it may well have been there."""
    api = _api("Beta")
    api.get_page.side_effect = requests.ConnectionError("connection dropped")
    result = _run(api, *args)
    assert result.exit_code != 0
    api.create_page.assert_not_called()


def _resolved(name):
    """follow_page reads the page itself; patched so the read under test is
    the first one that can fail."""
    from logseq_cli.pagenames import PageRef
    return patch(f"logseq_cli.commands.{name}.follow_page",
                 return_value=PageRef("Alpha", "Alpha"))


def test_note_content_does_not_create_a_page_it_failed_to_look_up():
    api = _api("Beta")
    api.get_page.side_effect = requests.ConnectionError("connection dropped")
    with _resolved("pages"):
        result = _run(api, "add-note-content", "--page", "Alpha", "--content", "x", "--create")
    assert result.exit_code != 0
    api.create_page.assert_not_called()


def test_a_failed_read_of_the_first_block_is_not_no_properties():
    api = _api("Beta")
    api.get_page.side_effect = lambda n: {"name": "alpha", "originalName": "Alpha"}
    api.get_page_blocks_tree.side_effect = requests.ConnectionError("connection dropped")
    with _resolved("properties"):
        result = _run(api, "get-properties", "--page", "Alpha")
    assert result.exit_code != 0
    assert json.loads(result.stderr)["reason"] == "connection_refused"
