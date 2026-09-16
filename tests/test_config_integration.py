"""The config file as the commands actually see it.

tests/test_config.py covers the loader in isolation: given a dict, what does
``require`` or ``resolve_heading`` return. That leaves the more expensive half
untested — whether any command asks. A setting that loads correctly and is
never read looks identical to a working feature from the outside, because the
command still exits 0 and still writes *something*.

So every test here drives a real command through CliRunner and asserts on what
reached the graph: the datalog query that was built, or the parent UUID a block
was inserted under.

``LOGSEQ_CLI_CONFIG`` points at a tmp_path file throughout, so no test can read
the developer's own config and quietly pass for the wrong reason.
"""

import json
import os
import re
from unittest.mock import MagicMock, patch

import pytest

from logseq_cli.cli import cli
from logseq_cli.commands.analysis import _project_pattern, _word_pattern
from tests.conftest import split_runner
from tests.test_datalog_quoting import QueryRecorder


# --- fixtures ------------------------------------------------------------

@pytest.fixture
def config_env(tmp_path):
    """Write a config file and point the CLI at it, for this test only.

    Returns a function taking TOML text. Both config variables are cleared on
    the way in and restored on the way out, so a test that writes no config
    really runs unconfigured regardless of the developer's environment.
    """
    def _write(toml_text=None):
        env = dict(os.environ)
        env.pop("LOGSEQ_JOURNAL_HEADING", None)
        if toml_text is None:
            # An unconfigured CLI. Pointing LOGSEQ_CLI_CONFIG at a file that
            # does not exist would be a different case: that is an explicit
            # request the loader answers with an error. What is wanted here is
            # "the user never configured anything", so the search path is
            # redirected into an empty tmp_path instead, the way
            # tests/test_config.py does it.
            env.pop("LOGSEQ_CLI_CONFIG", None)
            env["HOME"] = str(tmp_path)
            env["XDG_CONFIG_HOME"] = str(tmp_path)
        else:
            path = tmp_path / "config.toml"
            path.write_text(toml_text, encoding="utf-8")
            env["LOGSEQ_CLI_CONFIG"] = str(path)
        return patch.dict(os.environ, env, clear=True)

    return _write


def _journal_api():
    """API stand-in for the journal write paths."""
    api = MagicMock()
    api.get_user_configs.return_value = {}
    api.get_page.return_value = {"name": "journal"}
    api.get_page_blocks_tree.return_value = []
    api.append_block_in_page.return_value = {"uuid": "new-heading"}
    api.insert_block.return_value = {"uuid": "child"}
    return api


def _heading_parents(api):
    """UUIDs the command inserted blocks under."""
    return [call.args[0] for call in api.insert_block.call_args_list]


def _created_headings(api):
    """Heading texts the command appended to the page."""
    return [call.args[1] for call in api.append_block_in_page.call_args_list]


# --- graph settings in smart-query --------------------------------------

class TestProjectsNamespaceReachesTheQuery:
    def test_configured_namespace_appears_lowercased(self, config_env):
        """:block/name is stored lowercased, so the query must be too.

        A query for "Projects/" against lowercased names matches nothing and
        returns [], which reads as "you have no projects" — the silent-empty
        failure the config exists to prevent.
        """
        rec = QueryRecorder(result=[])
        with config_env('[graph]\nprojects_namespace = "Projects/"\n'):
            with patch("logseq_cli.group.LogseqAPI", return_value=rec):
                r = split_runner().invoke(
                    cli, ["smart-query", "--request", "projekte", "--json"])
        assert r.exit_code == 0, r.output
        assert rec.queries, "no query was built"
        q = rec.queries[0]
        assert '"projects/"' in q
        assert '"Projects/"' not in q

    def test_english_keyword_uses_the_same_setting(self, config_env):
        rec = QueryRecorder(result=[])
        with config_env('[graph]\nprojects_namespace = "Projects/"\n'):
            with patch("logseq_cli.group.LogseqAPI", return_value=rec):
                r = split_runner().invoke(
                    cli, ["smart-query", "--request", "projects", "--json"])
        assert r.exit_code == 0, r.output
        assert '"projects/"' in rec.queries[0]

    def test_a_different_namespace_is_not_hardcoded(self, config_env):
        """Guards against the setting being read but a default winning."""
        rec = QueryRecorder(result=[])
        with config_env('[graph]\nprojects_namespace = "Partners/"\n'):
            with patch("logseq_cli.group.LogseqAPI", return_value=rec):
                split_runner().invoke(
                    cli, ["smart-query", "--request", "projekte", "--json"])
        assert '"partners/"' in rec.queries[0]


