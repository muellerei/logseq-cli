"""Tests for get-todos: page-name inline per TODO."""

import json as _json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from logseq_cli.cli import cli


def _mock_api_for_todos(todo_rows, ref_rows=None):
    """Build a mocked API that returns datascript_query rows for get-todos.

    ``get-todos`` issues two queries: the todos themselves, then — unless
    ``--no-follow-refs`` is given — the blocks that reference them. The mock
    answers them in that order.

    Each row in ``todo_rows`` is (block_dict, page_dict) per the todo query.
    Each row in ``ref_rows`` is (block_dict, page_dict) per the reference
    query, where block_dict identifies the *referenced* todo by uuid and
    page_dict is the page the reference sits on.
    """
    api = MagicMock()
    api.datascript_query.side_effect = lambda q: (
        ref_rows or [] if ":block/refs" in q else todo_rows
    )
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
        # Verify the query string contained DOING. The todo query is the first
        # one; the reference query follows it.
        query = api.datascript_query.call_args_list[0][0][0]
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


class TestGetTodosBlockReferences:
    """A todo carried forward by ``((uuid))`` must be found on the day it stands.

    The defect this guards: ``get-todos`` located a task only through the page
    its block lives on. Carrying an open task forward by reference is the
    ordinary way to work in Logseq — the block exists once, every later
    occurrence is a reference to it — so a date range over those later days
    returned nothing at all, with no sign that anything had been left out.
    """

    # The origin block sits outside every range used below, so a todo that
    # shows up in one can only have been found through its references.
    _ORIGIN = ({"content": "TODO write the migration guide", "marker": "TODO",
                "uuid": "u-carried"},
               {"original-name": "Mar 4th, 2026", "name": "mar 4th, 2026",
                "journal-day": 20260304})

    def _ref(self, day, name=None):
        return ({"uuid": "u-carried"},
                {"original-name": name or f"journal {day}", "journal-day": day})

    def test_todo_is_found_through_a_reference(self):
        api = _mock_api_for_todos([self._ORIGIN], [self._ref(20260319)])
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(
                cli, ["get-todos", "--from", "2026-03-17", "--to", "2026-03-19", "--json"])
        assert result.exit_code == 0, result.output
        todos = _json.loads(result.output)["todos"]
        assert len(todos) == 1, f"todo carried into the range was not found: {todos!r}"
        assert todos[0]["uuid"] == "u-carried"

    def test_origin_fields_are_unchanged(self):
        """``page`` and ``uuid`` keep naming where the block lives."""
        api = _mock_api_for_todos([self._ORIGIN], [self._ref(20260319)])
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(
                cli, ["get-todos", "--from", "2026-03-17", "--to", "2026-03-19", "--json"])
        todo = _json.loads(result.output)["todos"][0]
        assert todo["page"] == "Mar 4th, 2026", (
            f"page must stay the origin, got {todo['page']!r}")
        assert todo["uuid"] == "u-carried"

    def test_many_references_yield_one_row(self):
        """A todo referenced N times is one task, not N tasks."""
        refs = [self._ref(20260317 + i) for i in range(3)]
        api = _mock_api_for_todos([self._ORIGIN], refs)
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(
                cli, ["get-todos", "--from", "2026-03-17", "--to", "2026-03-19", "--json"])
        data = _json.loads(result.output)
        assert data["count"] == 1, f"references were not deduplicated: {data!r}"
        assert len(data["todos"][0]["references"]) == 3

    def test_references_are_sorted_newest_first(self):
        """Datalog guarantees no order, so the sort has to be explicit."""
        refs = [self._ref(20260317, "Mar 17th, 2026"),
                self._ref(20260319, "Mar 19th, 2026"),
                self._ref(20260318, "Mar 18th, 2026")]
        api = _mock_api_for_todos([self._ORIGIN], refs)
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(
                cli, ["get-todos", "--from", "2026-03-17", "--to", "2026-03-19", "--json"])
        refs_out = _json.loads(result.output)["todos"][0]["references"]
        assert refs_out == ["Mar 19th, 2026", "Mar 18th, 2026", "Mar 17th, 2026"], refs_out

    def test_occurrences_outside_the_range_are_counted_not_listed(self):
        """Trimming must not hide that a todo has been carried for months."""
        refs = [self._ref(20260319)] + [self._ref(20260101 + i) for i in range(5)]
        api = _mock_api_for_todos([self._ORIGIN], refs)
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(
                cli, ["get-todos", "--from", "2026-03-17", "--to", "2026-03-19", "--json"])
        todo = _json.loads(result.output)["todos"][0]
        assert len(todo["references"]) == 1, todo["references"]
        assert todo["references_withheld"] == 5, todo

    def test_withheld_is_absent_when_nothing_was_withheld(self):
        api = _mock_api_for_todos([self._ORIGIN], [self._ref(20260319)])
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(
                cli, ["get-todos", "--from", "2026-03-17", "--to", "2026-03-19", "--json"])
        todo = _json.loads(result.output)["todos"][0]
        assert "references_withheld" not in todo, todo

    def test_refs_limit_caps_the_list_and_counts_the_rest(self):
        refs = [self._ref(20260301 + i) for i in range(5)]
        api = _mock_api_for_todos([self._ORIGIN], refs)
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, ["get-todos", "--refs-limit", "2", "--json"])
        todo = _json.loads(result.output)["todos"][0]
        assert len(todo["references"]) == 2, todo["references"]
        assert todo["references_withheld"] == 3, todo

    def test_refs_limit_zero_keeps_all(self):
        refs = [self._ref(20260301 + i) for i in range(5)]
        api = _mock_api_for_todos([self._ORIGIN], refs)
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, ["get-todos", "--refs-limit", "0", "--json"])
        todo = _json.loads(result.output)["todos"][0]
        assert len(todo["references"]) == 5, todo["references"]
        assert "references_withheld" not in todo, todo

    def test_no_follow_refs_restores_the_old_reading(self):
        """For callers who want where blocks live, not where they appear."""
        api = _mock_api_for_todos([self._ORIGIN], [self._ref(20260319)])
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(
                cli, ["get-todos", "--no-follow-refs", "--from", "2026-03-17",
                      "--to", "2026-03-19", "--json"])
        data = _json.loads(result.output)
        assert data["todos"] == [], f"--no-follow-refs still resolved refs: {data!r}"

    def test_no_follow_refs_issues_no_second_query(self):
        api = _mock_api_for_todos([self._ORIGIN], [self._ref(20260319)])
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            runner.invoke(cli, ["get-todos", "--no-follow-refs", "--json"])
        assert api.datascript_query.call_count == 1, (
            "--no-follow-refs must not pay for a read it does not use")

    def test_references_on_non_journal_pages_fall_out_of_a_range(self):
        """Same rule as the origin page: no journal-day, no place in the range.

        44 of 248 reference occurrences in the measured graph sit on ordinary
        pages. Letting them count would reopen the silent gap this command
        already closed for origin pages.
        """
        refs = [({"uuid": "u-carried"}, {"original-name": "Project Alpha"})]
        api = _mock_api_for_todos([self._ORIGIN], refs)
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(
                cli, ["get-todos", "--from", "2026-03-17", "--to", "2026-03-19", "--json"])
        data = _json.loads(result.output)
        assert data["todos"] == [], (
            f"a reference on a page with no journal-day entered a range: {data!r}")

    def test_non_journal_reference_is_listed_without_a_range(self):
        """Without a range there is nothing to fall outside of."""
        refs = [({"uuid": "u-carried"}, {"original-name": "Project Alpha"})]
        api = _mock_api_for_todos([self._ORIGIN], refs)
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, ["get-todos", "--json"])
        todo = _json.loads(result.output)["todos"][0]
        assert todo["references"] == ["Project Alpha"], todo

    def test_a_todo_without_references_has_no_references_field(self):
        """Callers reading todos that are not carried see the payload they saw."""
        api = _mock_api_for_todos([self._ORIGIN], [])
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, ["get-todos", "--json"])
        todo = _json.loads(result.output)["todos"][0]
        assert "references" not in todo, todo
        assert "references_withheld" not in todo, todo

    def test_page_filter_matches_the_origin_not_the_reference(self):
        """--page selects which todos appear; references still count graph-wide.

        The alternative — restricting references to the queried page — would
        make the field mean something different per call, and would mean
        nothing at all for --tag, which is not a page.
        """
        api = _mock_api_for_todos([self._ORIGIN], [self._ref(20260319, "Mar 19th, 2026")])
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, ["get-todos", "--page", "Mar 4th", "--json"])
        todos = _json.loads(result.output)["todos"]
        assert len(todos) == 1, todos
        assert todos[0]["references"] == ["Mar 19th, 2026"], todos[0]

    def test_a_reference_alone_does_not_invent_a_todo(self):
        """Only blocks the todo query returned may appear; refs add no rows."""
        refs = [({"uuid": "u-unknown"}, {"original-name": "Mar 19th, 2026",
                                          "journal-day": 20260319})]
        api = _mock_api_for_todos([self._ORIGIN], refs)
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, ["get-todos", "--json"])
        uuids = [t["uuid"] for t in _json.loads(result.output)["todos"]]
        assert uuids == ["u-carried"], uuids

    def test_duplicate_reference_dates_are_collapsed(self):
        """Two references on one day are one occurrence of that day."""
        refs = [self._ref(20260319, "Mar 19th, 2026"),
                self._ref(20260319, "Mar 19th, 2026")]
        api = _mock_api_for_todos([self._ORIGIN], refs)
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, ["get-todos", "--json"])
        todo = _json.loads(result.output)["todos"][0]
        assert todo["references"] == ["Mar 19th, 2026"], todo

    def test_plain_text_names_the_occurrences(self):
        """The defect was invisible in plain text too, not only in JSON."""
        refs = [self._ref(20260319, "Mar 19th, 2026")]
        api = _mock_api_for_todos([self._ORIGIN], refs)
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(
                cli, ["get-todos", "--from", "2026-03-17", "--to", "2026-03-19"])
        assert result.exit_code == 0, result.output
        assert "Mar 19th, 2026" in result.output, result.output

    def test_plain_text_separates_occurrences_unambiguously(self):
        """Journal names hold a comma, so the list must not be comma-separated.

        A real journal page is named "2026-09-16, Wednesday". Joined with ", "
        two of them read as four entries.
        """
        refs = [self._ref(20260916, "2026-09-16, Wednesday"),
                self._ref(20260914, "2026-09-14, Monday")]
        api = _mock_api_for_todos([self._ORIGIN], refs)
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, ["get-todos"])
        line = next(l for l in result.output.splitlines() if "also on" in l)
        assert "Wednesday; 2026-09-14" in line, (
            f"occurrences are not separably delimited: {line!r}")
