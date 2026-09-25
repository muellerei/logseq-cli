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