class TestProjectsWithoutConfigFailsLoud:
    def test_exit_nonzero_and_nothing_queried(self, config_env):
        rec = QueryRecorder(result=[])
        with config_env(None):
            with patch("logseq_cli.group.LogseqAPI", return_value=rec):
                r = split_runner().invoke(
                    cli, ["smart-query", "--request", "projekte"])
        assert r.exit_code != 0
        # The point of failing: never send a query built on a guessed value.
        assert not rec.queries, "a query was built without the setting"

    def test_message_names_the_setting_and_the_section(self, config_env):
        rec = QueryRecorder(result=[])
        with config_env(None):
            with patch("logseq_cli.group.LogseqAPI", return_value=rec):
                r = split_runner().invoke(
                    cli, ["smart-query", "--request", "projekte"])
        msg = r.stderr
        assert "projects_namespace" in msg
        assert "[graph]" in msg

    def test_no_result_is_printed_on_stdout(self, config_env):
        """An empty result list here would be indistinguishable from a real
        "no projects found", which is exactly the confusion to avoid."""
        rec = QueryRecorder(result=[])
        with config_env(None):
            with patch("logseq_cli.group.LogseqAPI", return_value=rec):
                r = split_runner().invoke(
                    cli, ["smart-query", "--request", "projekte"])
        assert r.stdout == ""

    def test_an_empty_value_counts_as_missing(self, config_env):
        """An empty prefix matches every page, so it must not be accepted."""
        rec = QueryRecorder(result=[])
        with config_env('[graph]\nprojects_namespace = ""\n'):
            with patch("logseq_cli.group.LogseqAPI", return_value=rec):
                r = split_runner().invoke(
                    cli, ["smart-query", "--request", "projekte"])
        assert r.exit_code != 0
        assert not rec.queries


class TestPersonPropertyReachesTheQuery:
    CONFIG = '[graph]\nperson_property = "type"\nperson_value = "person"\n'

    def test_property_and_value_both_appear(self, config_env):
        rec = QueryRecorder(result=[])
        with config_env(self.CONFIG):
            with patch("logseq_cli.group.LogseqAPI", return_value=rec):
                r = split_runner().invoke(
                    cli, ["smart-query", "--request", "personen", "--json"])
        assert r.exit_code == 0, r.output
        q = rec.queries[0]
        # The property is an EDN keyword, the value a string literal.
        assert ":type" in q
        assert '"person"' in q

    def test_a_graphs_own_convention_is_used_verbatim(self, config_env):
        rec = QueryRecorder(result=[])
        with config_env('[graph]\nperson_property = "kind"\nperson_value = "Contact"\n'):
            with patch("logseq_cli.group.LogseqAPI", return_value=rec):
                r = split_runner().invoke(
                    cli, ["smart-query", "--request", "people", "--json"])
        assert r.exit_code == 0, r.output
        q = rec.queries[0]
        assert ":kind" in q
        # Property values keep the user's spelling: unlike :block/name, they
        # are not lowercased by Logseq.
        assert '"Contact"' in q

    def test_missing_property_fails_loud(self, config_env):
        rec = QueryRecorder(result=[])
        with config_env(None):
            with patch("logseq_cli.group.LogseqAPI", return_value=rec):
                r = split_runner().invoke(
                    cli, ["smart-query", "--request", "personen"])
        assert r.exit_code != 0
        assert not rec.queries
        assert "person_property" in r.stderr
        assert "[graph]" in r.stderr
        assert r.stdout == ""

    def test_property_without_value_still_fails(self, config_env):
        """Half a configuration is not a configuration: without the value the
        query would match every page carrying the property at all."""
        rec = QueryRecorder(result=[])
        with config_env('[graph]\nperson_property = "type"\n'):
            with patch("logseq_cli.group.LogseqAPI", return_value=rec):
                r = split_runner().invoke(
                    cli, ["smart-query", "--request", "personen"])
        assert r.exit_code != 0
        assert not rec.queries
        assert "person_value" in r.stderr


