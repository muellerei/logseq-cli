"""analyze-journal-patterns — the command whose patterns are configurable.

The mood word lists and project markers come from [analysis]; tasks are the
blocks Logseq gave a marker, counted by one query. Both fail quietly when
wrong: an empty result looks exactly like a journal with nothing in it.
"""

import json
import os
import re
from unittest.mock import MagicMock, patch


from logseq_cli.cli import cli
from logseq_cli.dates import parse_date_range
from tests.conftest import journal_day, split_runner

JOURNAL = {"originalName": "J", "journalDay": journal_day(), "journal?": True}


def run(text, *args, config=None, tmp_path=None, rows=(), pages=None, as_json=True):
    """analyze-journal-patterns over journal pages that all read ``text``.

    ``rows`` answers the task query ([block, marker, day] rows, as the database
    gives them; None for an answer of none). It is always set: a MagicMock
    answers a query with something that iterates empty, and a test that says
    nothing about tasks would count zero without anyone seeing it.
    """
    api = MagicMock()
    api.get_all_pages.return_value = pages if pages is not None else [JOURNAL]
    api.datascript_query.return_value = None if rows is None else list(rows)
    # An empty file rather than no variable at all: without it the developer's
    # own config is read, and a test for "the English defaults find nothing in
    # a German journal" passes or fails depending on whose machine it runs on.
    if config is None:
        config = ""
    f = tmp_path / "c.toml"
    f.write_text(config, encoding="utf-8")
    env = {"LOGSEQ_CLI_CONFIG": str(f)}
    with patch.dict(os.environ, env, clear=False), \
         patch("logseq_cli.group.LogseqAPI", return_value=api), \
         patch("logseq_cli.commands.analysis.get_page_content", return_value=text):
        result = split_runner().invoke(
            cli, ["--token", "X", "analyze-journal-patterns",
                  "--timeframe", "last 30 days", *(["--json"] if as_json else []), *args])
    result.api = api
    return result


def payload(result):
    assert result.exit_code == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


DAY = journal_day()


def _rows(*markers, day=DAY):
    return [[i, m, day] for i, m in enumerate(markers, start=1)]


