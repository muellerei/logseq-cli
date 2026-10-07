"""get-todos --page-type: tasks by the kind of page their block stands on.

Fixture: J1 stands on a journal, P1 on an ordinary page with no reference, P2 on
an ordinary page and is carried into a journal by a ((block-ref)).
"""

import json as _json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from logseq_cli.blocktext import block_hash
from logseq_cli.cli import cli
from tests.test_get_todos import _mock_api_for_todos

J1 = ({"content": "TODO journal task", "marker": "TODO", "uuid": "u-j1"},
      {"original-name": "Mar 10th, 2026", "name": "mar 10th, 2026", "journal-day": 20260310})
P1 = ({"content": "TODO plain page task", "marker": "TODO", "uuid": "u-p1"},
      {"original-name": "Project Alpha", "name": "project alpha"})
P2 = ({"content": "TODO carried task", "marker": "TODO", "uuid": "u-p2"},
      {"original-name": "Project Beta", "name": "project beta"})
P2_ON_MARCH_12 = ({"uuid": "u-p2"},
                  {"original-name": "Mar 12th, 2026", "name": "mar 12th, 2026",
                   "journal-day": 20260312})

# The output before the option existed, taken from the unchanged command (plus
# the hash every block read carries).
BEFORE_JSON = {
    "todos": [
        {"marker": "TODO", "content": "journal task", "page": "Mar 10th, 2026",
         "uuid": "u-j1", "hash": block_hash("TODO journal task"), "journal_day": "2026-03-10"},
        {"marker": "TODO", "content": "plain page task", "page": "Project Alpha",
         "uuid": "u-p1", "hash": block_hash("TODO plain page task")},
        {"marker": "TODO", "content": "carried task", "page": "Project Beta",
         "uuid": "u-p2", "hash": block_hash("TODO carried task"), "references": ["Mar 12th, 2026"]},
    ],
    "count": 3,
}
BEFORE_TEXT = (
    "Tasks (3):\n\n"
    "  TODO [Mar 10th, 2026] journal task\n"
    "  TODO [Project Alpha] plain page task\n"
    "  TODO [Project Beta] carried task\n"
    "      also on: Mar 12th, 2026\n"
)


def _run(args, rows=(J1, P1, P2), refs=(P2_ON_MARCH_12,)):
    api = _mock_api_for_todos(list(rows), list(refs))
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        return CliRunner().invoke(cli, ["get-todos", *args])


def _uuids(args, **kw):
    result = _run([*args, "--json"], **kw)
    assert result.exit_code == 0, result.output
    return [t["uuid"] for t in _json.loads(result.output)["todos"]]


class TestSelection:
    def test_journal_gives_the_journal_task_page_gives_the_others(self):  # AK1
        assert _uuids(["--page-type", "journal"]) == ["u-j1"]
        assert _uuids(["--page-type", "page"]) == ["u-p1", "u-p2"]

    def test_count_follows_the_filter(self):
        data = _json.loads(_run(["--page-type", "page", "--json"]).output)
        assert data["count"] == 2

    def test_without_the_option_the_answer_is_what_it_was(self):  # AK2
        assert _json.loads(_run(["--json"]).output) == BEFORE_JSON
        assert _run([]).output == BEFORE_TEXT

    def test_a_carried_task_is_a_page_task_even_inside_a_journal_range(self):  # AK4
        rng = ["--from", "2026-03-11", "--to", "2026-03-13"]
        assert _uuids([*rng, "--page-type", "journal"]) == []
        assert _uuids([*rng, "--page-type", "page"]) == ["u-p2"]

    def test_references_are_left_alone(self):
        data = _json.loads(_run(["--page-type", "page", "--json"]).output)
        carried = next(t for t in data["todos"] if t["uuid"] == "u-p2")
        assert carried["references"] == ["Mar 12th, 2026"]


class TestValue:
    def test_an_unknown_value_is_refused_naming_the_choices(self):  # AK3a
        result = _run(["--page-type", "daily"])
        assert result.exit_code == 2
        assert "journal" in result.output and "page" in result.output

    @pytest.mark.parametrize("spelled,plain", [("Journal", "journal"), ("PAGE", "page")])
    def test_case_does_not_matter(self, spelled, plain):  # AK3b
        assert _uuids(["--page-type", spelled]) == _uuids(["--page-type", plain])


class TestRange:
    """E1: the option only narrows; the range keeps its own rule."""

    def _journal_on_march_10(self, with_ref):
        refs = ({"uuid": "u-j1"}, {"original-name": "Mar 1st, 2026", "name": "mar 1st, 2026",
                                   "journal-day": 20260301})
        return dict(rows=(J1,), refs=(refs,) if with_ref else ())

    def test_to_keeps_a_journal_task_with_an_earlier_reference(self):  # AK5a
        # the existing OR branch: a reference inside the range counts
        args = ["--to", "2026-03-05", "--page-type", "journal"]
        assert _uuids(args, **self._journal_on_march_10(with_ref=True)) == ["u-j1"]
        assert _uuids(args, **self._journal_on_march_10(with_ref=False)) == []

    def test_from_keeps_a_journal_task_with_a_later_reference(self):  # AK5b
        # the existing OR branch: a reference inside the range counts
        early = ({"content": "TODO early", "marker": "TODO", "uuid": "u-e"},
                 {"original-name": "Mar 1st, 2026", "name": "mar 1st, 2026",
                  "journal-day": 20260301})
        late = ({"uuid": "u-e"}, {"original-name": "Mar 10th, 2026", "name": "mar 10th, 2026",
                                  "journal-day": 20260310})
        args = ["--from", "2026-03-05", "--page-type", "journal"]
        assert _uuids(args, rows=(early,), refs=(late,)) == ["u-e"]
        assert _uuids(args, rows=(early,), refs=()) == []

    def test_a_carried_ordinary_page_task_is_in_the_range_only_under_page(self):  # AK5c
        rng = ["--from", "2026-03-11", "--to", "2026-03-13"]
        assert "u-p2" not in _uuids([*rng, "--page-type", "journal"])
        assert "u-p2" in _uuids([*rng, "--page-type", "page"])

    @pytest.mark.parametrize("rng", [[], ["--from", "2026-03-10", "--to", "2026-03-13"]])
    def test_the_two_values_split_the_unfiltered_set(self, rng):  # AK6
        journal = set(_uuids([*rng, "--page-type", "journal"]))
        page = set(_uuids([*rng, "--page-type", "page"]))
        assert journal and page
        assert not journal & page
        assert journal | page == set(_uuids(rng))

    def test_the_recipe_with_no_follow_refs_dates_by_the_blocks_own_day(self):  # AK7
        args = ["--no-follow-refs", "--to", "2026-03-10"]
        assert _uuids([*args, "--page-type", "journal"]) == ["u-j1"]
        assert _uuids([*args, "--page-type", "page"]) == []


class TestWithPage:
    def test_both_must_hold(self):  # AK8
        assert _uuids(["--page", "Alpha", "--page-type", "page"]) == ["u-p1"]
        assert _uuids(["--page", "Alpha", "--page-type", "journal"]) == []
        assert _uuids(["--page", "Mar", "--page-type", "journal"]) == ["u-j1"]
        assert _uuids(["--page", "Mar", "--page-type", "page"]) == []
