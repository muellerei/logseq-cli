"""Tests for find-block --limit and the notice that matches were withheld.

The defect this pins: a common search term matched thousands of blocks and all
of them were printed. An agent hitting its response cap got the answer cut off
with nothing saying so — the same unbounded-output failure the journal read
paths closed in 0.6.0, still open on the search path.

The cut cannot happen in the query: DataScript ignores a `:limit` clause and
returns the whole result set regardless. So these tests pin the two things that
are actually load-bearing — the caller gets at most N, and never silently.
"""

import json as _json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from logseq_cli.cli import cli
from tests.conftest import split_runner


def _blocks(n):
    return [
        {"uuid": f"uuid-{i}", "content": f"match number {i}",
         "page": {"original-name": "Page A", "name": "page a"}}
        for i in range(n)
    ]


def _run(args, n_matches=50, split=False):
    """Invoke find-block. ``split`` keeps stderr out of stdout, which is the
    only way to assert that the payload stayed parseable."""
    api = MagicMock()
    api.datascript_query.return_value = [[b] for b in _blocks(n_matches)]
    runner = split_runner() if split else CliRunner()
    with patch("logseq_cli.cli.LogseqAPI", return_value=api):
        return runner.invoke(cli, ["--token", "T", "find-block"] + args)


class TestLimit:
    def test_without_limit_every_match_is_printed(self):
        r = _run(["--content", "match"], n_matches=50)
        assert "Found 50 block(s)" in r.output

    def test_limit_caps_the_number_printed(self):
        r = _run(["--content", "match", "--limit", "5"], n_matches=50)
        assert "Found 5 block(s)" in r.output

    def test_limit_above_the_match_count_changes_nothing(self):
        r = _run(["--content", "match", "--limit", "99"], n_matches=50)
        assert "Found 50 block(s)" in r.output
        assert "omitted" not in r.output

    def test_json_output_is_capped_too(self):
        r = _run(["--content", "match", "--limit", "3", "--json"], n_matches=50, split=True)
        assert len(_json.loads(r.stdout)) == 3


class TestTheNoticeIsNeverSilent:
    def test_withholding_is_reported_with_both_numbers(self):
        r = _run(["--content", "match", "--limit", "5"], n_matches=50)
        assert "showing 5 of 50 match(es)" in r.output
        assert "45 omitted" in r.output

    def test_first_also_reports_what_it_dropped(self):
        # --first silently discarded the rest before; a caller could not tell
        # an unambiguous hit from one of hundreds.
        r = _run(["--content", "match", "--first"], n_matches=50)
        assert "showing 1 of 50 match(es)" in r.output

    def test_first_on_a_single_match_says_nothing(self):
        r = _run(["--content", "match", "--first"], n_matches=1)
        assert "omitted" not in r.output

    def test_json_keeps_stdout_pure(self):
        # The notice must not land in the payload an agent parses.
        r = _run(["--content", "match", "--limit", "5", "--json"], n_matches=50, split=True)
        _json.loads(r.stdout)  # raises if the notice leaked into stdout
        assert "omitted" in r.stderr


class TestArgumentChecking:
    def test_first_and_limit_together_are_refused(self):
        r = _run(["--content", "match", "--first", "--limit", "5"])
        assert r.exit_code == 1
        assert "not both" in r.output

    def test_a_limit_below_one_is_refused(self):
        r = _run(["--content", "match", "--limit", "0"])
        assert r.exit_code == 1
