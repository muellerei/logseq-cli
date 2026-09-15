"""Tests for get-todos: page-name inline per TODO."""

import json as _json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from logseq_cli.cli import cli


def _mock_api_for_todos(todo_rows):
    """Build a mocked API that returns datascript_query rows for get-todos.

    Each row in ``todo_rows`` is (block_dict, page_dict) per the get-todos query.
    """
    api = MagicMock()
    api.datascript_query.return_value = todo_rows
    return api


class TestGetTodosPageInline:
    """Page name must appear with each TODO in plain text and JSON."""

    def test_plain_text_shows_page_per_todo(self):
        rows = [
            ({"content": "TODO first task", "marker": "TODO", "uuid": "u1"},
             {"original-name": "Project Beta", "name": "project beta"}),
            ({"content": "DOING second task", "marker": "DOING", "uuid": "u2"},
             {"original-name": "Project Alpha", "name": "project alpha"}),
        ]
        api = _mock_api_for_todos(rows)
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, ["get-todos"])
        assert result.exit_code == 0, result.output
        # Each TODO line should mention its page directly
        # Find the lines containing the first todo
        lines = result.output.splitlines()
        line_with_first = next((l for l in lines if "first task" in l), "")
        line_with_second = next((l for l in lines if "second task" in l), "")
        assert "Project Beta" in line_with_first, f"page missing inline: {line_with_first!r}"
        assert "Project Alpha" in line_with_second, f"page missing inline: {line_with_second!r}"

    def test_json_includes_page_per_todo(self):
        rows = [
            ({"content": "TODO foo", "marker": "TODO", "uuid": "u1"},
             {"original-name": "PageA", "name": "pagea"}),
        ]
        api = _mock_api_for_todos(rows)
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, ["get-todos", "--json"])
        assert result.exit_code == 0, result.output
        data = _json.loads(result.output)
        todos = data["todos"]
        assert len(todos) == 1
        assert todos[0]["page"] == "PageA"
        assert "uuid" in todos[0]

    def test_filter_by_page_still_works(self):
        rows = [
            ({"content": "TODO a", "marker": "TODO", "uuid": "u1"},
             {"original-name": "Alpha", "name": "alpha"}),
            ({"content": "TODO b", "marker": "TODO", "uuid": "u2"},
             {"original-name": "Beta", "name": "beta"}),
        ]
        api = _mock_api_for_todos(rows)
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, ["get-todos", "--page", "Alpha", "--json"])
        assert result.exit_code == 0, result.output
        data = _json.loads(result.output)
        assert data["count"] == 1
        assert data["todos"][0]["page"] == "Alpha"

    def test_status_flag_passed_into_query(self):
        """--status flag must be reflected in the datascript query string."""
        rows = []
        api = _mock_api_for_todos(rows)
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            runner.invoke(cli, ["get-todos", "--status", "DOING"])
        # Verify the query string contained DOING
        query = api.datascript_query.call_args[0][0]
        assert "DOING" in query


class TestGetTodosDateRange:
    """--from/--to must filter the whole result, not only the journal subset.

    The defect this guards: a task whose page carries no ``journal-day`` used to
    pass through every range unchanged, so a range that predates the graph still
    returned every non-journal task. A filter that keeps most of what it is asked
    to exclude cannot be relied on, because the caller cannot tell which part of
    the output was filtered.
    """

    # journal-day is Logseq's YYYYMMDD integer, not a date string.
    _JOURNAL_2026_05_10 = 20260510

    def _rows(self):
        return [
            # On a journal page, inside the range used below.
            ({"content": "TODO in range", "marker": "TODO", "uuid": "u1"},
             {"original-name": "May 10th, 2026", "name": "may 10th, 2026",
              "journal-day": self._JOURNAL_2026_05_10}),
            # On an ordinary page: no journal-day at all.
            ({"content": "TODO on a plain page", "marker": "TODO", "uuid": "u2"},
             {"original-name": "Project Alpha", "name": "project alpha"}),
        ]

    def test_impossible_range_returns_nothing(self):
        """A range that predates the graph must not return non-journal tasks."""
        api = _mock_api_for_todos(self._rows())
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(
                cli, ["get-todos", "--from", "1990-01-01", "--to", "1990-01-02", "--json"]
            )
        assert result.exit_code == 0, result.output
        todos = _json.loads(result.output)["todos"]
        assert todos == [], f"range predating the graph still returned {todos!r}"

    def test_range_keeps_journal_task_and_drops_undated_one(self):
        api = _mock_api_for_todos(self._rows())
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(
                cli, ["get-todos", "--from", "2026-05-01", "--to", "2026-05-31", "--json"]
            )
        assert result.exit_code == 0, result.output
        todos = _json.loads(result.output)["todos"]
        contents = [t["content"] for t in todos]
        assert "in range" in " ".join(contents), f"journal task was dropped: {contents!r}"
        assert not any("plain page" in c for c in contents), (
            f"task without a journal date passed the filter: {contents!r}"
        )

    def test_without_range_everything_is_returned(self):
        """The filter only applies when asked for; the default is unchanged."""
        api = _mock_api_for_todos(self._rows())
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, ["get-todos", "--json"])
        assert result.exit_code == 0, result.output
        todos = _json.loads(result.output)["todos"]
        assert len(todos) == 2, f"expected both tasks without a range, got {todos!r}"

    def test_only_from_still_drops_undated(self):
        """One-sided ranges filter too — the bound is set, so it applies."""
        api = _mock_api_for_todos(self._rows())
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, ["get-todos", "--from", "2026-05-01", "--json"])
        assert result.exit_code == 0, result.output
        contents = [t["content"] for t in _json.loads(result.output)["todos"]]
        assert not any("plain page" in c for c in contents), (
            f"task without a journal date passed a one-sided range: {contents!r}"
        )
