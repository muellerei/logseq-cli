"""`suggest-connections`: what the cap leaves out is named, and "found" counts the graph.

`total_found` was computed after the cap, so it answered "how many did you get"
under a name that promises "how many are there". With three qualifying pairs and
`--max-suggestions 1` it reported `total_found: 1` — a statement about the graph
that the graph did not support, and the truncation that caused it went
unmentioned in both formats.

Same family as the `get-backlinks` defect in #17: a count derived from the cap
rather than from what was actually there.
"""
import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from logseq_cli.cli import cli
from tests.conftest import split_runner


def _api(page_count=3, topics=("alpha", "beta", "gamma", "delta")):
    """Pages that all share the same topics, so every pair qualifies."""
    api = MagicMock()
    api.get_all_pages.return_value = [{"originalName": f"P{i}"} for i in range(page_count)]
    api.get_page_blocks_tree.return_value = [
        {"content": " ".join(f"[[{t}]]" for t in topics), "children": []}
    ]
    return api


def _run(args, api, split=False):
    runner = split_runner() if split else CliRunner()
    with patch("logseq_cli.cli.LogseqAPI", return_value=api):
        return runner.invoke(cli, args)


class TestTotalFoundCountsTheGraph:
    def test_total_found_is_not_reduced_by_the_cap(self):
        result = _run(["suggest-connections", "--max-suggestions", "1", "--json"], _api())
        data = json.loads(result.output)
        assert data["total_found"] == 3, (
            "total_found reported the survivors, not what was found"
        )
        assert len(data["suggestions"]) == 1

    def test_what_the_cap_left_out_is_reported(self):
        result = _run(["suggest-connections", "--max-suggestions", "1", "--json"], _api())
        data = json.loads(result.output)
        assert data["withheld"] == 2
        assert len(data["suggestions"]) + data["withheld"] == data["total_found"]

    def test_nothing_is_reported_as_withheld_when_nothing_was(self):
        result = _run(["suggest-connections", "--max-suggestions", "10", "--json"], _api())
        data = json.loads(result.output)
        assert "withheld" not in data

    def test_the_plain_output_says_so_on_stderr(self):
        """Truncation is never silent, and stdout stays payload."""
        result = _run(["suggest-connections", "--max-suggestions", "1"], _api(), split=True)
        assert "2 omitted" in result.stderr
        assert "omitted" not in result.stdout

    def test_both_formats_agree_on_the_number(self):
        plain = _run(["suggest-connections", "--max-suggestions", "1"], _api(), split=True)
        as_json = _run(["suggest-connections", "--max-suggestions", "1", "--json"], _api())
        withheld = json.loads(as_json.output)["withheld"]
        assert f"{withheld} omitted" in plain.stderr


class TestZeroIsRefused:
    def test_zero_suggestions_is_not_an_answer(self):
        """It used to return an empty list under a message blaming the graph."""
        result = _run(["suggest-connections", "--max-suggestions", "0"], _api(), split=True)
        assert result.exit_code == 1
        assert "1 or greater" in result.stderr

    def test_the_empty_message_is_still_used_when_the_graph_is_really_empty(self):
        """The guard must not have taken the honest message with it."""
        api = _api(page_count=2, topics=("alpha",))
        result = _run(["suggest-connections", "--min-shared", "5"], api)
        assert result.exit_code == 0
        assert "No connections found" in result.output
