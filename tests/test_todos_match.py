"""#27: get-todos filters by what a task says, and its JSON shape is stated.

Recorded use: ``get-todos --json`` ran 7 times, 6 of them piped into an inline
script, 5 of those to filter the content by regex, all 6 guessing the shape
(``d if isinstance(d, list) else d.get('todos', d.get('items', []))``).
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from logseq_cli.cli import cli
from logseq_cli.commands.todos import _TODO_MARKERS
from tests.conftest import split_runner


def _rows():
    return [
        [{"content": "TODO Review the Alpha contract\nprio:: high", "marker": "TODO", "uuid": "u1"},
         {"original-name": "Sep 1st, 2026", "name": "sep 1st, 2026", "journal-day": 20260901}],
        [{"content": "DOING write alpha notes", "marker": "DOING", "uuid": "u2"},
         {"original-name": "Project Alpha", "name": "project alpha"}],
        [{"content": "TODO call the bank", "marker": "TODO", "uuid": "u3"},
         {"original-name": "Sep 2nd, 2026", "name": "sep 2nd, 2026", "journal-day": 20260902}],
    ]


def _run(*args):
    api = MagicMock()
    api.datascript_query.return_value = _rows()
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        return split_runner().invoke(cli, ["get-todos", "--no-follow-refs", *args]), api


def _uuids(result):
    return [t["uuid"] for t in json.loads(result.stdout)["todos"]]


class TestMatch:
    def test_filters_by_content_case_insensitively(self):
        r, _ = _run("--match", "alpha", "--json")
        assert r.exit_code == 0, r.stderr
        assert sorted(_uuids(r)) == ["u1", "u2"]

    def test_is_a_regex(self):
        r, _ = _run("--match", r"^(call|write)\b", "--json")
        assert sorted(_uuids(r)) == ["u2", "u3"]

    def test_matches_what_the_task_says_not_its_metadata(self):
        """prio:: high is a property line, stripped from content before output."""
        r, _ = _run("--match", "high", "--json")
        assert _uuids(r) == []

    def test_an_indented_property_line_is_metadata_too(self):
        """A block's properties may sit indented, as in the file; the shared
        PROPERTY_LINE_RE reads them, and get-todos no longer keeps its own."""
        api = MagicMock()
        api.datascript_query.return_value = [
            [{"content": "TODO sign it\n  owner:: someone", "marker": "TODO", "uuid": "u9"},
             {"original-name": "Page A", "name": "page a"}]]
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = split_runner().invoke(cli, ["get-todos", "--no-follow-refs",
                                            "--match", "someone", "--json"])
        assert json.loads(r.stdout)["count"] == 0

    def test_combines_with_other_filters(self):
        r, _ = _run("--match", "alpha", "--page", "sep", "--json")
        assert _uuids(r) == ["u1"]

    def test_invalid_regex_fails_before_the_query(self):
        r, api = _run("--match", "(unclosed")
        assert r.exit_code == 1
        assert "--match" in r.stderr
        api.datascript_query.assert_not_called()


class TestJsonShape:
    def test_journal_day_is_reported_where_the_page_has_one(self):
        r, _ = _run("--json")
        todos = {t["uuid"]: t for t in json.loads(r.stdout)["todos"]}
        assert todos["u1"]["journal_day"] == "2026-09-01"
        assert "journal_day" not in todos["u2"]  # ordinary page: absent, not null

    def test_shape_is_documented_in_help(self):
        api = MagicMock()
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = split_runner().invoke(cli, ["get-todos", "--help"])
        # Only the epilog's own text counts: "page" and "uuid" occur in the
        # option help anyway, which would let a bare word check pass.
        epilog = r.stdout[r.stdout.index("--json: {"):]
        for field in ('"todos"', '"count"', '"repeating_excluded"', "marker,", "content",
                      "page", "uuid", "journal_day", "scheduled", "deadline", "repeating",
                      "next_due", "references", "references_withheld"):
            assert field in epilog, field


class TestMarkerIsStrippedForEveryMarker:
    """content promised "without its marker", but the strip was a hand-kept list
    that missed CANCELED and WAIT, both written by set-todo-status: --match
    "^ship" missed "CANCELED ship it" and --match cancel hit them all. The
    block's own :block/marker now says what to strip."""

    @pytest.mark.parametrize("marker", sorted(_TODO_MARKERS | {"CANCELLED", "IN-PROGRESS"}))
    def test_marker_is_not_part_of_the_content(self, marker):
        api = MagicMock()
        api.datascript_query.return_value = [
            [{"content": f"{marker} ship it", "marker": marker, "uuid": "u1"},
             {"original-name": "Page A", "name": "page a"}]]
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = split_runner().invoke(cli, ["get-todos", "--no-follow-refs", "--status", marker,
                                            "--match", "^ship", "--json"])
        todos = json.loads(r.stdout)["todos"]
        assert [t["content"] for t in todos] == ["ship it"]

    def test_a_bare_marker_leaves_no_text(self):
        api = MagicMock()
        api.datascript_query.return_value = [
            [{"content": "TODO", "marker": "TODO", "uuid": "u1"},
             {"original-name": "Page A", "name": "page a"}]]
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = split_runner().invoke(cli, ["get-todos", "--no-follow-refs", "--json"])
        assert json.loads(r.stdout)["todos"][0]["content"] == ""
