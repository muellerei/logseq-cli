"""analyze-journal-patterns — the command whose patterns are now configurable.

Two of today's changes land here: the mood word lists and project markers come
from [analysis], and task counting learned Logseq's markers. Neither had a
test, and both fail the same quiet way — an empty result looks exactly like a
journal with nothing in it.
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from logseq_cli.cli import cli
from tests.conftest import split_runner

JOURNAL = {"originalName": "2026-09-10", "journalDay": 20260910, "journal?": True}


def run(text, *args, config=None, tmp_path=None):
    api = MagicMock()
    api.get_all_pages.return_value = [JOURNAL]
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
        return split_runner().invoke(
            cli, ["--token", "X", "analyze-journal-patterns",
                  "--timeframe", "last 30 days", "--json", *args])


def payload(result):
    assert result.exit_code == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


class TestTaskCounting:
    def test_logseq_markers_are_counted(self, tmp_path):
        d = payload(run("- TODO write it\n- DOING working\n- DONE shipped", tmp_path=tmp_path))
        assert d["tasks"]["total_incomplete"] == 2
        assert d["tasks"]["total_complete"] == 1

    def test_checkboxes_are_counted_too(self, tmp_path):
        d = payload(run("- [ ] open\n- [x] closed\n- [X] also closed", tmp_path=tmp_path))
        assert d["tasks"]["total_incomplete"] == 1
        assert d["tasks"]["total_complete"] == 2

    def test_marker_words_in_prose_are_not_tasks(self, tmp_path):
        """The reason markers match case-sensitively and only at line start."""
        d = payload(run("- Now that we finished it\n- that is done\n- todo: later", tmp_path=tmp_path))
        assert d["tasks"]["total_incomplete"] == 0
        assert d["tasks"]["total_complete"] == 0

    def test_a_doubled_bullet_still_counts(self, tmp_path):
        """get_page_content prefixes every block with "- ", so a block that
        already starts with one reaches the pattern as "- - TODO ...".
        """
        d = payload(run("- - TODO nested bullet\n- - DONE also nested",
                        tmp_path=tmp_path))
        assert d["tasks"]["total_incomplete"] == 1
        assert d["tasks"]["total_complete"] == 1

    def test_an_empty_journal_reports_zero_without_dividing_by_zero(self, tmp_path):
        d = payload(run("- just some prose", tmp_path=tmp_path))
        assert d["tasks"] == {"total_complete": 0, "total_incomplete": 0,
                              "completion_rate": 0}


class TestMoodWordsComeFromConfig:
    def test_english_defaults_find_nothing_in_a_german_journal(self, tmp_path):
        """The state before [analysis] existed — and why it was worth adding."""
        d = payload(run("- war heute richtig gut und produktiv", tmp_path=tmp_path))
        assert d["mood_summary"]["positive_signals"] == 0

    def test_configured_words_classify_a_stated_mood(self, tmp_path):
        """The words no longer scan the journal; they judge what it states.

        Free word counting was dropped because it read negations backwards —
        see TestMoodCountsStatementsNotWords in
        tests/test_read_only_commands_smoke.py.
        """
        d = payload(run("- stimmung: gut",
                        config=('[analysis]\nmood_positive = ["gut"]\n'
                                'mood_labels = ["stimmung"]\n'),
                        tmp_path=tmp_path))
        assert d["mood_summary"]["positive_signals"] == 1

    def test_a_positive_word_in_prose_is_not_a_signal(self, tmp_path):
        d = payload(run("- der Build lief gut durch",
                        config='[analysis]\nmood_positive = ["gut"]\n',
                        tmp_path=tmp_path))
        assert d["mood_summary"]["positive_signals"] == 0

    def test_configured_words_replace_the_defaults_rather_than_adding(self, tmp_path):
        d = payload(run("- this was great and productive",
                        config='[analysis]\nmood_positive = ["gut"]\n',
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
        d = payload(run("- TODO x", "--no-mood", "--no-topics", tmp_path=tmp_path))
        assert d["tasks"]["total_incomplete"] == 1