class TestTaskCounting:
    """A task is a block Logseq gave a marker, counted by one query; the text of
    the page takes no part. The mock applies no marker rule: what comes out can
    only come from the rows it answers."""

    def test_logseq_markers_are_counted(self, tmp_path):
        d = payload(run("- text", tmp_path=tmp_path, rows=_rows("TODO", "DOING", "DONE")))
        assert d["tasks"]["open"] == 2 and d["tasks"]["done"] == 1
        assert d["entries"][0]["tasks"] == {"open": 2, "done": 1, "cancelled": 0}

    def test_checkbox_lines_are_not_tasks(self, tmp_path):
        """Logseq reads "- [ ]" at the start of a block as text, not as a task."""
        d = payload(run("- [ ] open\n- [x] closed\n- [X] also closed", tmp_path=tmp_path))
        assert d["tasks"]["open"] == 0 and d["tasks"]["done"] == 0

    def test_marker_words_in_prose_are_not_tasks(self, tmp_path):
        """No marker in the database, so no task, whatever the text says."""
        d = payload(run("- Now that we finished it\n- that is done\n- todo: later",
                        tmp_path=tmp_path))
        assert d["tasks"]["open"] == 0 and d["tasks"]["done"] == 0

    def test_an_empty_journal_reports_zero_without_dividing_by_zero(self, tmp_path):
        d = payload(run("- just some prose", tmp_path=tmp_path))
        assert d["tasks"] == {"open": 0, "done": 0, "cancelled": 0, "done_rate": 0}

    def test_a_block_without_marker_does_not_count_whatever_the_text_says(self, tmp_path):
        text = "- TODO: x\n- [ ] x\n- TODO\nnotes\n- Now that we finished it"
        d = payload(run(text, tmp_path=tmp_path, rows=[]))
        assert d["tasks"] == {"open": 0, "done": 0, "cancelled": 0, "done_rate": 0}
        assert d["entries"][0]["tasks"] == {"open": 0, "done": 0, "cancelled": 0}

    def test_cancelled_is_not_done_and_not_in_the_rate(self, tmp_path):
        d = payload(run("- x", tmp_path=tmp_path,
                        rows=_rows("TODO", "DONE", "DONE", "CANCELED")))
        assert d["tasks"] == {"open": 1, "done": 2, "cancelled": 1, "done_rate": 66.7}
        d = payload(run("- x", tmp_path=tmp_path, rows=_rows("CANCELED", "CANCELLED")))
        assert d["tasks"] == {"open": 0, "done": 0, "cancelled": 2, "done_rate": 0}

    def test_the_old_keys_are_gone(self, tmp_path):
        d = payload(run("- x", tmp_path=tmp_path, rows=_rows("TODO", "DONE")))
        assert set(d["tasks"]) == {"open", "done", "cancelled", "done_rate"}
        for entry in d["entries"]:
            assert set(entry["tasks"]) == {"open", "done", "cancelled"}
            assert "tasks_complete" not in entry and "tasks_incomplete" not in entry
        assert "completion_rate" not in d["tasks"] and "checkboxes" not in d
        assert "checkboxes" not in d["entries"][0]

    def test_each_day_counts_the_tasks_of_its_page(self, tmp_path):
        pages = [{"originalName": "J1", "journalDay": journal_day(1), "journal?": True},
                 {"originalName": "J2", "journalDay": journal_day(2), "journal?": True}]
        rows = ([[1, "TODO", journal_day(1)], [2, "DONE", journal_day(1)],
                 [3, "TODO", journal_day(2)], [3, "TODO", journal_day(2)]])
        d = payload(run("- x", tmp_path=tmp_path, rows=rows, pages=pages))
        by_page = {e["page"]: e["tasks"] for e in d["entries"]}
        assert by_page["J1"] == {"open": 1, "done": 1, "cancelled": 0}
        assert by_page["J2"] == {"open": 1, "done": 0, "cancelled": 0}   # the same block twice
        assert d["tasks"]["open"] == 2 and d["tasks"]["done"] == 1

    def test_the_query_names_exactly_the_eleven_markers_and_the_range(self, tmp_path):
        result = run("- x", tmp_path=tmp_path)
        assert len(result.api.datascript_query.call_args_list) == 1
        query = result.api.datascript_query.call_args.args[0]
        assert ":block/marker" in query and ":block/journal-day" in query
        asked = set(re.findall(r'"([A-Z-]+)"', re.search(r"#\{([^}]*)\}", query).group(1)))
        assert asked == {"NOW", "LATER", "TODO", "DOING", "IN-PROGRESS", "WAIT", "WAITING",
                         "STARTED", "DONE", "CANCELED", "CANCELLED"}
        start, end = parse_date_range("last 30 days")
        assert f"(>= ?d {int(start.strftime('%Y%m%d'))})" in query
        assert f"(<= ?d {int(end.strftime('%Y%m%d'))})" in query

    def test_the_rate_uses_done_over_open_plus_done(self, tmp_path):
        d = payload(run("- x", tmp_path=tmp_path, rows=_rows("TODO", "TODO", "DONE")))
        assert d["tasks"]["done_rate"] == 33.3

    def test_the_text_line(self, tmp_path):
        out = run("- x", tmp_path=tmp_path, as_json=False,
                  rows=_rows("TODO", "DOING", "DONE", "CANCELED")).stdout
        assert "Tasks: 2 open, 1 done, 1 cancelled (33.3% done)" in out

    def test_a_row_with_fewer_than_three_fields_is_skipped(self, tmp_path):
        d = payload(run("- x", tmp_path=tmp_path, rows=[[1, "TODO"], [2, "TODO", DAY]]))
        assert d["tasks"]["open"] == 1

    def test_a_marker_outside_state_is_skipped(self, tmp_path):
        d = payload(run("- x", tmp_path=tmp_path, rows=[[1, "FOO", DAY], [2, "TODO", DAY]]))
        assert (d["tasks"]["open"], d["tasks"]["done"], d["tasks"]["cancelled"]) == (1, 0, 0)

    def test_a_query_answer_of_none_counts_zero(self, tmp_path):
        d = payload(run("- x", tmp_path=tmp_path, rows=None))
        assert d["tasks"] == {"open": 0, "done": 0, "cancelled": 0, "done_rate": 0}


class TestMoodWordsComeFromConfig:
    def test_english_defaults_find_nothing_in_a_spanish_journal(self, tmp_path):
        """The state before [analysis] existed — and why it was worth adding."""
        d = payload(run("- hoy fue muy bueno y productivo", tmp_path=tmp_path))
        assert d["mood_summary"]["positive_signals"] == 0

    def test_configured_words_classify_a_stated_mood(self, tmp_path):
        """The words no longer scan the journal; they judge what it states.

        Free word counting was dropped because it read negations backwards —
        see TestMoodCountsStatementsNotWords in
        tests/test_read_only_commands_smoke.py.
        """
        d = payload(run("- vibe: upbeat",
                        config=('[analysis]\nmood_positive = ["upbeat"]\n'
                                'mood_labels = ["vibe"]\n'),
                        tmp_path=tmp_path))
        assert d["mood_summary"]["positive_signals"] == 1

    def test_a_positive_word_in_prose_is_not_a_signal(self, tmp_path):
        d = payload(run("- the team was upbeat after the build",
                        config='[analysis]\nmood_positive = ["upbeat"]\n',
                        tmp_path=tmp_path))
        assert d["mood_summary"]["positive_signals"] == 0

    def test_configured_words_replace_the_defaults_rather_than_adding(self, tmp_path):
        d = payload(run("- this was great and productive",
                        config='[analysis]\nmood_positive = ["upbeat"]\n',
                        tmp_path=tmp_path))
        assert d["mood_summary"]["positive_signals"] == 0