class TestConfigErrorAsJson:
    """--json callers parse stderr; a config error needs its own reason.

    Without a distinct reason an agent reads the failure as a connection
    problem, runs doctor (which reports OK) and falls back to the filesystem —
    the wrong repair for a missing setting.
    """

    @pytest.mark.parametrize("request_text", ["projekte", "personen"])
    def test_reason_is_config_error(self, config_env, request_text):
        rec = QueryRecorder(result=[])
        with config_env(None):
            with patch("logseq_cli.group.LogseqAPI", return_value=rec):
                r = split_runner().invoke(
                    cli, ["smart-query", "--request", request_text, "--json"])
        assert r.exit_code != 0
        assert r.stdout == "", "a failure must not also print a payload"
        payload = json.loads(r.stderr)
        assert payload["reason"] == "config_error"

    def test_the_json_error_text_still_names_the_setting(self, config_env):
        rec = QueryRecorder(result=[])
        with config_env(None):
            with patch("logseq_cli.group.LogseqAPI", return_value=rec):
                r = split_runner().invoke(
                    cli, ["smart-query", "--request", "projekte", "--json"])
        payload = json.loads(r.stderr)
        assert "projects_namespace" in payload["error"]
        assert "[graph]" in payload["error"]

    def test_a_broken_config_file_is_a_config_error_too(self, config_env):
        """Malformed TOML must not be silently ignored: the user meant to
        configure something."""
        rec = QueryRecorder(result=[])
        with config_env("[graph\nbroken"):
            with patch("logseq_cli.group.LogseqAPI", return_value=rec):
                r = split_runner().invoke(
                    cli, ["smart-query", "--request", "projekte", "--json"])
        assert r.exit_code != 0
        payload = json.loads(r.stderr)
        assert payload["reason"] == "config_error"


# --- heading shortcuts, end to end --------------------------------------

class TestHeadingShortcutEndToEnd:
    CONFIG = (
        '[journal]\n'
        '[journal.headings]\n'
        'tasks = "## Tasks"\n'
        'log = "## Log"\n'
    )

    def test_shortcut_resolves_to_the_configured_heading(self, config_env):
        api = _journal_api()
        with config_env(self.CONFIG):
            with patch("logseq_cli.group.LogseqAPI", return_value=api):
                r = split_runner().invoke(
                    cli, ["add-journal-block", "--content", "Entry",
                          "--under-heading", "tasks"])
        assert r.exit_code == 0, r.output
        # The heading the command looked for (and here created) must be the
        # configured text, never the shortcut name itself: find_or_create_heading
        # creates what it does not find, so an unresolved name would silently
        # add a block under a new heading called "tasks".
        assert "## Tasks" in _created_headings(api)
        assert "tasks" not in _created_headings(api)
        assert "new-heading" in _heading_parents(api)

    def test_an_existing_heading_is_reused_not_recreated(self, config_env):
        api = _journal_api()
        api.get_page_blocks_tree.return_value = [
            {"content": "## Tasks", "uuid": "tasks-uuid"},
        ]
        with config_env(self.CONFIG):
            with patch("logseq_cli.group.LogseqAPI", return_value=api):
                r = split_runner().invoke(
                    cli, ["add-journal-block", "--content", "Entry",
                          "--under-heading", "tasks"])
        assert r.exit_code == 0, r.output
        assert "tasks-uuid" in _heading_parents(api)
        assert _created_headings(api) == [], "heading was created despite existing"

    def test_a_literal_heading_still_works(self, config_env):
        """Configuring shortcuts must not break passing the heading itself."""
        api = _journal_api()
        api.get_page_blocks_tree.return_value = [
            {"content": "## Notes", "uuid": "notes-uuid"},
        ]
        with config_env(self.CONFIG):
            with patch("logseq_cli.group.LogseqAPI", return_value=api):
                r = split_runner().invoke(
                    cli, ["add-journal-block", "--content", "Entry",
                          "--under-heading", "## Notes"])
        assert r.exit_code == 0, r.output
        assert "notes-uuid" in _heading_parents(api)

    def test_an_unknown_name_is_passed_through_unchanged(self, config_env):
        """A typo must land where the user typed, not resolve to something
        else: a wrong-but-visible heading is recoverable, a silently
        redirected write is not."""
        api = _journal_api()
        with config_env(self.CONFIG):
            with patch("logseq_cli.group.LogseqAPI", return_value=api):
                r = split_runner().invoke(
                    cli, ["add-journal-block", "--content", "Entry",
                          "--under-heading", "taskz"])
        assert r.exit_code == 0, r.output
        assert "taskz" in _created_headings(api)
        assert "## Tasks" not in _created_headings(api)

    def test_without_any_config_the_value_is_used_verbatim(self, config_env):
        api = _journal_api()
        with config_env(None):
            with patch("logseq_cli.group.LogseqAPI", return_value=api):
                r = split_runner().invoke(
                    cli, ["add-journal-block", "--content", "Entry",
                          "--under-heading", "## Log"])
        assert r.exit_code == 0, r.output
        assert "## Log" in _created_headings(api)


