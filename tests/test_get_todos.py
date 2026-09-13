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
