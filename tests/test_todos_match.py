"""#27: get-todos filters by what a task says, and its JSON shape is stated.

Recorded use: ``get-todos --json`` ran 7 times, 6 of them piped into an inline
script, 5 of those to filter the content by regex, all 6 guessing the shape
(``d if isinstance(d, list) else d.get('todos', d.get('items', []))``).
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from logseq_cli.cli import cli
from logseq_cli import tasks
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble


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

    @pytest.mark.parametrize("marker", sorted(tasks.ORDER))
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


class TestLogbookOpenerAndFenceAreText:
    """An unclosed :LOGBOOK: is text and one inside a code fence is code
    (mldoc 1.5.7): the task keeps them, and --match and --tag see them."""

    def _run_block(self, content, *args):
        api = MagicMock()
        api.datascript_query.return_value = [
            [{"content": content, "marker": "TODO", "uuid": "u1"},
             {"original-name": "Page A", "name": "page a"}]]
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = split_runner().invoke(cli, ["get-todos", "--no-follow-refs", "--json", *args])
        return json.loads(r.stdout)

    @pytest.mark.parametrize("content,match", [
        ("TODO x\n:LOGBOOK:\nno end #urgent", "no end"),
        ("TODO x\n```\n:LOGBOOK:\n```\ny #urgent", "y #urgent"),
    ])
    def test_the_text_stays_and_is_found(self, content, match):
        out = self._run_block(content)
        assert out["todos"][0]["content"] == content[len("TODO "):]
        assert self._run_block(content, "--match", match)["count"] == 1
        assert self._run_block(content, "--tag", "urgent")["count"] == 1

    def test_a_closed_drawer_stays_out(self):
        content = "TODO x\n:LOGBOOK:\nCLOCK: [2026-09-29 Tue 10:00:00]\n:END:\n#urgent"
        out = self._run_block(content)
        assert out["todos"][0]["content"] == "x\n#urgent"
        assert self._run_block(content, "--match", "CLOCK")["count"] == 0


HOST = "00000000-0000-4000-8000-0000000000b1"
TARGET = "00000000-0000-4000-8000-0000000000b2"


def _status_run(rows, *args, blocks=None):
    """set-todo-status --content over a mocked search that answers ``rows``
    (pull dicts, a marker where Logseq stored one); ``blocks`` answers getBlock."""
    api = MagicMock()
    api.datascript_query.return_value = [[r] for r in rows]
    api.get_block.side_effect = lambda uuid, **_: (blocks or {}).get(uuid)
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        r = split_runner().invoke(cli, ["set-todo-status", "--content", "x", "--page", "P",
                                        "--status", "DONE", *args])
    return r, api


class TestSetTodoStatusNeedsATask:
    """--content finds blocks by text; only a block with a marker is a task."""

    NOT_TASKS = [{"uuid": "u-A", "content": "see x above"}, {"uuid": "u-B", "content": "x again"}]

    @pytest.mark.parametrize("dry", [[], ["--dry-run"]], ids=["write", "dry-run"])
    def test_matches_without_a_task_are_refused(self, dry):
        r, api = _status_run(self.NOT_TASKS, "--json", *dry)
        assert r.exit_code != 0
        out = json.loads(r.stderr)
        assert out["reason"] == "no_task_matches"
        assert (out["content"], out["match_count"]) == ("x", 2)
        assert out["error"].endswith(
            'none is a task (no marker). Pass --id of a task, or write one as "TODO …".')
        assert "matches" not in out
        api.update_block.assert_not_called()

    def test_the_page_is_named_after_the_alias_resolution(self, monkeypatch):
        double = LogseqHttpDouble().install(monkeypatch)
        double.add_page("Target", ["alias:: zz-al", "see x above"])
        r = split_runner().invoke(cli, ["--token", "t", "set-todo-status", "--content", "x",
                                        "--page", "zz-al", "--status", "DONE", "--json"])
        out = json.loads(r.stderr)
        assert out["reason"] == "no_task_matches"
        assert out["page"] == "Target"
        assert "on page 'Target'" in out["error"]

    @pytest.mark.parametrize("dry", [[], ["--dry-run"]], ids=["write", "dry-run"])
    def test_with_follow_refs_one_match_without_a_marker_is_refused_as_no_task(self, dry):
        r, api = _status_run([{"uuid": "u-A", "content": "see x above"}],
                             "--follow-refs", "--json", *dry)
        assert r.exit_code != 0
        assert json.loads(r.stderr)["reason"] == "not_a_task"
        api.update_block.assert_not_called()

    def test_with_follow_refs_two_matches_without_a_marker_are_ambiguous(self):
        r, _ = _status_run(self.NOT_TASKS, "--follow-refs", "--json")
        assert json.loads(r.stderr)["reason"] == "ambiguous"

    def test_follow_refs_takes_a_match_that_only_points_to_a_task(self):
        pointer = {"uuid": HOST, "content": f"(({TARGET}))"}
        target = {"uuid": TARGET, "content": "TODO x", "marker": "TODO"}
        r, api = _status_run([pointer], "--follow-refs", "--json", blocks={TARGET: target})
        assert r.exit_code == 0, r.stderr
        out = json.loads(r.stdout)
        assert out["new"] == "DONE x"
        assert out["followed"] == [TARGET]
        assert api.update_block.call_args.args[0] == TARGET

    def test_a_pointer_without_follow_refs_names_follow_refs(self):
        api = MagicMock()
        api.get_block.return_value = {"uuid": HOST, "content": f"{{{{embed (({TARGET}))}}}}"}
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = split_runner().invoke(cli, ["set-todo-status", "--id", HOST,
                                            "--status", "DONE", "--dry-run", "--json"])
        out = json.loads(r.stderr)
        assert out["reason"] == "not_a_task" and out["points_to"] == TARGET
        assert "--follow-refs" in out["error"] and "TODO" not in out["error"]


class TestSetTodoStatusReasons:
    """The three refusals that were plain text now carry a reason."""

    def test_not_found_over_content_has_no_id(self):
        r, _ = _status_run([], "--json")
        assert r.exit_code != 0
        out = json.loads(r.stderr)
        assert out["reason"] == "block_not_found"
        assert (out["content"], "id" in out) == ("x", False)
        assert "No block found matching 'x' on page" in out["error"]

    def test_ambiguous_lists_every_candidate_in_matches(self):
        rows = [{"uuid": f"u-{i:02d}", "content": ("TODO " + "w" * 76) if i == 0 else f"TODO t{i}",
                 "marker": "TODO"} for i in range(12)]
        r, _ = _status_run(rows, "--json")
        out = json.loads(r.stderr)
        assert out["reason"] == "ambiguous"
        assert out["matches"] == [f"u-{i:02d}" for i in range(12)]
        assert "refusing to guess which one to update" in out["error"]
        assert out["error"].endswith("... and 2 more")
        first = next(ln for ln in out["error"].split("\n") if ln.startswith("  u-00"))
        assert first == "  u-00  " + ("TODO " + "w" * 76)[:70]

    def test_an_id_wins_over_content(self):
        api = MagicMock()
        api.get_block.return_value = {"uuid": TARGET, "content": "TODO x", "marker": "TODO"}
        with patch("logseq_cli.group.LogseqAPI", return_value=api), \
                patch("logseq_cli.commands.todos.find_blocks_by_content") as search:
            r = split_runner().invoke(cli, ["set-todo-status", "--id", TARGET, "--content", "x",
                                            "--page", "P", "--status", "DONE", "--json"])
        assert r.exit_code == 0, r.stderr
        search.assert_not_called()

    @pytest.mark.parametrize("args", [["--status", "DONE"], ["--content", "x", "--status", "DONE"]],
                             ids=["nothing", "content without page"])
    def test_a_missing_selector_is_refused_before_any_request(self, args):
        api = MagicMock()
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = split_runner().invoke(cli, ["set-todo-status", *args, "--json"])
        assert r.exit_code != 0
        out = json.loads(r.stderr)
        assert out == {"error": "Specify either --id or both --content and --page.",
                       "reason": "missing_selector"}
        assert api.method_calls == []

    def test_plain_text_gets_the_error_prefix_and_leaves_stdout_empty(self):
        for rows, text in (([], "Error: No block found matching 'x' on page"),
                           ([{"uuid": "a", "content": "TODO a", "marker": "TODO"},
                             {"uuid": "b", "content": "TODO b", "marker": "TODO"}],
                            "Error: 2 blocks match 'x' on page")):
            r, _ = _status_run(rows)
            assert r.exit_code != 0 and r.stdout == ""
            assert text in r.stderr
        api = MagicMock()
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = split_runner().invoke(cli, ["set-todo-status", "--status", "DONE"])
        assert r.stderr.startswith("Error: Specify either --id or both --content and --page.")


class TestAHeadingTaskKeepsItsHeadingInContent:
    def _content(self, *args):
        api = MagicMock()
        api.datascript_query.return_value = [
            [{"content": "## TODO ship it", "marker": "TODO", "uuid": "u1"},
             {"original-name": "Page A", "name": "page a"}]]
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = split_runner().invoke(cli, ["get-todos", "--no-follow-refs", "--json", *args])
        return json.loads(r.stdout)

    def test_the_marker_goes_the_heading_stays(self):
        assert self._content()["todos"][0]["content"] == "## ship it"

    def test_match_sees_the_text_not_the_marker(self):
        assert self._content("--match", "^## ship")["count"] == 1
        assert self._content("--match", "TODO ship")["count"] == 0