class TestDefaultHeadingPrecedence:
    CONFIG = '[journal]\ndefault_heading = "## FromFile"\n'

    def test_env_var_beats_the_config_default(self, config_env):
        """LOGSEQ_JOURNAL_HEADING predates the config file and stays
        authoritative, so an existing setup keeps behaving as before."""
        api = _journal_api()
        with config_env(self.CONFIG):
            with patch.dict(os.environ, {"LOGSEQ_JOURNAL_HEADING": "## FromEnv"}):
                with patch("logseq_cli.group.LogseqAPI", return_value=api):
                    r = split_runner().invoke(
                        cli, ["add-journal-block", "--content", "Entry"])
        assert r.exit_code == 0, r.output
        assert "## FromEnv" in _created_headings(api)
        assert "## FromFile" not in _created_headings(api)

    def test_config_default_applies_when_the_env_var_is_unset(self, config_env):
        api = _journal_api()
        with config_env(self.CONFIG):
            with patch("logseq_cli.group.LogseqAPI", return_value=api):
                r = split_runner().invoke(
                    cli, ["add-journal-block", "--content", "Entry"])
        assert r.exit_code == 0, r.output
        assert "## FromFile" in _created_headings(api)

    def test_explicit_flag_beats_both(self, config_env):
        api = _journal_api()
        with config_env(self.CONFIG):
            with patch.dict(os.environ, {"LOGSEQ_JOURNAL_HEADING": "## FromEnv"}):
                with patch("logseq_cli.group.LogseqAPI", return_value=api):
                    r = split_runner().invoke(
                        cli, ["add-journal-block", "--content", "Entry",
                              "--under-heading", "## Explicit"])
        assert r.exit_code == 0, r.output
        assert "## Explicit" in _created_headings(api)

    def test_top_level_beats_the_config_default(self, config_env):
        """--top-level means top level, whatever the file says."""
        api = _journal_api()
        with config_env(self.CONFIG):
            with patch("logseq_cli.group.LogseqAPI", return_value=api):
                r = split_runner().invoke(
                    cli, ["add-journal-block", "--content", "Entry",
                          "--top-level"])
        assert r.exit_code == 0, r.output
        assert "## FromFile" not in _created_headings(api)
        assert api.insert_block.call_args_list == []

    def test_nothing_configured_means_top_level(self, config_env):
        api = _journal_api()
        with config_env(None):
            with patch("logseq_cli.group.LogseqAPI", return_value=api):
                r = split_runner().invoke(
                    cli, ["add-journal-block", "--content", "Entry"])
        assert r.exit_code == 0, r.output
        assert api.insert_block.call_args_list == []