class TestProjectMarkers:
    def test_namespace_tag_and_link_both_count(self, tmp_path):
        cfg = '[analysis]\nproject_tag_prefix = "#projects/"\n'
        d = payload(run("- worked on #projects/alpha\n- also [[projects/beta]]",
                        config=cfg, tmp_path=tmp_path))
        assert "project_progress" in d
        assert set(d["project_progress"]) == {"alpha", "beta"}

    def test_flat_tags_are_configurable(self, tmp_path):
        cfg = ('[analysis]\nproject_tag_prefix = "#projects/"\n'
               'project_tags = ["alpha"]\n')
        d = payload(run("- shipped #alpha today", config=cfg, tmp_path=tmp_path))
        assert "alpha" in d.get("project_progress", {})


class TestItStillRunsWithoutAnything:
    def test_no_config_no_crash(self, tmp_path):
        d = payload(run("- a plain line", tmp_path=tmp_path))
        assert d["entries_analyzed"] == 1

    def test_flags_turn_sections_off(self, tmp_path):
        d = payload(run("- TODO x", "--no-mood", "--no-topics", tmp_path=tmp_path,
                        rows=[[1, "TODO", DAY]]))
        assert d["tasks"]["open"] == 1


class TestHabitTrackingIsUnchanged:
    """Habit tracking still counts the text pattern "- [ ]" / "- [x]"; it was
    never tested, so it is pinned before the task count moves to the database."""

    def test_habit_tracking_is_unchanged(self, tmp_path):
        d = payload(run("- [ ] walk\n- [x] read", tmp_path=tmp_path))
        assert d["habit_tracking"] == {
            "walk": {"total": 1, "done": 0, "completion_rate": 0.0,
                     "current_streak": 0, "longest_streak": 0},
            "read": {"total": 1, "done": 1, "completion_rate": 100.0,
                     "current_streak": 1, "longest_streak": 1}}

    def test_the_text_output_lists_the_habits(self, tmp_path):
        out = run("- [ ] walk\n- [x] read", tmp_path=tmp_path, as_json=False).stdout
        assert "Habit Tracking:" in out
        assert "Completion: 0.0% (0/1)" in out and "Completion: 100.0% (1/1)" in out
        assert "Current streak: 1 days" in out and "Longest streak: 1 days" in out


# The sentences stand here as literals of their own, not as imports of the
# constants: a test that imports them compares them with themselves.
DATING_SENTENCE = ("Tasks are dated by the page their block is on. "
                   "get-todos --from/--to also counts the days a block ref carried a task to.")
HABIT_SENTENCE = 'Habits are counted from the text pattern "- [ ]" / "- [x]", not from Logseq\'s checkboxes.'


def _help():
    out = split_runner().invoke(cli, ["analyze-journal-patterns", "--help"]).output
    return " ".join(out.split())


class TestTheOutputSaysWhatItCounts:
    """Tasks are dated by the page their block lies on, habits come from a text
    pattern: said in the help and in the text output, not in the JSON."""

    def test_help_says_how_tasks_are_dated(self):
        assert DATING_SENTENCE in _help()

    def test_help_says_what_habits_count(self):
        assert HABIT_SENTENCE in _help()

    def test_text_output_says_how_tasks_are_dated(self, tmp_path):
        out = run("- x", tmp_path=tmp_path, as_json=False, rows=_rows("TODO")).stdout
        lines = out.splitlines()
        assert out.count(DATING_SENTENCE) == 1
        i = next(n for n, line in enumerate(lines) if line.startswith("Tasks:"))
        assert lines[i + 1] == DATING_SENTENCE

    def test_json_does_not_carry_the_sentences(self, tmp_path):
        result = run("- [ ] walk", tmp_path=tmp_path, rows=_rows("TODO"))
        assert DATING_SENTENCE not in result.stdout and HABIT_SENTENCE not in result.stdout
        assert "dated_by" not in payload(result)["tasks"]

    def test_text_output_says_what_habits_count(self, tmp_path):
        out = run("- [ ] walk", tmp_path=tmp_path, as_json=False).stdout
        lines = out.splitlines()
        assert out.count(HABIT_SENTENCE) == 1
        i = lines.index("Habit Tracking:")
        assert lines[i + 1].strip() == HABIT_SENTENCE
        assert "Completion: 0.0% (0/1)" in out
        plain = run("- plain line", tmp_path=tmp_path, as_json=False).stdout
        assert "Habit Tracking:" not in plain and HABIT_SENTENCE not in plain

    def test_json_habit_keys_are_unchanged(self, tmp_path):
        d = payload(run("- [ ] walk", tmp_path=tmp_path))
        assert set(d["habit_tracking"]["walk"]) == {
            "total", "done", "completion_rate", "current_streak", "longest_streak"}