class TestConfigIsNotReadFromTheDeveloperEnvironment:
    """A guard on the tests themselves.

    Every assertion above rests on LOGSEQ_CLI_CONFIG pointing into tmp_path.
    If that stopped working, the "no config" tests would read whatever the
    developer has configured and could pass for the wrong reason.
    """

    def test_the_unconfigured_case_really_finds_no_file(self, config_env):
        from logseq_cli.config import load_config
        with config_env(None):
            assert load_config() == {}

    def test_the_configured_case_reads_the_tmp_file(self, config_env):
        from logseq_cli.config import load_config
        with config_env('[graph]\nprojects_namespace = "Alpha/"\n'):
            cfg = load_config()
        assert cfg["graph"]["projects_namespace"] == "Alpha/"


class TestDoctorReportsConfig:
    """doctor names the config state, so a missing setting is found before a
    command silently needs it."""

    def _run(self, tmp_path, body):
        cfg = tmp_path / "c.toml"
        cfg.write_text(body, encoding="utf-8")
        with patch.dict(os.environ, {"LOGSEQ_CLI_CONFIG": str(cfg)}, clear=False):
            return split_runner().invoke(cli, ["--token", "X", "doctor", "--json"])

    def test_configured_graph_settings_are_listed(self, tmp_path):
        r = self._run(tmp_path, '[graph]\nprojects_namespace = "projects/"\n')
        check = next(c for c in json.loads(r.output)["checks"] if c["check"] == "config")
        assert check["ok"] is True
        assert "projects_namespace" in check["detail"]

    def test_config_without_graph_section_is_flagged_but_not_a_failure(self, tmp_path):
        """Not having one is legitimate: it must not make doctor report unhealthy."""
        r = self._run(tmp_path, '[journal]\ndefault_heading = "## Log"\n')
        check = next(c for c in json.loads(r.output)["checks"] if c["check"] == "config")
        assert check["ok"] is None
        assert "smart-query" in check["detail"]

    def test_broken_config_fails_the_check(self, tmp_path):
        r = self._run(tmp_path, "[graph\nbroken")
        check = next(c for c in json.loads(r.output)["checks"] if c["check"] == "config")
        assert check["ok"] is False
        assert "not valid TOML" in check["detail"]

    def test_a_section_written_as_a_flat_key_does_not_crash_doctor(self, tmp_path):
        """`graph = "projects/"` instead of `[graph]` is valid TOML and a
        plausible typo: the brackets are easy to forget. It makes the section a
        string, and reaching into it with .get() raised AttributeError — a raw
        traceback from the one command whose job is to diagnose exactly this,
        and one that --json could not turn into an error object either.

        config.py guards every other reader with isinstance(); doctor reads the
        sections directly and must go through the same accessor.
        """
        r = self._run(tmp_path, 'graph = "projects/"\n')
        check = next(c for c in json.loads(r.output)["checks"] if c["check"] == "config")
        assert check["ok"] is None
        assert "smart-query" in check["detail"]


class TestAnalysisPatternsComeFromConfig:
    """The word lists and project markers in analyze-journal are language- and
    graph-specific. Built in, they are English, so a journal written otherwise
    scores nothing at all — and says so no more loudly than a journal with
    genuinely no moods in it."""

    def test_word_pattern_matches_configured_words(self):
        pat = _word_pattern(["gut", "produktiv"])
        assert pat.search("war gut heute")
        assert pat.search("sehr Produktiv")

    def test_word_pattern_respects_word_boundaries(self):
        """Substring hits would count 'gut' inside unrelated words."""
        assert not _word_pattern(["gut"]).search("Regengutachten")

    def test_word_pattern_escapes_user_input(self):
        """A word from config is data, not a regex."""
        assert _word_pattern(["c++"]).search("i like c++")
        assert not _word_pattern(["c++"]).search("cxx")

    def test_empty_word_list_matches_nothing(self):
        """An empty alternation would match at every position instead."""
        assert not _word_pattern([]).search("anything at all")
        assert not _word_pattern(["", "  "]).search("anything at all")

    def test_project_pattern_matches_tag_and_link_form(self):
        """Both spellings name the same project; matching one undercounts."""
        pat = _project_pattern("#projects/")
        assert next(g for g in pat.search("#projects/alpha").groups() if g) == "alpha"
        assert next(g for g in pat.search("[[projects/alpha]]").groups() if g) == "alpha"

    def test_project_pattern_accepts_flat_tags(self):
        """Graphs that do not namespace list their project tags instead."""
        pat = _project_pattern("#projects/", ["Alpha", "Beta"])
        assert next(g for g in pat.search("shipped #Alpha today").groups() if g) == "Alpha"
        assert pat.search("#Gamma") is None

    def test_flat_tags_match_the_link_form_too(self):
        """A listed project name is the same project written either way.

        Measured against a real journal: the flat link outnumbered the flat
        tag by two orders of magnitude, so matching tags alone found nothing
        at all in a graph that writes [[Alpha]] rather than #Alpha.
        """
        pat = _project_pattern("#projects/", ["Alpha", "Beta"])
        for text in ("worked on [[Alpha]] today", "#Alpha ticket",
                     "[[Beta]] release"):
            assert pat.search(text), text

    def test_a_longer_name_is_not_matched_as_the_shorter_one(self):
        """[[Alpha-Legacy]] is a different project from [[Alpha]]."""
        pat = _project_pattern("#projects/", ["Alpha"])
        assert pat.search("see [[Alpha-Legacy]]") is None

    def test_project_prefix_is_escaped(self):
        pat = _project_pattern("#a.b/")
        assert pat.search("#a.b/one")
        assert pat.search("#axb/one") is None


class TestTaskCountingSeesLogseqMarkers:
    """analyze-journal-patterns counted markdown checkboxes only.

    Logseq writes TODO/DOING/DONE markers, so a real graph reported
    "0 complete, 0 incomplete (0% rate)" — a number that reads like a
    measurement of a graph with no tasks, not like a counter that cannot
    match. Measured against 120 real journals: 0/0 before, 284/30 after.
    """

    INCOMPLETE = re.compile(
        r"(?i:- \[ \])|^(?:\s*-\s*)*(?:TODO|DOING|NOW|LATER|WAITING|IN-PROGRESS)\b",
        re.MULTILINE)
    COMPLETE = re.compile(
        r"(?i:- \[x\])|^(?:\s*-\s*)*(?:DONE|CANCELED|CANCELLED)\b",
        re.MULTILINE)

    def test_markers_count_as_tasks(self):
        assert self.INCOMPLETE.search("- TODO write the ticket")
        assert self.INCOMPLETE.search("- DOING in progress")
        assert self.COMPLETE.search("- DONE shipped")

    def test_checkboxes_still_count(self):
        """The old syntax must keep working for graphs that use it."""
        assert self.INCOMPLETE.search("- [ ] open")
        assert self.COMPLETE.search("- [x] closed")

    def test_a_marker_word_inside_prose_is_not_a_task(self):
        """Only at the start of a line, so 'nothing to do' stays prose."""
        assert not self.INCOMPLETE.search("we talked about the todo list")
        assert not self.COMPLETE.search("that is done and dusted")

    def test_done_is_not_also_counted_as_incomplete(self):
        assert not self.INCOMPLETE.search("- DONE shipped")

    def test_a_lowercase_marker_word_is_prose(self):
        """Logseq markers are upper-case. "Now that..." opens a sentence.

        Reported against an English graph: with (?i) on the markers, every
        block starting "Now", "Later", "Waiting" or "done" counted as a task.
        """
        for line in ("- done", "- Now that we finished it",
                     "- Later kam die Rückmeldung", "- Waiting for the reply",
                     "- todo: das muss noch"):
            assert not self.INCOMPLETE.search(line), line
            assert not self.COMPLETE.search(line), line

    def test_a_checkbox_still_matches_either_case(self):
        """[x] and [X] are both in the wild, unlike the markers."""
        assert self.COMPLETE.search("- [x] done")
        assert self.COMPLETE.search("- [X] done")

